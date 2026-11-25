"""Managed per-family environments and cancellable, revision-pinned downloads."""
from __future__ import annotations
import hashlib,json,os,re,shutil,subprocess,threading,time,urllib.request,urllib.parse,tempfile
from pathlib import Path
from .core import models,subdir,sha256,is_audio
from . import external, downloads
from .profile_gate import ProfileGate
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)
INSTALL_LOCK=threading.Lock()
PROFILE_LOCKS={}
PROFILE_GUARD=threading.Lock()
def profile_lock(model):
    key=models()[model]["profile"]
    with PROFILE_GUARD:return PROFILE_LOCKS.setdefault(key,ProfileGate())

def generation_lock(model):
    return profile_lock(model).reader()

class Cancelled(Exception): pass

def check_cancel(cancel):
    if cancel and cancel.is_set(): raise Cancelled('Cancelled by user; partial model files retained.')

def run(cmd, log, cancel=None, env=None, network_idle=None):
    from queue import Queue,Empty
    tail=[]; q=Queue(); last_output=time.monotonic(); last_heartbeat=last_output
    p=external.popen([str(x) for x in cmd],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',env=env,creationflags=FLAGS,priority='background')
    from . import load_control
    governor=load_control.govern(p)
    def consume():
        try:
            for line in p.stdout: q.put(line.rstrip())
        finally:q.put(None)
    reader=threading.Thread(target=consume,daemon=True);reader.start()
    try:
        started=time.monotonic()
        while True:
            check_cancel(cancel)
            if time.monotonic()-started>10800: raise TimeoutError('Dependency operation timed out.')
            try: line=q.get(timeout=.2)
            except Empty:
                now=time.monotonic()
                if network_idle and now-last_output>=network_idle and tail and is_network_wait(tail[-1]):
                    raise RuntimeError('Network transfer idle too long: '+tail[-1][:300])
                if now-last_heartbeat>=15:
                    log(f'Still running ({int(now-started)}s elapsed; {int(now-last_output)}s since last output): '+(tail[-1][:200] if tail else 'waiting for subprocess output'))
                    last_heartbeat=now
                continue
            if line is None: break
            last_output=time.monotonic(); tail.append(line); tail=tail[-100:]; log(line[:4000])
        if p.wait(timeout=20): raise RuntimeError('Command failed:\n'+'\n'.join(tail))
    finally:
        governor.close()
        if p.poll() is None:
            if os.name=='nt': external.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=FLAGS)
            else: p.kill()
            p.wait()
        reader.join(timeout=2)
        if p.stdout is not None:p.stdout.close()

def is_network_wait(line):
    # Do not kill quiet wheel compilation or package installation.
    return bool(re.search(r"(?:downloading|fetching|cloning|retrying|connecting)\b",line,re.I)) and not bool(re.search(r"(?:building wheel|installing build dependencies|preparing metadata|successfully)",line,re.I))

def network_failure(exc):
    msg=str(exc).lower()
    return any(x in msg for x in ('timed out','timeout','connection reset','connection aborted','connection broken','incomplete download','incomplete response','network transfer idle','temporary failure','remote end closed','connection refused','remote disconnected','could not resolve host','name resolution','http 502','http 503','http 504','http error 502','http error 503','http error 504','http error 429','readtimeout','protocolerror','unexpected eof','early eof','rpc failed'))

def pip_run(cmd,log,cancel):
    for attempt in range(3):
        check_cancel(cancel)
        try:return run(cmd,log,cancel,env(False),network_idle=600)
        except RuntimeError as exc:
            if not network_failure(exc) or attempt==2:raise
            delay=3*(attempt+1);log(f'Package network interrupted; retry {attempt+2}/3 in {delay}s (cached files retained)')
            end=time.monotonic()+delay
            while time.monotonic()<end:check_cancel(cancel);time.sleep(.2)


def ensure_resumable_pip(py,log,cancel):
    ok,_=probe_import(py,"from pip import __version__;from pip._vendor.packaging.version import Version;assert Version(__version__)>=Version('25.2')")
    if ok:return
    log('Updating package downloader for interrupted-download recovery')
    try:
        pip_run([py,'-u','-m','pip','install','--upgrade','--index-url','https://pypi.org/simple','--retries','8','--timeout','120','--progress-bar','off','pip>=25.2,<27'],log,cancel)
    except RuntimeError as exc:
        log('Could not update pip; continuing with existing package downloader: '+str(exc)[-600:])


def probe(path):
    try:
        r=external.run([str(path),'-c','import sys,struct;print(sys.executable if sys.version_info[:2]==(3,11) and struct.calcsize("P")==8 else "BAD")'],capture_output=True,text=True,timeout=8,creationflags=FLAGS)
        x=Path(r.stdout.strip()); return x if r.returncode==0 and x.is_file() else None
    except (OSError,subprocess.SubprocessError): return None

