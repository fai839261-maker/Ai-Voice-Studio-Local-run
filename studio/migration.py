"""UI-side staging for the independently packaged Move folder helper."""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

from . import external
from .core import bundled
from .move_engine import atomic_json, make_plan, next_destination, read_json


def _base():
    from .platform_info import local_base
    return local_base()


def control_dir():
    # Deliberately outside AVS and the installation. Survives a failed move.
    return _base()/'AVS-Move'


def log_path():
    return _base()/'AVS-migration.log'


def active_path():
    return control_dir()/'active.json'


def stable_app(exe):
    """Prefer the installer-recorded path, even if launched from the new drive."""
    app = Path(exe).absolute().parent
    physical = app.resolve(strict=True)
    candidates = [_base()/'Programs'/'AI Voice Studio']
    try:
        state = read_json(control_dir()/'last-move.json')
        candidates.insert(0, Path(state['app']))
    except (OSError,ValueError,KeyError):
        pass
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{AC43A22B-744E-4969-8CE0-4D9C4A4BDF5A}_is1',0,winreg.KEY_READ|view) as key:
                        candidates.insert(0,Path(winreg.QueryValueEx(key,'InstallLocation')[0]))
                except OSError:
                    pass
    for candidate in candidates:
        try:
            if candidate.is_dir() and candidate.resolve(strict=True)==physical:
                return candidate.absolute()
        except OSError:
            pass
    return app


RECOVERY_RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
RECOVERY_RUN_NAME='AVS-PendingMove'

def register_recovery(helper):
    # HKCU Run survives a power loss even if the app executable temporarily
    # cannot be reached during the junction switch. Removed after finalization.
    import winreg, subprocess
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RECOVERY_RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key,RECOVERY_RUN_NAME,0,winreg.REG_SZ,subprocess.list2cmdline([str(helper),'--recover']))


def remove_recovery(helper):
    import winreg, subprocess
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RECOVERY_RUN_KEY,0,winreg.KEY_QUERY_VALUE|winreg.KEY_SET_VALUE) as key:
            value,_=winreg.QueryValueEx(key,RECOVERY_RUN_NAME)
            if value==subprocess.list2cmdline([str(helper),'--recover']):
                winreg.DeleteValue(key,RECOVERY_RUN_NAME)
    except FileNotFoundError:pass


def start(destination,exe,data):
    if os.name!='nt' or not getattr(sys,'frozen',False):
        raise RuntimeError('Move folder is available in the installed Windows application.')
    if active_path().exists():
        raise RuntimeError(f'A move is already pending. See the recovery instructions in {log_path()} or restart the move helper in {control_dir()}.')
    exe=Path(exe)
    app=stable_app(exe)
    if not any(app.glob('unins*.exe')):
        raise RuntimeError('Move folder requires an installed copy of AI Voice Studio.')
    token=uuid.uuid4().hex[:12]
    plan=make_plan(app,Path(data),Path(destination),token)
    ctrl=control_dir();ctrl.mkdir(parents=True,exist_ok=True)
    from .move_engine import overlap
    if any(overlap(ctrl.resolve(),Path(r[k])) for r in plan['rows'] for k in ('logical','physical')):
        raise RuntimeError('Migration recovery folder overlaps managed data.')
    if overlap(ctrl.resolve(),Path(plan['destination'])):
        raise RuntimeError('Select a destination outside the AVS recovery folder.')
    run=ctrl/f'move-{token}';run.mkdir()
    helper=run/'AVS_Move_Helper.exe'
    pointer_created=False
    try:
        shutil.copy2(bundled('resources/AVS_Move_Helper.exe'),helper)
        import psutil
        plan.update(pid=os.getpid(),parent_created=psutil.Process().create_time(),exe=exe.name,log=str(log_path()),control=str(ctrl),run=str(run))
        atomic_json(run/'plan.json',plan)
        # Exclusive pointer acquisition: two simultaneous GUI sessions cannot move.
        with active_path().open('x',encoding='utf-8') as f:
            pointer_created=True
            json.dump({'helper':str(helper),'run':str(run)},f)
            f.flush();os.fsync(f.fileno())
        register_recovery(helper)
        with log_path().open('a',encoding='utf-8') as f:
            f.write(f'\nMove requested. Destination: {destination}\nRecovery helper: {helper}\n')
        external.popen([helper],cwd=run,close_fds=True)
    except BaseException:
        if pointer_created:
            try:remove_recovery(helper)
            except OSError:pass
            active_path().unlink(missing_ok=True)
        shutil.rmtree(run,ignore_errors=True)
        raise


def block_launch():
    """Prevent a second GUI instance from opening state while paths are switching."""
    if os.name!='nt' or not getattr(sys,'frozen',False) or not active_path().exists():
        return False
    import ctypes
    try:
        helper=Path(read_json(active_path())['helper'])
        message=(f'A Move folder operation is pending. If its progress window is still open, let it finish.\n\n'
                 f'If it stopped unexpectedly, click Yes to open the recovery helper.\n\n{helper}')
        if ctypes.windll.user32.MessageBoxW(None,message,'AI Voice Studio — Move folder',0x24)==6 and helper.is_file():
            external.popen([helper,'--recover'],cwd=helper.parent,close_fds=True)
    except Exception:
        ctypes.windll.user32.MessageBoxW(None,f'A move is pending. See {log_path()} and {control_dir()} before restarting.','AI Voice Studio',0x10)
    return True


# Hold the per-user GUI lock until Windows closes the process handle. Keeping it
# outside the managed roots avoids locking a file that the move must relocate.
_gui_lock=None

def acquire_gui_lock():
    global _gui_lock
    if not getattr(sys,'frozen',False):
        return True
    if _gui_lock is not None:return True
    if os.name!='nt':               # Mac / Linux: an advisory lock the system drops when the program ends
        import fcntl
        directory=control_dir();directory.mkdir(parents=True,exist_ok=True)
        handle=(directory/'gui.lock').open('a+b')
        try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close();return False
        _gui_lock=handle
        return True
    import msvcrt
    directory=control_dir();directory.mkdir(parents=True,exist_ok=True)
    handle=(directory/'gui.lock').open('a+b')
    try:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        handle.close();return False
    _gui_lock=handle
    return True
