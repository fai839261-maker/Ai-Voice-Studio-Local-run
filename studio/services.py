"""Installer, adapter discovery and generation orchestration.
All heavyweight work runs in a GUI background thread, never in the Qt event loop.
"""
from __future__ import annotations
from . import external
import json
import queue
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import managed
from .core import bundled, is_audio, join_wave, models, root, safe_chunks, sha256, subdir

# Engine packages are isolated from a user's system site-packages.
# A single shared --target folder prevents separate copies of PyTorch per engine.
TARGET = subdir("py") / "shared"


def find_python() -> str | None:
    """Reject the Windows Store alias and verify 64-bit Python 3.11/3.12 can execute."""
    candidates = []
    if os.name == "nt":
        candidates += [["py", "-3.11"], ["py", "-3.12"]]
    for name in ["python3.11", "python3.12", "python", "python3"]:
        path = shutil.which(name)
        if path and "WindowsApps" not in path:
            candidates.append([path])
    if os.name == "nt":
        for base in [Path.home()/"AppData"/"Local"/"Programs"/"Python", Path("C:/Python311"), Path("C:/Python312")]:
            if base.is_dir():
                for f in (base.glob("Python3*/python.exe") if "Programs" in str(base) else [base/"python.exe"]):
                    candidates.append([str(f)])
    for command in candidates:
        try:
            p = external.run(command+["-c", "import sys,struct;print('%d.%d %d'%(sys.version_info.major,sys.version_info.minor,struct.calcsize('P')*8))"],capture_output=True,text=True,timeout=8,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if p.returncode == 0 and p.stdout.strip() in {"3.11 64","3.12 64"}:
                return json.dumps(command)  # preserve launcher prefix e.g. py -3.11
        except (OSError,subprocess.SubprocessError):
            pass
    return None


def python_command():
    found = find_python()
    if not found:
        raise RuntimeError("Python 3.11/3.12 64-bit is missing. Install via AI Setup > Install Python. Windows Store alias is not supported.")
    return json.loads(found)


class JobStopped(Exception):
    """The person pressed Stop. Not an error: the job is paused and keeps what is finished."""


_ABORT=set();_PROCS={};_CTX=threading.local();_PROC_LOCK=threading.Lock()
PART_RUN={};PART_STATS={};JOB_START={}      # parts being made now, finished-part speed, when each job started (for the moving progress bar)

def part_fraction(job_id):
    """How far the parts that are being made right now have got (0 to 0.95 each), estimated from the speed of the parts already finished."""
    now=time.monotonic();chars,secs=PART_STATS.get(job_id,(0,0.0))
    rate=chars/secs if secs>3 and chars>0 else 10.0      # letters per second; a first guess until one part is finished
    total=0.0
    for (j,_idx),(t0,n) in list(PART_RUN.items()):
        if j==job_id:total+=min(0.95,(now-t0)/max(5.0,n/rate))
    return total

def job_elapsed(job_id):
    t0=JOB_START.get(job_id);return (time.monotonic()-t0) if t0 else 0.0

def current_job():return getattr(_CTX,"job",None)

def stopped(job_id=None):
    job=job_id or current_job()
    return bool(job and job in _ABORT)

def stop_job(job_id):
    """End the voice engine right now, in the middle of a part. Finished parts are kept."""
    _ABORT.add(job_id)
    with _PROC_LOCK:procs=list(_PROCS.get(job_id,()))
    for p in procs:
        try:p.kill()
        except Exception:pass
    from . import warm_worker
    warm_worker.kill_now()


def python_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(TARGET) + os.pathsep + env.get("PYTHONPATH", "")
    env["HF_HOME"] = str(subdir("hf_cache"))
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["HF_HUB_OFFLINE"] = "1"  # Never permit surprise multi-GB downloads during synthesis
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def run_command(cmd, log=lambda msg:None, timeout=None, env=None):
    """Stream child output without letting silent/hung subprocesses avoid timeouts."""
    log("Running: " + " ".join(str(x) for x in cmd))
    flags = getattr(subprocess,"CREATE_NO_WINDOW",0)
    p = external.popen([str(x) for x in cmd],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                         text=True,bufsize=1,errors="replace",env=env,creationflags=flags,priority="work")
    job=current_job()
    if job:
        with _PROC_LOCK:_PROCS.setdefault(job,set()).add(p)
    output=queue.Queue()
    def consume():
        try:
            for line in p.stdout:
                output.put(line.rstrip()[:2000])
        finally:
            output.put(None)
    threading.Thread(target=consume,daemon=True).start()
    started=time.monotonic()
    tail=[]
    done=False
    try:
        while not done:
            if job and job in _ABORT:
                p.kill();raise JobStopped()
            if timeout and time.monotonic()-started>timeout:
                raise TimeoutError(f"Process exceeded {timeout} seconds and was terminated")
            try:
                line=output.get(timeout=0.4)
                if line is None:done=True
                elif line:
                    tail.append(line);tail=tail[-30:];log(line)
            except queue.Empty:
                if p.poll() is not None and output.empty():done=True
        rc=p.wait(timeout=10)
        if job and job in _ABORT:raise JobStopped()      # the process ended because Stop was pressed, not because it failed
        if rc:
            raise RuntimeError(f"Process exited with code {rc}.\n"+"\n".join(tail))
    finally:
        if job:
            with _PROC_LOCK:_PROCS.get(job,set()).discard(p)
        if p.poll() is None:
            p.kill()
            p.wait()


def module_present(name: str) -> bool:
    try:
        res = external.run(python_command()+["-c",f"import sys, importlib.util;sys.path.insert(0,{str(TARGET)!r});print(importlib.util.find_spec({name!r}) is not None)"],capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),env=python_env())
        return res.returncode == 0 and res.stdout.strip() == "True"
    except Exception:
        return False