def runtime_python():
    candidates=[]
    saved=subdir('py')/'runtime.json'
    if saved.exists():
        try: candidates.append(json.loads(saved.read_text())['python'])
        except (ValueError,KeyError): pass
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,r'Software\Python\PythonCore\3.11\InstallPath',0,winreg.KEY_READ|view) as k:
                        try: candidates.append(winreg.QueryValueEx(k,'ExecutablePath')[0])
                        except OSError: candidates.append(str(Path(winreg.QueryValue(k,None))/'python.exe'))
                except OSError: pass
        candidates += [Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Python/Python311/python.exe',Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Python311/python.exe',Path('C:/Python311/python.exe')]
        launcher=shutil.which('py') or str(Path(os.environ.get('WINDIR','C:/Windows'))/'py.exe')
        try:
            p=external.run([launcher,'-3.11','-c','import sys;print(sys.executable)'],capture_output=True,text=True,timeout=8,creationflags=FLAGS)
            if p.returncode==0:candidates.append(p.stdout.strip())
        except (OSError,subprocess.SubprocessError): pass
    if sys.platform=='darwin':
        candidates += ['/opt/homebrew/bin/python3.11','/usr/local/bin/python3.11','/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11',str(Path.home()/'.pyenv/shims/python3.11')]
    candidates += [shutil.which('python3.11'),shutil.which('python')]
    for candidate in candidates:
        if candidate and 'windowsapps' not in str(candidate).lower():
            found=probe(candidate)
            if found:return found
    return None

MAC_PYTHON_HELP=('Python 3.11 could not be installed automatically. Open https://www.python.org/downloads/macos/ , download the Python 3.11 macOS installer '
                 '(the file ends in .pkg), open it, press Continue until it finishes, and then press the button here again.')
MAC_TORCH_HELP=('\n\nOn a Mac the engines need PyTorch 2.x from the normal Python index. Apple Silicon Macs (M1 or newer) have it. Intel Macs do not get the newer PyTorch '
                'builds these engines need: use Piper, Kokoro or the Word checker there.')
PSF_TEAM_ID='BMM5U3QVKW'
PYTHON_MAC_PKG='https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg'

def torch_index(wheel_tag):
    return [] if sys.platform=='darwin' else ['--index-url','https://download.pytorch.org/whl/'+wheel_tag]

def torch_names(version,wheel_tag):
    return [f'torch=={version}',f'torchaudio=={version}'] if sys.platform=='darwin' else [f'torch=={version}+{wheel_tag}',f'torchaudio=={version}+{wheel_tag}']

def xcode_tools_present():
    try:return subprocess.run(['/usr/bin/xcode-select','-p'],capture_output=True,timeout=10).returncode==0
    except (OSError,subprocess.SubprocessError):return False

