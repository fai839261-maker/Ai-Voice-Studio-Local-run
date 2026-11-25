"""Launch external executables without inheriting PyInstaller's DLL search path."""
import os,sys,subprocess,threading
from pathlib import Path
from contextlib import contextmanager
LOCK=threading.RLock()

def clean_env(env=None):
    result=dict(os.environ if env is None else env)
    bundle=getattr(sys,'_MEIPASS',None)
    for key in ('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QML2_IMPORT_PATH','QML_IMPORT_PATH'):
        result.pop(key,None)
    if bundle:
        base=os.path.normcase(os.path.abspath(bundle))
        def outside(p):
            try:return os.path.commonpath([base,os.path.normcase(os.path.abspath(p.strip('"')))])!=base
            except ValueError:return True
        result['PATH']=os.pathsep.join(p for p in result.get('PATH','').split(os.pathsep) if p and outside(p))
        for key in list(result):
            if key.startswith('_PYI_'):result.pop(key,None)
        result.pop('PYINSTALLER_RESET_ENVIRONMENT',None)
    return result

@contextmanager
def dll_scope():
    with LOCK:
        if os.name!='nt' or not getattr(sys,'frozen',False):
            yield;return
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetDllDirectoryW.argtypes=[ctypes.c_uint,ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.argtypes=[ctypes.c_wchar_p]
        buf=ctypes.create_unicode_buffer(32768)
        n=kernel.GetDllDirectoryW(len(buf),buf)
        if n>=len(buf):raise RuntimeError('DLL search path too long')
        if not kernel.SetDllDirectoryW(None):raise ctypes.WinError()
        try:yield
        finally:kernel.SetDllDirectoryW(buf.value if n else None)

def popen(cmd,**kwargs):
    # priority='background' (installs, checks) or 'work' (voice engines): lower Windows priority and thread limits
    # from the chosen load strength. None = leave the process exactly as before.
    kind=kwargs.pop('priority',None)
    kwargs['env']=clean_env(kwargs.get('env'))
    if kind:
        from . import load_control
        kwargs['env']=load_control.child_env(kwargs['env'],kind)
    # A launch with close_fds=True and no pipes hands work over to a program that must live on after this one closes
    # (an installer, the folder-move helper). It leaves the shutdown job and is never ended by the close-time clean-up.
    handover=bool(kwargs.get('close_fds')) and kwargs.get('stdout') is None
    if os.name=='nt':
        flags=kwargs.get('creationflags',0)|subprocess.CREATE_NO_WINDOW
        if kind:flags|=load_control.creation_flags(kind)
        kwargs['creationflags']=flags
    args=[str(x) for x in cmd]
    with dll_scope():
        if handover and os.name=='nt':
            try:proc=subprocess.Popen(args,**{**kwargs,'creationflags':kwargs['creationflags']|0x01000000})   # leave the job
            except OSError:proc=subprocess.Popen(args,**kwargs)
        else:proc=subprocess.Popen(args,**kwargs)
    if handover:
        from . import shutdown
        shutdown.keep(proc.pid)
    if kind:load_control.lower_priority(proc.pid,kind)
    return proc

def run(cmd,*,capture_output=False,timeout=None,check=False,**kwargs):
    if capture_output:kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    with popen(cmd,**kwargs) as proc:
        try:out,err=proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill();proc.communicate();raise
        r=subprocess.CompletedProcess(cmd,proc.returncode,out,err)
        if check:r.check_returncode()
        return r