def install_python(log=lambda msg:None):
    if find_python():
        log("Python is already installed and verified.")
        return
    if os.name != "nt":
        raise RuntimeError("Automated Python bootstrap is available only on Windows.")
    exe = shutil.which("winget")
    if not exe:
        raise RuntimeError("WinGet not found. Install App Installer from Microsoft, then retry. No unverified installer will be run.")
    run_command([exe,"install","--id","Python.Python.3.11","-e","--source","winget",
                 "--accept-package-agreements","--accept-source-agreements"],log,timeout=1800)
    if not find_python():
        raise RuntimeError("Python installer exited, but the new runtime could not yet be detected. Restart the app and scan again.")


def pip_install(packages: list[str], log=lambda msg:None, extra_index: str | None=None):
    TARGET.mkdir(parents=True,exist_ok=True)
    cmd=python_command()+["-m","pip","install","--disable-pip-version-check","--no-warn-script-location","--target",str(TARGET)]
    if extra_index:
        cmd += ["--index-url",extra_index]
    cmd += packages
    # Never use --upgrade by default: replacing shared Torch can break installed adapters.
    run_command(cmd,log,timeout=7200,env=python_env())


def ensure_base(mode, log=lambda msg:None):
    if mode == "CPU":
        pip_install(["torch", "torchaudio"], log, "https://download.pytorch.org/whl/cpu")
    elif mode == "CUDA 12.8":
        pip_install(["torch", "torchaudio"], log, "https://download.pytorch.org/whl/cu128")
    else:
        raise ValueError("Unknown base mode")
    pip_install(["transformers", "soundfile", "numpy", "huggingface-hub"],log)
    log("Base installed into the shared application folder. Each adapter still requires a real synthesis test.")


def install_engine(model: str, log=lambda msg:None):
    entry = models()[model]
    # Note: pip dependency conflicts can happen in one shared target. A failing test
    # must not lead to a 'Ready' status. Fixes may require model-specific environments.
    if model in ("chatterbox","f5") and not module_present("torch"):
        raise RuntimeError("Install Base AI Dependencies first (PyTorch).")
    pip_install([entry["package"]],log)