def ask_xcode_tools(log=lambda m:None):
    """macOS shows its own window that installs the compiler and Git (the Apple developer tools)."""
    try:subprocess.Popen(['/usr/bin/xcode-select','--install'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except OSError as exc:log('Could not open the developer tools installer: '+str(exc))

def install_python_mac(log,cancel=None):
    """Python 3.11 on a Mac: Homebrew when it exists, otherwise the official installer from python.org (checked for the Python Software Foundation signature)."""
    for brew in (shutil.which('brew'),'/opt/homebrew/bin/brew','/usr/local/bin/brew'):
        if brew and Path(brew).is_file():
            log('Installing Python 3.11 with Homebrew')
            try:
                run([brew,'install','python@3.11'],log,cancel);
                if runtime_python():return
            except RuntimeError as exc:log('Homebrew could not install Python: '+str(exc)[-300:])
            break
    import ssl
    dest=subdir('py')/'python-3.11.9-macos11.pkg'
    log('Downloading the official Python 3.11 installer from python.org')
    try:
        try:
            import certifi
            context=ssl.create_default_context(cafile=certifi.where())
        except ImportError:context=ssl.create_default_context()
        with urllib.request.urlopen(PYTHON_MAC_PKG,context=context,timeout=60) as r,open(dest,'wb') as f:shutil.copyfileobj(r,f,1024*1024)
    except (OSError,ValueError) as exc:
        raise RuntimeError('The Python installer could not be downloaded ('+str(exc)[:120]+').\n\n'+MAC_PYTHON_HELP) from exc
    check=subprocess.run(['/usr/sbin/pkgutil','--check-signature',str(dest)],capture_output=True,text=True,timeout=60)
    if check.returncode or PSF_TEAM_ID not in check.stdout:
        dest.unlink(missing_ok=True)
        raise RuntimeError('The downloaded installer is not signed by the Python Software Foundation, so it was not run.\n\n'+MAC_PYTHON_HELP)
    log('Installer signature checked. macOS will ask for your password to install Python.')
    script='do shell script "/usr/sbin/installer -pkg \\"'+str(dest)+'\\" -target /" with administrator privileges'
    r=subprocess.run(['/usr/bin/osascript','-e',script],capture_output=True,text=True,timeout=1800)
    if r.returncode:raise RuntimeError('Python was not installed ('+(r.stderr or r.stdout).strip()[:200]+').\n\n'+MAC_PYTHON_HELP)

def ensure_runtime(log,cancel=None):
    p=runtime_python()
    if not p:
        if sys.platform=='darwin':
            install_python_mac(log,cancel)
            p=runtime_python()
            if not p:raise RuntimeError(MAC_PYTHON_HELP)
            (subdir('py')/'runtime.json').write_text(json.dumps({'python':str(p)}))
            return p
        if os.name!='nt':raise RuntimeError('Python 3.11 x64 required.')
        winget=shutil.which('winget')
        if not winget:raise RuntimeError('WinGet is missing. Install Microsoft App Installer, then retry.')
        try:run([winget,'install','--id','Python.Python.3.11','--exact','--source','winget','--scope','user','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity'],log,cancel)
        except RuntimeError:
            if not runtime_python():raise
        p=runtime_python()
        if not p:raise RuntimeError('Python installation finished but Python 3.11 could not be executed. Restart Windows or repair Python.')
    (subdir('py')/'runtime.json').write_text(json.dumps({'python':str(p)}))
    return p

def profile_dir(model):return subdir('env')/models()[model]['profile']
def engine_python(model):return profile_dir(model)/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
def env(offline=True):
    e=os.environ.copy(); e.pop('PYTHONPATH',None); e.pop('PYTHONHOME',None);e['PYTHONNOUSERSITE']='1';e['PIP_CACHE_DIR']=str(subdir('pip_cache'));e['HF_HOME']=str(subdir('hf_cache'));e['HF_HUB_OFFLINE']='1' if offline else '0';e['TRANSFORMERS_OFFLINE']=e['HF_HUB_OFFLINE'];e['HF_HUB_DISABLE_TELEMETRY']='1';e['HF_HUB_DOWNLOAD_TIMEOUT']='120';e.setdefault('HF_XET_NUM_CONCURRENT_RANGE_GETS','8');e['HF_HUB_ETAG_TIMEOUT']='45';e['TEMP']=e['TMP']=str(subdir('temp'));e['TORCH_HOME']=str(subdir('torch_cache'));e['XDG_CACHE_HOME']=str(subdir('cache'));e['GIT_HTTP_LOW_SPEED_LIMIT']='1';e['GIT_HTTP_LOW_SPEED_TIME']='120';e['PIP_DISABLE_PIP_VERSION_CHECK']='1';e['PIP_TIMEOUT']='120';e['PIP_RETRIES']='8';e['PIP_RESUME_RETRIES']='10';e['PIP_NO_INPUT']='1';e.pop('PYTHONOPTIMIZE',None)
    from .network_trust import child_bundle
    try:
        bundle=child_bundle(subdir('cache'))
        if bundle:
            for key in ('SSL_CERT_FILE','REQUESTS_CA_BUNDLE','PIP_CERT','GIT_SSL_CAINFO','CURL_CA_BUNDLE'):e.setdefault(key,str(bundle))
    except (OSError,ImportError,ValueError):pass
    return e

def probe_import(py, code, timeout=180):
    """Return the actual process output, including DLL errors and tracebacks."""
    try:
        r=external.run([py,'-c',code],capture_output=True,text=True,errors='replace',timeout=timeout,env=env(),creationflags=FLAGS,priority='background')
        return r.returncode==0, (f'Exit code {r.returncode}\n'+(r.stdout or '')+'\n'+(r.stderr or '')).strip()
    except (OSError,subprocess.SubprocessError) as exc:return False,repr(exc)

def import_ok(py,module):
    return probe_import(py,f'import importlib;importlib.import_module({module!r})')[0]

ENGINE_SYMBOLS={
 'piper':'from piper import PiperVoice', 'kokoro':'from kokoro_onnx import Kokoro;import soundfile;from misaki import en,espeak,ja,zh;from importlib.metadata import version;assert version("misaki-fork")=="0.9.6"',
 'chatterbox':'from chatterbox.tts import ChatterboxTTS', 'turbo':'from chatterbox.tts_turbo import ChatterboxTurboTTS',
 'f5':'from f5_tts.api import F5TTS', 'openf5':'from f5_tts.api import F5TTS',
 'voxcpm':'from voxcpm import VoxCPM', 'indextts2':'from indextts.infer_v2 import IndexTTS2',
 'dia':'from transformers import AutoProcessor,DiaForConditionalGeneration',
 'xtts2':'from TTS.api import TTS;import soundfile', 'styletts2':'from styletts2 import tts;import soundfile',
 'wordcheck':'from faster_whisper import WhisperModel',
 'fishs2':'import fish_speech',
}

MODULE_TO_PACKAGE={'yaml':'pyyaml','PIL':'pillow','cv2':'opencv-python-headless','sklearn':'scikit-learn','bs4':'beautifulsoup4','dateutil':'python-dateutil'}

def reduced_marker(model):return profile_dir(model)/(model+'.reduced')

def is_reduced(model):
    """True when this engine was installed without an optional part (Auto resolve), for example Kokoro without Japanese."""
    try:return reduced_marker(model).is_file()
    except Exception:return False

def set_reduced(model):
    marker=reduced_marker(model);marker.parent.mkdir(parents=True,exist_ok=True);marker.write_text('optional part left out',encoding='utf-8')

def clear_reduced():
    """Try the full package set again the next time an engine is repaired."""
    for mid in models():
        try:reduced_marker(mid).unlink(missing_ok=True)
        except Exception:pass

def engine_symbol(model):
    symbol=ENGINE_SYMBOLS.get(model,'from qwen_tts import Qwen3TTSModel')
    return symbol.replace('misaki import en,espeak,ja,zh','misaki import en,espeak,zh') if model=='kokoro' and is_reduced('kokoro') else symbol

def install_with_resolve(model,py,constraints,package,log,cancel,installer=None):
    """Install an engine and its helpers. If a build tool is missing for an optional part, install the engine without that part."""
    from .model_sources import extra_packages
    from . import autoresolve
    installer=installer or pip_install;seen=[]
    def capture(line):
        seen.append(str(line));log(line)
    args=['--upgrade','--upgrade-strategy','only-if-needed',*constraints,package]
    try:installer(py,[*args,*extra_packages(model,is_reduced(model))],capture,cancel)
    except RuntimeError as exc:
        part=None if is_reduced(model) else autoresolve.reducible(model,'\n'.join(seen[-300:])+'\n'+str(exc))
        if not part:raise
        log(f'Auto resolve: a build tool needed for {part} is missing on this PC, so {model} is installed without {part}. Settings > Tools > Install build tools adds it later.')
        set_reduced(model)
        installer(py,[*args,*extra_packages(model,True)],log,cancel)

def engine_probe(py, model):
    return probe_import(py,'from transformers import VitsModel,AutoTokenizer;import soundfile,uroman' if model.startswith('hf_vits_') else engine_symbol(model))

def torch_version(entry):
    return '2.6.0' if entry['profile']=='chatterbox' else '2.8.0'

def torch_probe(py,version,device):
    code=f"import torch,torchaudio;print(torch.__version__,torchaudio.__version__,torch.version.cuda,torch.cuda.is_available());assert torch.__version__.split('+')[0]=={version!r};assert torchaudio.__version__.split('+')[0]=={version!r}"
    if device=='cuda':code+=';assert torch.version.cuda is not None, "CPU-only PyTorch build"'
    return probe_import(py,code)

def wheelhouse():
    return subdir('wheels')

def wheel_from_report(item, log, cancel, py):
    """Stage a resolved binary distribution; return None for source/VCS packages."""
    info=item.get('download_info',{});url=info.get('url','');u=urllib.parse.urlsplit(url)
    filename=urllib.parse.unquote(u.path.rsplit('/',1)[-1]);cache=wheelhouse()
    if not filename.lower().endswith('.whl') or '/' in filename or '\\' in filename:return None
    dest=cache/filename
    if u.scheme=='file':
        # Only accept local files from our own shared wheel directory.
        local=Path(urllib.request.url2pathname(urllib.parse.unquote(u.path)))
        if local.resolve()==dest.resolve() and dest.is_file():
            log('Reusing cached wheel: '+filename);return dest
        return None
    if u.scheme!='https':return None
    digest=(info.get('archive_info',{}).get('hashes') or {}).get('sha256')
    if not digest or not re.fullmatch(r'[a-fA-F0-9]{64}',digest):return None
    if dest.is_file() and sha256(dest)==digest.lower():
        log('Reusing cached wheel: '+filename);return dest
    check_cancel(cancel)
    from .package_cache import reuse_http_body
    if reuse_http_body(subdir('pip_cache'),url,dest,digest,lambda:check_cancel(cancel)):
        log('Reusing verified pip-cached package: '+filename);return dest
    log('Downloading package: '+filename)
    # Persist partial wheels in the shared cache instead of deleting a temporary
    # pip-download directory on every interrupted connection.
    item={'name':filename,'url':url,'size':None,'sha256':digest.lower()}
    last=[0.0,0,None]
    def progress(done,total,stage):
        now=time.monotonic()
        if now-last[0]>=2 or stage.startswith(('Connection','Verifying')):
            rate=(done-last[1])/(now-last[0])/1e6 if last[0] and done>=last[1] and last[2]==stage else 0
            log(f'Package {filename}: {done/1e6:.1f} MB'+(f' / {total/1e6:.1f} MB' if total else '')+(f' · {rate:.2f} MB/s' if rate else '')+' · '+stage);last[:]=[now,done,stage]
    downloads.download_set(cache,[item],progress,lambda:check_cancel(cancel),valid_file,sha256,manifest=False)
    return dest

def prefetch_torch(py,version,tag,log,cancel):
    # Pre-stage exact Windows wheels before pip dry-run, which otherwise may
    # download the full archive just to resolve metadata.
    if os.name!='nt':return []
    from .package_cache import torch_report
    paths=[]
    for name in ('torch','torchaudio'):
        check_cancel(cancel)
        expected=wheelhouse()/f'{name}-{version}+{tag}-cp311-cp311-win_amd64.whl'
        if expected.is_file():paths.append(str(expected));continue # app-owned wheelhouse; pip validates format/tags
        try:report=torch_report(name,version,tag)
        except Exception as exc:
            log('Official wheel index unavailable; using pip resolver: '+str(exc));continue
        if report:
            wheel=wheel_from_report(report,log,cancel,py)
            if wheel:paths.append(str(wheel))
    return paths if len(paths)==2 else []

def pip_install(py,args,log,cancel):
    """Resolve once against installed packages, stage wheels, then install locally.
    Source/Git packages still require pip's normal build/install flow; compatible
    binary wheels remain available to that flow and every other profile.
    """
    cache=wheelhouse();base=[py,'-u','-m','pip','install','--prefer-binary','--retries','8','--timeout','120','--progress-bar','off','--find-links',cache]
    with tempfile.TemporaryDirectory(prefix='avs-resolution-',dir=subdir('temp')) as td:
        report=Path(td)/'report.json'
        log('Resolving compatible package versions (only missing or incompatible packages)')
        # Prefer a fully cached binary resolution before contacting package indexes.
        # Do not attempt against Git/direct source requirements, which may access the network.
        local_args=[];skip=False
        for arg in args:
            if skip:skip=False;continue
            if str(arg) in ('--index-url','--extra-index-url'):skip=True;continue
            local_args.append(arg)
        cached=any(cache.glob('*.whl')) and not any(str(a).startswith(('git+','http:','https:')) for a in local_args)
        if cached:
            try:
                log('Checking shared offline package cache first')
                run([*base,'--no-index','--dry-run','--report',report,*local_args],log,cancel,env(False))
            except RuntimeError:
                report.unlink(missing_ok=True);log('Cache incomplete; fetching only missing compatible packages')
        if not report.is_file():pip_run([*base,'--dry-run','--report',report,*args],log,cancel)
        plan=json.loads(report.read_text(encoding='utf-8')).get('install',[])
        if not plan:
            log('All required packages already installed');return
        staged=[];source=False
        for i,item in enumerate(plan,1):
            check_cancel(cancel);metadata=item.get('metadata',{})
            log(f'Package {i}/{len(plan)}: {metadata.get("name","unknown")} {metadata.get("version","")}')
            wheel=wheel_from_report(item,log,cancel,py)
            if wheel is None:source=True
            else:staged.append(wheel)
        if source:
            log('Source/Git package requires normal pip build; reusing staged wheels and shared cache')
            pip_run([*base,*args],log,cancel);return
        # The resolver has already selected the full compatible transaction.
        # Avoid another index lookup or resolver pass, and preserve force-repair behavior.
        req=Path(td)/'wheels.txt';req.write_text('\n'.join(p.resolve().as_uri() for p in staged)+'\n',encoding='utf-8')
        log(f'Installing {len(staged)} resolved packages from local storage')
        force=['--force-reinstall'] if '--force-reinstall' in args else []
        run([py,'-u','-m','pip','install','--no-index','--no-deps',*force,'-r',req],log,cancel,env(False))

def ensure_git(log,cancel):
    if sys.platform=='darwin':
        if not xcode_tools_present():
            ask_xcode_tools(log)
            raise RuntimeError('Git is part of the Apple developer tools. A window asked to install them: click Install, wait until it finishes, then try again.')
        return
    if os.name!='nt' or shutil.which('git'):return
    paths=[Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/cmd',Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Git/cmd']
    if not any((x/'git.exe').is_file() for x in paths):
        winget=shutil.which('winget')
        if not winget:raise RuntimeError('Git required; WinGet unavailable.')
        run([winget,'install','--id','Git.Git','--exact','--source','winget','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity'],log,cancel)
    for path in paths:
        if (path/'git.exe').is_file():os.environ['PATH']=str(path)+os.pathsep+os.environ.get('PATH','');return
    if not shutil.which('git'):raise RuntimeError('Git is still unavailable after installation.')

def repair_native_runtime(detail,log,cancel):
    """Repair missing VC runtime only on Windows and only after a native loader failure."""
    if os.name!='nt' or not any(x in detail.lower() for x in ('dll load failed','winerror 126','winerror 1114','winerror 127','vcruntime','msvcp140')):return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as k:
            if winreg.QueryValueEx(k,'Installed')[0]==1:return False
    except OSError:pass
    winget=shutil.which('winget')
    if not winget:
        log('Possible missing Microsoft Visual C++ redistributable (x64). WinGet is unavailable.');return False
    log('Installing missing Microsoft Visual C++ x64 redistributable')
    try:
        run([winget,'install','--id','Microsoft.VCRedist.2015+.x64','--exact','--source','winget','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity'],log,cancel)
        return True
    except RuntimeError as e:
        log('Visual C++ runtime repair failed: '+str(e));return False


def nvidia_smi():
    candidates=[shutil.which('nvidia-smi')]
    if os.name=='nt':
        candidates += [Path(os.environ.get('WINDIR','C:/Windows'))/'System32/nvidia-smi.exe',Path(os.environ.get('ProgramFiles','C:/Program Files'))/'NVIDIA Corporation/NVSMI/nvidia-smi.exe']
    return next((str(x) for x in candidates if x and Path(x).is_file()),None)

def ensure_engine(model,device,log,cancel=None):
    """Wait for global installer capacity without monopolizing a runtime.

    Never wait for the global installer while holding a profile lock. Conversely
    release the global lock if a running inference owns the requested profile.
    """
    lock=profile_lock(model)
    waiting=time.monotonic();notified=waiting
    log('Waiting for dependency installer (other downloads may continue)')
    while True:
        check_cancel(cancel)
        if INSTALL_LOCK.acquire(timeout=.2):
            if lock.acquire(blocking=False):
                try:
                    check_cancel(cancel)
                    return _ensure_engine(model,device,log,cancel)
                finally:
                    lock.release();INSTALL_LOCK.release()
            INSTALL_LOCK.release()
        if time.monotonic()-notified>=15:
            notified=time.monotonic();log(f'Waiting for shared dependency installer or active voice generation ({int(notified-waiting)}s)')
        if cancel and cancel.wait(.2):check_cancel(cancel)
        elif not cancel:time.sleep(.2)


def _ensure_engine(model,device,log,cancel=None):
    from . import warm_worker
    warm_worker.release_profile(model)
    entry=models()[model]
    check_cancel(cancel); python=ensure_runtime(log,cancel); py=engine_python(model)
    if not py.is_file():run([python,'-m','venv',profile_dir(model)],log,cancel,env())
    elif not probe(py):run([python,'-m','venv','--upgrade',profile_dir(model)],log,cancel,env())
    if not probe(py):raise RuntimeError(f'Engine Python cannot execute: {py}')
    if not import_ok(py,'pip'):run([py,'-m','ensurepip','--upgrade'],log,cancel,env(False))
    marker=profile_dir(model)/(model+'.installed.json')
    marker.unlink(missing_ok=True)
    # Keep exact compatible torch/torchaudio pairs. Do not install an unbounded latest release.
    version=torch_version(entry); constraints=[]
    if entry['torch']:
        mode='cuda' if device=='cuda' and sys.platform!='darwin' and (os.name!='nt' or nvidia_smi()) else 'cpu'
        if device=='cuda' and mode=='cpu':log('NVIDIA driver tools not found; installing the CPU runtime instead.')
        index='cu126' if version=='2.6.0' else 'cu128'
        wheel_tag=index if mode=='cuda' else 'cpu'
        ok,detail=torch_probe(py,version,mode)
        if not ok and repair_native_runtime(detail,log,cancel):ok,detail=torch_probe(py,version,mode)
        if not ok:
            ensure_resumable_pip(py,log,cancel)
            log('Preparing compatible PyTorch: '+detail)
            torch_args=(None if sys.platform=='darwin' else prefetch_torch(py,version,wheel_tag,log,cancel)) or torch_names(version,wheel_tag)
            try:pip_install(py,[*torch_index(wheel_tag),'--upgrade',*torch_args],log,cancel)
            except RuntimeError as exc:raise RuntimeError(f'PyTorch initial check for {model}:\n{detail}\n\nInstallation failed:\n{exc}'+(MAC_TORCH_HELP if sys.platform=='darwin' else '')) from exc
            ok,detail=torch_probe(py,version,mode)
            if not ok:
                # Same version with a different local wheel (CPU/CUDA), or broken native files.
                pip_install(py,[*torch_index(wheel_tag),'--force-reinstall','--no-deps',*torch_names(version,wheel_tag)],log,cancel)
                ok,detail=torch_probe(py,version,mode)
                if not ok:raise RuntimeError('PyTorch import/build check failed for '+model+'\n'+detail)
        path=profile_dir(model)/'avs-torch-constraints.txt';path.write_text(f'torch=={version}\ntorchaudio=={version}\n',encoding='ascii');constraints=['-c',path]
    log("Checking existing engine imports and package compatibility")
    ok,detail=engine_probe(py,model)
    if not ok and repair_native_runtime(detail,log,cancel):ok,detail=engine_probe(py,model)
    # Check package identity as well, so previously installed incompatible versions are replaced.
    package=entry['package']; version_ok=True
    if '==' in package:
        name,wanted=package.split('==',1)
        version_ok,_=probe_import(py,f'from importlib.metadata import version;assert version({name!r})=={wanted!r}')
    check=external.run([py,'-m','pip','check'],capture_output=True,text=True,errors='replace',timeout=90,env=env(),creationflags=FLAGS,priority='background')
    if not ok or not version_ok or check.returncode:
        ensure_resumable_pip(py,log,cancel)
        log('Package repair needed: '+detail+'\n'+(check.stdout or '')+(check.stderr or ''))
        if entry['profile']=='chatterbox' or package.startswith('git+'):ensure_git(log,cancel)
        try:install_with_resolve(model,py,constraints,package,log,cancel)
        except RuntimeError as exc:raise RuntimeError(f'{model} initial engine import/check:\n{detail}\n\nPackage setup failed:\n{exc}') from exc
        ok,detail=engine_probe(py,model)
        if not ok:
            # One targeted reinstall; avoid redownloading the entire environment or model weights.
            log('Engine import still failing: '+detail)
            original=detail
            try:pip_install(py,['--force-reinstall','--no-deps',*constraints,package],log,cancel)
            except RuntimeError as exc:raise RuntimeError(f'{model} import failed:\n{original}\n\nPackage repair also failed:\n{exc}') from exc
            ok,detail=engine_probe(py,model)
    # A plain missing module (for example 'requests') is installed and the check is repeated, up to three times.
    for _ in range(3):
        if ok:break
        found=[m for m in re.findall(r"No module named '([A-Za-z0-9_]+)",detail) if m not in ('torch','torchaudio')]
        if not found:break
        names=sorted({MODULE_TO_PACKAGE.get(m,m) for m in found})
        log('Installing a missing part: '+', '.join(names))
        try:pip_install(py,[*constraints,*names],log,cancel)
        except RuntimeError:break
        ok,detail=engine_probe(py,model)
    if not ok:raise RuntimeError(f'{model} engine import failed in {py}\n{detail}')
    run([py,'-m','pip','check'],log,cancel,env())
    if entry['torch']:
        ok,detail=torch_probe(py,version,mode)
        if not ok:raise RuntimeError('Engine dependency installation changed PyTorch incompatibly:\n'+detail)
        log(detail)
    # Compatible variants (e.g. four Qwen checkpoints) share installed packages.
    info=json.dumps({'package':package,'time':time.time(),'runtime':str(py),'repair_schema':2,'voice_language_schema':1})
    for mid,other in models().items():
        if other['profile']==entry['profile'] and other['package']==package and other['module']==entry['module']:
            target=profile_dir(mid)/(mid+'.installed.json');target.parent.mkdir(parents=True,exist_ok=True);tmp=target.with_suffix('.tmp');tmp.write_text(info);tmp.replace(target)


def marker_current(model):
    try:
        mark=json.loads((profile_dir(model)/(model+'.installed.json')).read_text())
        return engine_python(model).is_file() and mark.get('repair_schema')==2 and mark.get('package')==models()[model]['package'] and (model!='kokoro' or mark.get('voice_language_schema')==1)
    except (OSError,ValueError,KeyError):return False

def generation_runtime_ok(model,device,log=lambda _:None):
    if marker_current(model):return True
    py=engine_python(model)
    if not py.is_file():return False
    entry=models()[model]
    log('Checking existing voice runtime locally')
    ok,detail=engine_probe(py,model)
    if not ok:return False
    package=entry['package']
    if '==' in package:
        name,wanted=package.split('==',1)
        ok,_=probe_import(py,f'from importlib.metadata import version;assert version({name!r})=={wanted!r}',timeout=20)
        if not ok:return False
    if entry.get('torch'):
        ok,_=torch_probe(py,torch_version(entry),device)
        if not ok:return False
    log('Existing runtime imports verified; no dependency download required')
    return True


def fetch_json(url):
    return downloads.fetch_json(url)

def file_manifest(model,voice,persist=True,remote=False):
    from . import services
    entry=models()[model]; folder=services.model_folder(model,voice)
    mf=manifest_path(model,voice)
    if remote:persist=False
    if mf.is_file() and not remote:
        try:
            saved=json.loads(mf.read_text(encoding='utf-8'))
            if saved and isinstance(saved,list):return saved
        except (ValueError,OSError,KeyError):pass
    pending=folder/'.avs-download-plan.json'
    old_plan=None
    if pending.is_file() and not remote:
        try:
            saved=json.loads(pending.read_text(encoding='utf-8'))
            if saved.get('entry')==entry and saved.get('items'):
                old_plan=saved['items']
                if all(x.get('size') is not None for x in old_plan):return old_plan
        except (ValueError,OSError,KeyError,TypeError):pass
    def save_plan(items):
        if not persist:return items
        tmp=pending.with_suffix('.tmp');tmp.write_text(json.dumps({'entry':entry,'items':items}),encoding='utf-8');os.replace(tmp,pending);return items
    def with_sizes(files):
        from concurrent.futures import ThreadPoolExecutor
        fast=downloads.is_fast()
        def get_size(item):
            if item.get('size') is not None:return dict(item)
            if fast:
                with downloads.fast_mode():return {**item,'size':services.download_size(item['url'])}
            return {**item,'size':services.download_size(item['url'])}
        with ThreadPoolExecutor(max_workers=8 if fast else 3) as pool:return list(pool.map(get_size,files))
    if old_plan:return save_plan(with_sizes(old_plan))
    if not entry.get('snapshot'):
        from .repository_metadata import hydrate
        return save_plan(with_sizes(hydrate(services.voice_files(model,voice),fetch_json)))
    repo=entry['hf_repo']; info=fetch_json('https://huggingface.co/api/models/'+repo+'?blobs=true'); rev=entry.get('hf_revision') or info['sha']
    if entry.get('hf_revision') and info['sha']!=rev:
        info=fetch_json('https://huggingface.co/api/models/'+repo+'/revision/'+rev+'?blobs=true')
    result=[]
    for x in info['siblings']:
        name=x['rfilename']; path=Path(name)
        if model.startswith('hf_vits_') and (path.suffix.lower() not in ('.json','.safetensors','.txt','.model') or '/' in name or ':' in name or '\\' in name):continue
        if (model.startswith('qwen') or model=='dia') and name.endswith(('.bin','.pt','.pth')) and any(y['rfilename'].endswith('.safetensors') and Path(y['rfilename']).parent==path.parent for y in info['siblings']):continue
        if path.is_absolute() or '..' in path.parts or '\\' in name:raise ValueError('Unsafe repository path')
        if name.startswith('.') or path.suffix.lower() not in {'.json','.safetensors','.bin','.pt','.pth','.model','.txt','.yaml','.yml','.tiktoken','.bpe','.npz'}:continue
        result.append(dict(name=name,url=f'https://huggingface.co/{repo}/resolve/{rev}/'+urllib.parse.quote(name),size=x.get('size'),sha256=(x.get('lfs') or {}).get('sha256')))
    if not any(x['name'].endswith(('.safetensors','.bin','.pt','.pth')) for x in result):raise RuntimeError('No checkpoint files returned by the source repository.')
    result.extend(entry.get('extra_files',[]))
    return save_plan(with_sizes(result))

def manifest_path(model,voice):
    from .services import model_folder
    return model_folder(model,voice)/'.avs-manifest.json'

def valid_file(path,item):
    return path.is_file() and path.stat().st_size>0 and (item.get('size') is None or path.stat().st_size==item['size'])

def downloaded(model,voice):
    from .services import model_folder,voice_files
    folder=model_folder(model,voice); mf=manifest_path(model,voice)
    if mf.is_file():
        try:
            items=json.loads(mf.read_text());return bool(items) and all(valid_file(folder/x['name'],x) for x in items)
        except (ValueError,OSError,KeyError):return False
    files=voice_files(model,voice)
    return bool(files) and all(valid_file(folder/x['name'],x) for x in files) and not models()[model].get('snapshot')

def download_files(model,voice,items,progress,cancel):
    from .services import model_folder
    folder=model_folder(model,voice)
    from .weight_cache import reuse_weights
    reuse_weights(folder,items,progress,lambda:check_cancel(cancel))
    result=downloads.parallel_set(folder,items,progress,lambda:check_cancel(cancel),valid_file,sha256)
    (folder/'.avs-download-plan.json').unlink(missing_ok=True)
    return result


def prepare_auxiliary(model, voice, log, cancel=None):
    from .services import model_folder
    folder=model_folder(model,voice);py=engine_python(model)
    if (folder/'.avs-aux-complete').is_file():return
    if model=='indextts2':
        run([py,'-c','from indextts.utils.model_download import ensure_models_available;import sys;ensure_models_available(sys.argv[1])',folder],log,cancel,env(False))
    elif model=='dia':
        run([py,'-c','from transformers import AutoProcessor;import sys;AutoProcessor.from_pretrained(sys.argv[1])',folder],log,cancel,env(False))
    if model in ('indextts2','dia'):
        (folder/'.avs-aux-complete').write_text('Prepared; real inference still required.',encoding='utf-8')


def ensure_runtime_and_tools(log,cancel=None):
    """Python plus the Microsoft C++ build tools and CMake (some voice engines need them to build one of their parts)."""
    while not INSTALL_LOCK.acquire(timeout=.2):check_cancel(cancel)
    try:
        python=ensure_runtime(log,cancel)
        from . import autoresolve
        if sys.platform=='darwin':
            if xcode_tools_present():log('The Apple developer tools (compiler and Git) are already installed.')
            else:
                log('Asking macOS to install the Apple developer tools. A window opens: click Install and wait until it finishes. Python is ready; the main voices work without them.')
                ask_xcode_tools(log)
        elif os.name=='nt':
            if autoresolve.build_tools_present():log('Microsoft C++ build tools and CMake are already installed.')
            else:
                check_cancel(cancel)
                log('Installing the Microsoft C++ build tools and CMake. This is a large download and Windows may ask for permission.')
                ok,message=autoresolve.install_build_tools(log)
                log(message if ok else message+' Python is ready; English voices work without them.')
        return python
    finally:INSTALL_LOCK.release()

def ensure_runtime_serial(log,cancel=None):
    while not INSTALL_LOCK.acquire(timeout=.2):check_cancel(cancel)
    try:return ensure_runtime(log,cancel)
    finally:INSTALL_LOCK.release()
