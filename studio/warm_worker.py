"""Bounded local subprocess reuse. No external server or open network port."""
import atexit,json,queue,threading,time,uuid,subprocess
from . import external
_REG=threading.RLock()
_IDLE=300

class _Slot:
    """One loaded voice engine. Several slots let several parts be made at the same time."""
    def __init__(self):
        self.lock=threading.RLock();self.worker=None;self.key=None;self.timer=None

_SLOTS={0:_Slot()}

def _slot(index):
    with _REG:return _SLOTS.setdefault(index,_Slot())

def _slots():
    with _REG:return list(_SLOTS.values())

def slot_worker(index=0):
    return _slot(index).worker

def set_idle(seconds):
    """How long an unused voice engine stays loaded (the app allows 2 to 6 minutes)."""
    global _IDLE
    _IDLE=max(120,min(360,int(seconds)))

class WarmTransportError(RuntimeError): pass

def _stop_slot(sl):
    if sl.timer:sl.timer.cancel();sl.timer=None
    worker=sl.worker;sl.worker=None;sl.key=None
    if worker:worker.close()

def _stop():
    for sl in _slots():_stop_slot(sl)

def kill_now():
    """End every voice engine immediately, without waiting for the part it is making (used by Stop)."""
    for sl in _slots():
        worker=sl.worker
        if worker is not None and worker.p.poll() is None:
            try:worker.p.kill()
            except Exception:pass

def gpu_pids():
    """Process ids of the loaded voice engines that work on the graphics card."""
    out=[]
    for sl in _slots():
        w=sl.worker
        if w is not None and getattr(w,'device','')=='cuda' and w.p.poll() is None:out.append(w.p.pid)
    return out

def release():
    for sl in _slots():
        with sl.lock:_stop_slot(sl)

def _profile(model):
    from .core import models
    return models().get(model,{}).get("profile")

def release_profile(model):
    for sl in _slots():
        k=sl.key
        if k and _profile(k[1])==_profile(model):
            with sl.lock:
                if sl.key and _profile(sl.key[1])==_profile(model):_stop_slot(sl)

def release_model(model):
    for sl in _slots():
        k=sl.key
        if k and k[1]==model:
            with sl.lock:
                if sl.key and sl.key[1]==model:_stop_slot(sl)

def _release_idle_slot(sl):
    if sl.lock.acquire(blocking=False):
        try:_stop_slot(sl)
        finally:sl.lock.release()

def release_idle():
    for sl in _slots():_release_idle_slot(sl)

class Worker:
    def __init__(self,cmd,env):
        self.p=external.popen(cmd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',bufsize=1,priority='work')
        self.lines=queue.Queue(maxsize=2048);self.closed=threading.Event()
        def read():
            for line in self.p.stdout:
                while not self.closed.is_set():
                    try:self.lines.put(line.rstrip(),timeout=.2);break
                    except queue.Full:pass
            if not self.closed.is_set():
                try:self.lines.put(None,timeout=.5)
                except queue.Full:pass
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
    def close(self):
        self.closed.set()
        if self.p.poll() is None:
            self.p.kill();self.p.wait(timeout=10)
        self.reader.join(timeout=2)
        for stream in (self.p.stdin,self.p.stdout):
            if stream:stream.close()
    def generate(self,job,log,timeout=3600):
        ident=uuid.uuid4().hex
        try:self.p.stdin.write(json.dumps({'id':ident,'job':job},ensure_ascii=True)+'\n');self.p.stdin.flush()
        except (OSError,ValueError) as exc:raise WarmTransportError('Voice worker pipe closed') from exc
        end=time.monotonic()+timeout;tail=[]
        while time.monotonic()<end:
            try:line=self.lines.get(timeout=.2)
            except queue.Empty:
                if self.p.poll() is not None:break
                continue
            if line is None:break
            if line.startswith('AVS_RESULT '):
                try:result=json.loads(line[len('AVS_RESULT '):])
                except ValueError as exc:raise WarmTransportError('Invalid worker response') from exc
                if not isinstance(result,dict) or result.get('id')!=ident:raise WarmTransportError('Unexpected worker response')
                if not result.get('ok'):raise RuntimeError(result.get('error','Generation failed'))
                return
            if line:tail=(tail+[line])[-25:];log(line[:4000])
        raise WarmTransportError('Voice worker stopped or timed out.\n'+'\n'.join(tail))

def run(cmd,job,env,log,timeout=3600,slot=0):
    sl=_slot(slot)
    opts=job.get('options',{})
    key=(tuple(map(str,cmd)),job['model'],job['models_dir'],job['voice'] if job['model']=='piper' else '',opts.get('device'),opts.get('cpu_threads'),opts.get('gpu_memory_fraction'),bool(opts.get('emotion_text')))
    if not sl.lock.acquire(timeout=90 if slot==0 else 0):raise WarmTransportError("Another independent audio job owns the warm worker")
    try:
        if sl.timer:sl.timer.cancel();sl.timer=None
        try:
            if sl.worker is None or sl.key!=key or sl.worker.p.poll() is not None:
                _stop_slot(sl);sl.worker=Worker(cmd,{**env,'PYTHONIOENCODING':'utf-8'});sl.worker.device=opts.get('device','');sl.key=key;log('Loading voice engine (first request)')
            else:log('Reusing loaded voice engine')
            sl.worker.generate(job,log,timeout=timeout)
        except BaseException:
            _stop_slot(sl);raise
        sl.timer=threading.Timer(_IDLE,lambda s_=sl:_release_idle_slot(s_));sl.timer.daemon=True;sl.timer.start()
    finally:sl.lock.release()
atexit.register(release)