def voice_files(model, voice):
    cfg=models()[model]
    if model == "piper":
        entry=next((v for v in cfg["voices"] if v["id"]==voice),None)
        if entry is None:
            raise ValueError("Unknown Piper voice")
        pfx=f"https://huggingface.co/{entry['repo']}/resolve/main/{entry['prefix']}"
        return [{"name":voice+suffix,"url":pfx+voice+suffix} for suffix in (".onnx",".onnx.json")]
    if model == "kokoro":
        return cfg["files"]
    if model in ("chatterbox", "f5", "openf5"):
        return cfg.get("files", [])
    return []


def model_folder(model, voice=""):
    base=subdir("models")/model
    if model == "piper":
        base=base/voice
    base.mkdir(parents=True,exist_ok=True)
    return base


def download_size(url):
    return managed.downloads.head_size(url)

def download_plan(model, voice):
    plan=[]
    for file in voice_files(model,voice):
        target=model_folder(model,voice)/file["name"]
        if target.exists() and target.stat().st_size > 100:
            continue
        plan.append({**file, "size":download_size(file["url"]),"path":target})
    return plan


def download_one(url, dst: Path, log=lambda msg:None):
    dst.parent.mkdir(parents=True,exist_ok=True)
    from .downloads import download_set,head_size
    item={'name':dst.name,'url':url,'size':head_size(url)}
    download_set(dst.parent,[item],lambda d,t,s:log(f'{s}: {d/1e6:.1f} MB'),lambda:None,managed.valid_file,sha256,manifest=False)


def download_model(model, voice, log=lambda msg:None):
    plan=download_plan(model,voice)
    if not plan:
        log("All declared model files already exist (does not imply they work).")
    for item in plan:
        log("From: "+item["url"])
        download_one(item["url"],item["path"],log)


def asset_signature(model: str, voice: str="") -> str:
    import hashlib
    h=hashlib.sha256()
    from .core import APP_VERSION
    h.update(APP_VERSION.encode())
    folder=model_folder(model,voice)
    assets=list(folder.glob("**/*")) if model != "piper" else list(folder.glob("*"))
    for p in sorted(assets):
        if p.is_file():
            st=p.stat()
            h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    # Include installed dist metadata, so edits to packages invalidate proof.
    for item in sorted(list(managed.profile_dir(model).glob("Lib/site-packages/*.dist-info/METADATA"))+list(managed.profile_dir(model).glob("lib/python*/site-packages/*.dist-info/METADATA"))):
        h.update(f"{item.parent.name}:{item.stat().st_mtime_ns}".encode())
    return h.hexdigest()


def readiness(store, model, voice):
    if not managed.engine_python(model).is_file():return "Engine not installed"
    if not managed.downloaded(model,voice):return "Voice files missing"
    key=model+":"+voice
    record=store.verification(key)
    if record and is_audio(Path(record["testfile"])) and record["signature"]==asset_signature(model,voice):return "READY · test passed"
    return "Installed · test required"

