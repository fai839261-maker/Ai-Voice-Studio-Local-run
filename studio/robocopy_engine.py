"""Optional Windows-native staging accelerator. Never moves or deletes sources.

The transactional Python engine still verifies every file and repairs failures.
No shell, PATH lookup, destructive flags, ACL copy, or unverified publication.
"""
from __future__ import annotations
import ctypes
import os
import subprocess
import time
from pathlib import Path


def executable():
    if os.name != 'nt':
        return None
    # Resolve from the Windows system directory, never the current directory/PATH.
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [w.LPWSTR, w.UINT]
    kernel.GetSystemDirectoryW.restype = w.UINT
    buf = ctypes.create_unicode_buffer(32768)
    n = kernel.GetSystemDirectoryW(buf, len(buf))
    if not n or n >= len(buf):
        return None
    exe = Path(buf.value) / 'robocopy.exe'
    return exe if exe.is_file() else None


def thread_count():
    return 4 if (os.cpu_count() or 2) <= 4 else 8


def arguments(exe, src, dst, threads=None):
    n = max(1, min(16, int(threads if threads is not None else thread_count())))
    return [str(exe), str(src), str(dst), '/E', '/Z', f'/MT:{n}',
            '/COPY:DAT', '/DCOPY:DA', '/R:2', '/W:2', '/XJ', '/SL',
            '/NP', '/NFL', '/NDL']


class KillOnCloseJob:
    """Windows kills our copy child when the helper is unexpectedly terminated.
    If the OS cannot create/assign a job, use the native Python copy engine.
    """
    def __init__(self):
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                        ('PerJobUserTimeLimit', ctypes.c_longlong), ('LimitFlags', w.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t), ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', w.DWORD), ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount',
                        'OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', Counters),
                        *[(n, ctypes.c_size_t) for n in ('ProcessMemoryLimit','JobMemoryLimit',
                          'PeakProcessMemoryUsed','PeakJobMemoryUsed')]]
        k = self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        k.CreateJobObjectW.restype = w.HANDLE
        k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k.SetInformationJobObject.restype = w.BOOL
        k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        k.AssignProcessToJobObject.restype = w.BOOL
        k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error()); self.close(); raise error

    def assign(self, proc):
        if not self.k.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle); self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def copy_tree(exe, src, dst, journal, progress, log):
    """Run a bounded-retry, restartable transfer, with graphical status only."""
    cmd = arguments(exe,src,dst)
    report = Path(journal).parent / 'robocopy.log'
    start = time.monotonic()
    log(f'Robocopy staging: {src} -> {dst} ({cmd[5]}). Full log: {report}')
    with KillOnCloseJob() as job, report.open('ab') as out:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                    cwd=str(Path(journal).parent), close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        try:
            job.assign(proc)
            while True:
                try:
                    returncode = proc.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    progress('Robocopy — transferring files',0,0,
                             f'{src.name} | {int(time.monotonic()-start)}s elapsed | verifying after transfer')
        finally:
            # Never allow the fallback to write while this copy is still active.
            if proc.poll() is None:
                proc.kill();proc.wait()
    log(f'Robocopy exit code {returncode}: {src}')
    return 0 <= returncode < 8


def safe_groups(root, dirs, files, checkpoints, is_link):
    """Select maximal ordinary subtrees; linked paths stay with Python.

    Existing Python partials also stay with Python to avoid duplicate space and
    preserve their verified-prefix resume behavior. Empty links are included.
    """
    root=Path(root); bad=set(); child_dirs={}; stats={}
    def mark(rel):
        bad.add(rel);bad.update(rel.parents)
    for rel in dirs:
        if rel != Path('.'):
            child_dirs.setdefault(rel.parent, []).append(rel)
        if is_link(root/rel): mark(rel)
    for rel,actual,size,_ in files:
        if actual != root/rel or is_link(root/rel) or rel in checkpoints:
            mark(rel)
        for d in rel.parents:
            count,bytes_=stats.get(d,(0,0));stats[d]=(count+1,bytes_+size)
    def visit(rel):
        # A link itself must never be passed as a Robocopy source, even if an
        # ordinary-looking child exists below it.
        if is_link(root/rel): return
        if rel not in bad:
            count,size=stats.get(rel,(0,0))
            if count >= 32 or size >= 64*1024**2:
                yield rel
            return
        for child in child_dirs.get(rel,[]):
            yield from visit(child)
    return list(visit(Path('.')))


def accelerate(root, base, dirs, files, checkpoints, journal, progress, log, is_link):
    """Best-effort pre-copy only; caller must verify or repair every file."""
    try:
        exe=executable()
    except (OSError,AttributeError) as exc:
        log(f'Robocopy unavailable: {exc}; using verified Python transfer.');return
    if exe is None:
        log('Robocopy unavailable; using verified Python transfer.');return
    # Reject unexpected staging links before handing a directory to an external
    # copier. Do not traverse junctions or symlinks into other locations.
    for path,subdirs,names in os.walk(base,followlinks=False):
        for name in subdirs+names:
            if is_link(Path(path)/name):
                raise RuntimeError(f'Unexpected staging link: {Path(path)/name}')
    groups=safe_groups(root,dirs,files,checkpoints,is_link)
    for rel in groups:
        src,dst=Path(root)/rel,Path(base)/rel
        progress('Robocopy — transferring files',0,0,str(rel))
        try:
            if not copy_tree(exe,src,dst,journal,progress,log):
                log('Robocopy reported incomplete transfer; Python will verify and repair remaining files.')
                # Unsupported flags or failing storage shouldn't trigger dozens
                # of repeated native copy attempts for other subtrees.
                break
        except (OSError,RuntimeError,AttributeError) as exc:
            log(f'Robocopy could not run: {exc}; using verified Python transfer.')
            break
