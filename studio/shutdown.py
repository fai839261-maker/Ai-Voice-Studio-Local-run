"""Nothing keeps running after AI Voice Studio closes.

Three safety layers (each one works even if the others fail):
1. Windows "job": every helper the software starts (voice engines, installs, FFmpeg ...) is tied to the program.
   When the program ends for ANY reason (closed, crashed, killed in Task Manager) Windows ends them too.
2. kill_children(): at close the helpers are ended one by one, including everything they started.
3. A short timer ends the program itself, so a stuck background thread can never keep it alive.
"""
import atexit
import os
import subprocess
import sys
import threading

_JOB = None            # the Windows job handle; it must stay open for as long as the program lives
_KEEP = set()          # process ids that must survive (for example the new copy started by a restart)
_STARTED = False
_LEAVING = False


def install_job_object() -> bool:
    """Tie all helper processes to this program (Windows only). Returns True when it worked."""
    global _JOB
    if os.name != "nt" or _JOB is not None:
        return _JOB is not None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                           "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return False
        info = EXTENDED()
        # KILL_ON_JOB_CLOSE (0x2000): helpers end with the program.  BREAKAWAY_OK (0x800): a restart may start a free copy.
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return False
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            return False
        _JOB = job
        return True
    except Exception:
        return False


_CAP = {"percent": 100, "active": False}


def cpu_rate_bytes(percent: int) -> bytes:
    """The Windows structure that caps the processor use of a whole job (hard cap, in hundredths of a percent of ALL processors)."""
    import ctypes
    percent = max(1, min(100, int(percent)))

    class CPU_RATE(ctypes.Structure):
        _fields_ = [("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32)]

    info = CPU_RATE()
    if percent >= 100:
        info.ControlFlags = 0                            # no cap
    else:
        info.ControlFlags = 0x1 | 0x4                    # ENABLE | HARD_CAP
        info.CpuRate = percent * 100
    return bytes(info)


def set_cpu_cap(percent: int) -> bool:
    """Ask Windows to keep this program AND all its helpers together at or below `percent` of the whole processor.
    Returns True when Windows enforces it (False on other systems or when the job cannot be used)."""
    percent = max(1, min(100, int(percent)))
    if os.name != "nt":
        return False
    if _JOB is None and not install_job_object():
        return False
    if _CAP["percent"] == percent and _CAP["active"]:
        return percent < 100
    try:
        import ctypes
        from ctypes import wintypes
        data = cpu_rate_bytes(percent)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        buf = ctypes.create_string_buffer(data, len(data))
        ok = bool(kernel.SetInformationJobObject(_JOB, 15, buf, len(data)))     # 15 = JobObjectCpuRateControlInformation
    except Exception:
        ok = False
    _CAP["percent"], _CAP["active"] = percent, ok
    return ok and percent < 100


def kill_orphans() -> int:
    """End voice-engine helpers left behind by an earlier run of this software (for example a version that did not clean up).
    Only processes that run this software's own runner script are ended, and never ones that belong to this window."""
    count = 0
    try:
        import psutil
        me = psutil.Process()
        mine = {me.pid, *(c.pid for c in me.children(recursive=True))}
        for proc in psutil.process_iter(["pid", "cmdline", "name"]):
            if proc.info["pid"] in mine:
                continue
            args = [str(a) for a in (proc.info.get("cmdline") or [])]
            runner = next((a for a in args if a.replace("\\", "/").lower().endswith("runtime/runner.py")), None)
            if runner and any(a in ("--worker", "--payload") for a in args):
                try:
                    proc.kill()
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


def keep(pid: int) -> None:
    """A process that must not be ended when this program closes."""
    _KEEP.add(int(pid))


def spawn_free(command, **kwargs):
    """Start a program that must live on after this one closes (the restart). It leaves the job, so it is not ended with us."""
    if os.name == "nt":
        flags = kwargs.pop("creationflags", 0) | 0x00000008 | 0x00000200          # detached, own process group
        try:
            proc = subprocess.Popen(command, creationflags=flags | 0x01000000, **kwargs)   # 0x01000000: leave the job
        except OSError:
            proc = subprocess.Popen(command, creationflags=flags, **kwargs)
    else:
        proc = subprocess.Popen(command, start_new_session=True, **kwargs)     # its own session: it lives on after this copy closes
    keep(proc.pid)
    return proc


def kill_children() -> int:
    """End every helper process and everything it started. Returns how many were ended."""
    count = 0
    try:
        import psutil
        me = psutil.Process()
        family = me.children(recursive=True)
        spared = set()
        for pid in _KEEP:
            spared.add(pid)
            try:
                spared.update(c.pid for c in psutil.Process(pid).children(recursive=True))
            except Exception:
                pass
        victims = [p for p in family if p.pid not in spared]
        for p in victims:
            try:
                p.kill()
                count += 1
            except Exception:
                pass
        try:
            psutil.wait_procs(victims, timeout=2)
        except Exception:
            pass
    except Exception:
        pass
    return count


def _stop_engines() -> None:
    try:
        from . import warm_worker
        warm_worker.kill_now()
    except Exception:
        pass
    try:
        from . import load_control
        load_control._resume_all()
    except Exception:
        pass


def cleanup() -> None:
    """Stop the voice engines and end all helper processes. Safe to call more than once."""
    _stop_engines()
    kill_children()


def leave_now(delay: float = 0.4) -> None:
    """Clean up and end the program itself shortly after, even if a background thread is stuck."""
    global _LEAVING
    if os.environ.get("AVS_NO_PROMPTS") or os.environ.get("AVS_NO_EXIT"):      # tests keep their own process
        return
    cleanup()
    if _LEAVING:
        return
    _LEAVING = True

    def finish():
        cleanup()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    timer = threading.Timer(delay, finish)
    timer.daemon = True
    timer.start()


def install(app=None) -> None:
    """Call once when the window is built."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    install_job_object()
    kill_orphans()                  # helpers left behind by an older run would otherwise keep using the processor
    atexit.register(cleanup)
    try:
        if app is None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: leave_now(0.6))
    except Exception:
        pass