def invoke_engine(model,voice,text,output,options=None,reference="",ref_text="",log=lambda msg:None):
    if stopped():raise JobStopped()
    if not managed.engine_python(model).is_file():
        raise RuntimeError("Engine is not installed. Use AI Setup.")
    if options is not None:
        from .capabilities import clamp_inplace
        clamp_inplace(model,options,bool(options.get("low_end")))
    output=Path(output); scratch=output.with_name(output.stem+".new.wav");scratch.unlink(missing_ok=True)
    run_options=slot_options(options,getattr(_CTX,"slot",0))
    payload={"model":model,"voice":voice,"text":text,"output":str(scratch),"options":run_options,
             "reference":reference,"ref_text":ref_text,"models_dir":str(model_folder(model,voice))}
    with tempfile.NamedTemporaryFile(mode="w",suffix=".json",prefix="avs_",delete=False,encoding="utf-8") as f:
        json.dump(payload,f)
        config=f.name
    try:
        cmd=[str(managed.engine_python(model)),str(bundled("runtime/runner.py")),"--payload",config]
        from .generation_policy import render_timeout
        timeout=render_timeout(model,text)
        def execute():
            if payload["options"].get("keep_model_warm"):
                from . import warm_worker
                try:warm_worker.run(cmd[:2]+["--worker"],payload,managed.env(),log,timeout=timeout,slot=getattr(_CTX,"slot",0))
                except warm_worker.WarmTransportError as exc:
                    if stopped():raise JobStopped()
                    log('Voice worker communication failed; retrying once in a fresh process: '+str(exc)[-300:])
                    scratch.unlink(missing_ok=True)
                    run_command(cmd,log,timeout=timeout,env=managed.env())
            else:run_command(cmd,log,timeout=timeout,env=managed.env())
        try:
            execute()
        except RuntimeError as exc:
            detail=str(exc).lower()
            card=payload['options'].get('device')
            if card=='mps':pass           # any failure on the Apple graphics is retried on the processor (some operations are missing in Metal)
            elif card!='cuda' or not any(x in detail for x in ('device-side assert','srcindex < srcselectdimsize','cuda out of memory','cuda error: out of memory')):raise
            log('GPU generation failed; retrying this section using a fresh CPU process. '+str(exc)[-500:])
            scratch.unlink(missing_ok=True);payload['options']={**payload['options'],'device':'cpu','cpu_threads':max(1,int((options or {}).get('cpu_threads',payload['options'].get('cpu_threads',1))))}
            Path(config).write_text(json.dumps(payload),encoding='utf-8')
            execute()
            if options is not None:options['device']='cpu';options['gpu_failed']=True # remaining sections avoid repeated CUDA crashes

        if not is_audio(scratch):
            raise RuntimeError("Engine did not produce a valid PCM WAV file. Status remains unverified.")
        os.replace(scratch,output)
    finally:
        Path(config).unlink(missing_ok=True)
        scratch.unlink(missing_ok=True)


def slot_options(options,slot):
    """Options for one helper. With 'Both together' (hybrid) the first helper runs on the graphics card and every other one on the
    processor, each with its own share of the processor threads, so the parts are shared between the two and whoever is free takes the next."""
    if options is None:return {}
    if not options.get("hybrid") or options.get("device")!="cuda" or options.get("gpu_failed"):return options
    from . import load_control
    gpu_threads,cpu_threads=load_control.hybrid_threads(options.get("cpu_threads",1))
    if slot==0:return {**options,"cpu_threads":gpu_threads}
    return {**options,"device":"cpu","cpu_threads":cpu_threads,"gpu_memory_fraction":1.0}


