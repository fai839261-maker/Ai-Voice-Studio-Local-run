"""Open with administrator rights without a permission question at every start.

How it works (Windows only, installed program only):
- The FIRST time, Windows asks once (the normal permission window). The copy that is then running with administrator rights saves a
  Windows scheduled task "AI Voice Studio (admin start)" that starts this program with the highest rights for the same user.
- Every later start finds the task, runs it and closes itself; the administrator copy opens with no question.
- If the task is missing or fails, the normal one-time question is used again and the task is saved again.
- "--no-admin" starts the program normally. A standard user (no administrator account) is never asked.
Windows does not allow dragging files from Explorer into a window that runs as administrator; use the "Select file" buttons.
"""
import os
import subprocess
import sys
import tempfile

TASK_NAME = "AI Voice Studio (admin start)"
CREATE_NO_WINDOW = 0x08000000


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def split_token_user() -> bool:
    """True for an administrator account that is running with the limited (filtered) token, i.e. one that can be elevated."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        token = wintypes.HANDLE()
        kernel, advapi = ctypes.windll.kernel32, ctypes.windll.advapi32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return False
        try:
            kind, size = wintypes.DWORD(), wintypes.DWORD()
            if not advapi.GetTokenInformation(token, 18, ctypes.byref(kind), ctypes.sizeof(kind), ctypes.byref(size)):
                return False
            return kind.value == 3          # TokenElevationTypeLimited
        finally:
            kernel.CloseHandle(token)
    except Exception:
        return False


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def task_xml(exe: str, user: str) -> str:
    """The scheduled task: no trigger (it only runs when asked), highest rights, works on battery, no time limit, several copies allowed."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Starts AI Voice Studio with administrator rights without asking each time.</Description></RegistrationInfo>
  <Triggers />
  <Principals>
    <Principal id="Author">
      <UserId>{_xml_escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_escape(exe)}</Command>
      <Arguments>--task-launch</Arguments>
      <WorkingDirectory>{_xml_escape(os.path.dirname(exe))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def current_user() -> str:
    domain, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{name}" if domain and name else name


def _schtasks(args, timeout=20):
    return subprocess.run(["schtasks.exe", *args], capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)


def task_points_to(exe: str) -> bool:
    """True when the saved task exists and starts this very program file."""
    try:
        out = _schtasks(["/query", "/tn", TASK_NAME, "/xml"])
        return out.returncode == 0 and os.path.normcase(exe) in os.path.normcase(out.stdout.replace("&amp;", "&"))
    except Exception:
        return False


def create_task(exe: str) -> bool:
    """Save the task (needs administrator rights, so only the elevated copy calls this)."""
    path = None
    try:
        handle, path = tempfile.mkstemp(prefix="avs_task_", suffix=".xml")
        os.close(handle)
        with open(path, "w", encoding="utf-16") as f:
            f.write(task_xml(exe, current_user()))
        return _schtasks(["/create", "/tn", TASK_NAME, "/xml", path, "/f"], 30).returncode == 0
    except Exception:
        return False
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def run_task() -> bool:
    try:
        return _schtasks(["/run", "/tn", TASK_NAME]).returncode == 0
    except Exception:
        return False


def ask_windows(argv) -> bool:
    """The one-time Windows permission question. True when an elevated copy was started."""
    import ctypes
    from ctypes import wintypes
    shell = ctypes.windll.shell32.ShellExecuteW
    shell.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int)
    shell.restype = ctypes.c_void_p
    params = subprocess.list2cmdline([*argv[1:], "--elevated-launch"])
    old = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        result = shell(None, "runas", sys.executable, params, None, 1)
        return bool(result and result > 32)
    finally:
        if old is None:
            os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        else:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = old


def ensure_admin(argv=None, frozen=None, exe=None, hooks=None) -> bool:
    """Call first thing when the window is about to open. True = another (administrator) copy was started: close this one now.
    `hooks` lets tests replace the Windows calls."""
    argv = list(sys.argv if argv is None else argv)
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = exe or sys.executable
    h = {"is_admin": is_admin, "split": split_token_user, "points_to": task_points_to, "create": create_task, "run": run_task, "ask": ask_windows}
    h.update(hooks or {})
    if (os.name != "nt" and not hooks) or not frozen or "--no-admin" in argv:
        return False
    if h["is_admin"]():
        if not h["points_to"](exe):          # first administrator start (or the program moved): save the task for next time
            h["create"](exe)
        return False
    if not h["split"]():
        return False                         # a standard user cannot be elevated: start normally, never ask
    if "--elevated-launch" in argv or "--task-launch" in argv:
        return False                         # already tried: never loop
    if h["points_to"](exe) and h["run"]():
        return True                          # the saved task opens the administrator copy with no question
    return bool(h["ask"](argv))