def schedule_parts(job_id,sections,n,work):
    """Run work(section, slot, failed) for every section. n>1 makes that many parts at the same time, each with its own loaded engine;
    when one finishes the next starts. Returns the outcomes in order. The first error is raised after running parts have finished."""
    import concurrent.futures
    failed=threading.Event();free=queue.Queue()
    for i in range(n):free.put(i)
    def run(section):
        if n==1:slot=0
        else:slot=free.get()
        if n>1:_CTX.job=job_id
        _CTX.slot=slot
        try:return work(section,slot,failed)
        finally:
            _CTX.slot=0
            if n>1:free.put(slot)
    if n==1:
        outcomes=[]
        for section in sections:
            outcome=run(section);outcomes.append(outcome)
            if outcome=="paused":break
        return outcomes
    error=None;results={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures={pool.submit(run,sec):i for i,sec in enumerate(sections)}
        for fut in concurrent.futures.as_completed(futures):
            try:results[futures[fut]]=fut.result()
            except BaseException as exc:
                failed.set();error=error or exc
    if error is not None:raise error
    return [results[i] for i in sorted(results)]

def _pieces(text,model,opts):
    pieces=safe_chunks([text],model,int(opts.get("chunk_chars",0) or 0))
    if len(pieces)<2:
        middle=len(text)//2;cut=max(text.rfind(". ",0,middle+40),text.rfind(", ",0,middle+40),text.rfind(" ",0,middle+40))
        pieces=[text[:cut+1].strip(),text[cut+1:].strip()] if cut>20 else [text]
    return [p for p in pieces if p]

def render_section(model,voice,text,path,opts,reference,ref_text,log,retries=1,auto=True,depth=0):
    """Make one part of a voice-over. Text longer than the model reads reliably is split automatically,
    and a failed part is retried (a too-long failure is retried as smaller pieces)."""
    from . import autofix
    from .capabilities import max_chars
    path=Path(path)
    def split_and_join():
        pieces=_pieces(text,model,opts)
        if len(pieces)<2:return False
        log(f"Splitting a {len(text)}-character part into {len(pieces)} smaller parts automatically.")
        files=[]
        try:
            for i,piece in enumerate(pieces):
                f=path.with_name(f"{path.stem}.p{i}.wav");files.append(f)
                render_section(model,voice,piece,f,opts,reference,ref_text,log,retries,auto,depth+1)
            join_wave(files,path,max(60,int(opts.get("pause_ms",220))//2))
        finally:
            for f in files:f.unlink(missing_ok=True)
        return True
    if auto and depth<3 and len(text)>max_chars(model):
        log(f"This part has {len(text)} characters; {models()[model]['name'].split(' · ')[0]} reads about {max_chars(model)} reliably at once.")
        if split_and_join():return
    last=None
    for attempt in range(retries+1):
        try:
            invoke_engine(model,voice,text,path,opts,reference,ref_text,log);return
        except RuntimeError as exc:
            last=exc
            if not auto or attempt>=retries:raise
            diagnosis=autofix.diagnose(str(exc),model)
            if diagnosis.code=="too_long" and depth<3 and split_and_join():return
            log(f"The part failed ({diagnosis.title}). Trying again ({attempt+1} of {retries}).");time.sleep(1.0)
    raise last

def test_engine(store,model,voice,options=None,reference="",ref_text="",log=lambda msg:None):
    if models()[model]["kind"]=="clone" and not reference:
        raise RuntimeError("To test a voice-cloning adapter, select an authorized reference WAV first.")
    dest=subdir("tests")/f"{model}-{voice or 'clone'}.wav"
    store.invalidate(model+":"+voice)
    invoke_engine(model,voice,"Hello. This is a real audio synthesis test.",dest,options,reference,ref_text,log)
    signature=asset_signature(model,voice)
    store.mark_verified(model+":"+voice,signature,str(dest))
    return dest


def process_job(store,job_id,log=lambda msg:None,halt=None):
    job=store.job(job_id)
    if not job:raise ValueError("Unknown job")
    model=job['model'];lock=managed.generation_lock(model)
    from .generation_policy import effective_device
    device=effective_device(models()[model],json.loads(job['options']).get('device','cpu'))
    store.job_state(job_id,'running')
    _CTX.job=job_id;_ABORT.discard(job_id)
    try:
        if not managed.downloaded(model,job['voice']):
            raise RuntimeError('Model or voice files are missing. Use Check in AI Setup.')
        repaired=False
        while True:
            managed.check_cancel(halt)
            while not lock.acquire(timeout=.2):managed.check_cancel(halt)
            try:
                if repaired or managed.generation_runtime_ok(model,device,log):
                    return _process_job(store,job_id,log,halt)
            finally:lock.release()
            # Repair is outside the runtime lock to avoid an installer lock inversion.
            log('Repairing voice runtime; waiting for dependency installer if necessary')
            managed.ensure_engine(model,device,log,halt)
            repaired=True
    except managed.Cancelled:
        store.job_state(job_id,'paused');log('Job paused')
    except JobStopped:
        store.job_state(job_id,'paused');log('Stopped')
    except Exception as exc:
        store.job_state(job_id,'failed',str(exc));raise
    finally:
        _CTX.job=None;_ABORT.discard(job_id);JOB_START.pop(job_id,None)
        for k in [k for k in PART_RUN if k[0]==job_id]:PART_RUN.pop(k,None)
        with _PROC_LOCK:_PROCS.pop(job_id,None)

def _process_job(store,job_id,log=lambda msg:None,halt=None):
    job=store.job(job_id)
    if not job:
        raise ValueError("Unknown job")
    if not managed.engine_python(job['model']).is_file() or not managed.downloaded(job['model'],job['voice']):
        error="Download this model in AI Setup before generating."
        store.job_state(job_id,"failed",error)
        raise RuntimeError(error)
    opts=json.loads(job["options"])
    from .generation_policy import effective_device
    opts['device']=effective_device(models()[job['model']],opts.get('device','cpu'))
    opts['keep_model_warm']=bool(store.setting('keep_model_warm',True))
    from .resource_policy import limits,thread_budget
    policy=limits(store);opts['cpu_threads']=max(1,thread_budget(policy['cpu_limit'])//(max(1,min(4,int(store.setting('parallel_jobs',1)))) if store.setting('generation_mode','sequential')=='parallel' else 1));opts['gpu_memory_fraction']=policy['gpu_limit']/100
    if (job['model'] in ('chatterbox','turbo') and not opts.get('exact_parts')) or opts.get('auto_chunk'):store.rechunk_failed(job_id)
    from . import speedup
    waiting_parts=sum(1 for x in store.sections(job_id) if not (x["status"]=="complete" and is_audio(Path(x["file"]))))
    n_parts=speedup.choose_parts(store,job["model"],opts["device"],waiting_parts)
    if opts.get("hybrid") and opts["device"]=="cuda" and waiting_parts>=2:
        n_parts=2        # graphics card helper + processor helper share the parts (their threads are split in slot_options)
    elif n_parts>1:
        opts["cpu_threads"]=max(1,opts["cpu_threads"]//n_parts);opts["gpu_memory_fraction"]=opts["gpu_memory_fraction"]/n_parts
    if opts.get("hybrid") and n_parts<2:opts["hybrid"]=False
    store.job_state(job_id,"running")
    from . import perf
    ready_once=readiness(store,job["model"],job["voice"])=="READY · test passed"
    auto=bool(store.setting("autofix",True));retries=max(0,min(3,int(store.setting("retry_parts",1)))) if auto else 0
    rest=max(0.0,float(store.setting("rest_ms",0))/1000)
    from . import guard
    job_start=time.monotonic();JOB_START[job_id]=job_start;PART_STATS.pop(job_id,None);made_before=int(opts.get("made_seconds",0) or 0)
    def save_time():
        try:
            original=json.loads(store.job(job_id)["options"] or "{}");original["made_seconds"]=made_before+round(time.monotonic()-job_start)
            store.set_job_options(job_id,original)
        except Exception:pass
    verify_lock=threading.Lock();verified=[ready_once]
    def make_part(section,slot,failed):
        path=Path(section["file"])
        if section["status"]=="complete" and is_audio(path):return "skip"
        if failed.is_set():return "skip"
        if halt is not None and halt.is_set():return "paused"
        if not guard.wait_until_safe(store,log,lambda:(halt is not None and halt.is_set()) or stopped()):
            if stopped():raise JobStopped()
            return "paused"
        store.section_state(job_id,section["idx"],"running")
        PART_RUN[(job_id,section["idx"])]=(time.monotonic(),len(section["body"]))
        timing={}
        def part_log(line,timing=timing):
            found=perf.parse_line(line)
            if found is not None:timing.update(found)
            else:log(line)
        try:
            started=time.monotonic()
            render_section(job["model"],job["voice"],section["body"],path,opts,job["reference"],job["ref_text"],part_log,retries,auto)
            took=time.monotonic()-started;old=PART_STATS.get(job_id,(0,0.0));PART_STATS[job_id]=(old[0]+len(section["body"]),old[1]+took)
            perf.record(job_id,section["idx"],took,timing,job["model"])
            store.section_state(job_id,section["idx"],"complete")
            with verify_lock:
                if not verified[0]:
                    store.mark_verified(job["model"]+":"+job["voice"],asset_signature(job["model"],job["voice"]),str(path));verified[0]=True
            log(f"Section {section['idx']+1} done")
            if rest:time.sleep(rest)
            return "done"
        except JobStopped:
            store.section_state(job_id,section["idx"],"pending");raise
        except Exception as exc:
            store.section_state(job_id,section["idx"],"failed",str(exc))
            raise
        finally:
            PART_RUN.pop((job_id,section["idx"]),None)
    try:
        if n_parts>1:log(f"Making up to {n_parts} parts at the same time.")
        outcomes=schedule_parts(job_id,store.sections(job_id),n_parts,make_part)
        if "paused" in outcomes:
            store.job_state(job_id,"paused");log("Job paused after completed section");save_time()
            return
        segments=store.sections(job_id)
        join_wave([Path(s["file"]) for s in segments],Path(job["output"]),int(opts.get("pause_ms",220)),opts.get("gaps"))
        from .audio_fx import apply_voice_fx
        apply_voice_fx(Path(job["output"]),float(opts.get("output_speed",1)),float(opts.get("pitch_semitones",0)))
        if opts.get("normalize") or opts.get("trim_silence") or opts.get("fade_ms") or int(opts.get("volume_pct",100) or 100)!=100:
            from .audio_fx import apply_polish
            apply_polish(Path(job["output"]),bool(opts.get("normalize")),bool(opts.get("trim_silence")),int(opts.get("fade_ms",0) or 0),int(opts.get("volume_pct",100) or 100))
        if opts.get("export_transcript"):
            try:
                from .core import strip_pause_tags
                Path(job["output"]).with_suffix(".txt").write_text(" ".join(strip_pause_tags(job["script"]).split()),encoding="utf-8")
            except OSError as exc:log("Transcript was not saved: "+str(exc)[:200])
        if opts.get("auto_mp3"):
            try:export_mp3(Path(job["output"]),log)
            except Exception as exc:log("MP3 was not saved: "+str(exc)[:200])
        save_time()
        store.job_state(job_id,"completed")
        log("Made in "+perf.fmt_duration(made_before+time.monotonic()-job_start)+" (this run: "+perf.fmt_duration(time.monotonic()-job_start)+")")
        log("Final audio: "+job["output"])
        if store.setting("release_after_job",False):
            from . import warm_worker
            warm_worker.release()
    except managed.Cancelled:
        store.job_state(job_id,"paused")
        log("Job paused during dependency preparation")
    except JobStopped:
        store.job_state(job_id,"paused");save_time();log("Stopped. Finished parts are kept; press Generate to continue.")
    except Exception as exc:
        store.job_state(job_id,"failed",str(exc));save_time()
        raise


def resources():
    try:
        import psutil
        vm=psutil.virtual_memory()
        stats=f"CPU {psutil.cpu_percent(interval=0.1):.0f}%   RAM {vm.used/1024**3:.1f}/{vm.total/1024**3:.1f} GiB"
    except ImportError:
        stats=f"Processor: {platform.processor() or 'unknown'}"
    if shutil.which("nvidia-smi"):
        try:
            x=external.run(["nvidia-smi","--query-gpu=name,memory.used,memory.total","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=3,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if x.returncode==0:
                stats += "   GPU "+x.stdout.strip().replace(", "," · ")+" MiB"
        except Exception:
            pass
    return stats


def check_update_feed(url: str):
    """Manifest format: {version, url, sha256}. Genuine installer identity is
    independently checked via Windows Authenticode against a pinned fingerprint.
    """
    from .core import APP_VERSION
    if not url.startswith("https://"):
        raise RuntimeError("Update manifest must use HTTPS")
    req=urllib.request.Request(url,headers={"User-Agent":"AI-Voice-Studio/0.1"})
    with urllib.request.urlopen(req,timeout=25) as r:
        if not r.geturl().startswith("https://"):
            raise RuntimeError("Unsafe redirect from HTTPS update feed")
        response=r.read(65537)
        if len(response)>65536:
            raise RuntimeError("Update feed is unexpectedly large")
    info=json.loads(response.decode("utf-8"))
    if not isinstance(info,dict):raise RuntimeError("Invalid update manifest")
    for field in ("version","url","sha256"):
        if not isinstance(info.get(field),str) or not info[field]:
            raise RuntimeError("Update manifest missing "+field)
    if not info["url"].startswith("https://") or not re.fullmatch(r"[a-fA-F0-9]{64}",info["sha256"]):
        raise RuntimeError("Update manifest URL or SHA-256 is invalid")
    def ver_tuple(v):
        if not re.fullmatch(r"\d+\.\d+\.\d+",v):raise ValueError("Invalid version in update manifest")
        return tuple(map(int,v.split(".")))
    info["newer"]=ver_tuple(info["version"])>ver_tuple(APP_VERSION)
    info["size"]=download_size(info["url"]) if info["newer"] else None
    return info


def download_signed_update(manifest, fingerprint, log=lambda msg:None):
    """Pin the signing certificate, not the text name of a publisher.
    Unsigned binaries and changed certificate fingerprints are rejected.
    """
    if os.name!="nt":raise RuntimeError("Windows update verification requires Windows Authenticode")
    if not re.fullmatch(r"[a-fA-F0-9]{40}",fingerprint):
        raise RuntimeError("Set the exact 40-digit Authenticode signer certificate SHA-1 thumbprint first")
    dest=subdir("updates")/f"AI_Voice_Studio_{manifest['version']}.exe"
    download_one(manifest["url"],dest,log)
    if sha256(dest).lower()!=manifest["sha256"].lower():
        dest.unlink(missing_ok=True)
        raise RuntimeError("Downloaded update failed SHA-256 verification")
    quoted=str(dest).replace("'","''")
    command=f"$s=Get-AuthenticodeSignature -LiteralPath '{quoted}'; if ($s.Status -ne 'Valid' -or -not $s.SignerCertificate) {{exit 3}}; Write-Output $s.SignerCertificate.Thumbprint"
    p=external.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],capture_output=True,text=True,timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if p.returncode or p.stdout.strip().lower()!=fingerprint.lower():
        dest.unlink(missing_ok=True)
        raise RuntimeError("Update signature is missing, untrusted, or from an unexpected certificate. Update rejected.")
    log("Installer SHA-256 and pinned Authenticode signer verified")
    return dest


def export_mp3(source: Path, log=lambda msg:None) -> Path:
    if not is_audio(source):
        raise RuntimeError("Generate the final WAV before exporting MP3")
    from .audio_fx import ffmpeg_path
    ffmpeg=ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not installed. On Windows, install it with WinGet (Gyan.FFmpeg) or use WAV export.")
    destination=source.with_suffix(".mp3")
    run_command([ffmpeg,"-hide_banner","-nostdin","-y","-i",str(source),"-codec:a","libmp3lame","-b:a","192k",str(destination)],log,timeout=300)
    if not destination.exists() or destination.stat().st_size<1000:
        raise RuntimeError("MP3 encoding did not create a valid file")
    log("MP3 export complete: "+str(destination))
    return destination


def gpu_diagnostics(log=lambda msg:None):
    if not module_present("torch"):
        raise RuntimeError("PyTorch base must be installed to test CUDA")
    script="import torch;print('PyTorch',torch.__version__);print('CUDA active',torch.cuda.is_available());print('GPU',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'not available')"
    run_command(python_command()+["-c",script],log,timeout=120,env=python_env())
