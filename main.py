"""Desktop entrypoint and packaged-resource self-check."""
import sys


def self_check():
    import json, os
    from pathlib import Path
    from studio.core import bundled,root,catalog,Store,APP_VERSION
    files=["resources/catalog.json","runtime/runner.py","runtime/adapters/warm_cache.py","runtime/adapters/language.py","runtime/adapters/piper_adapter.py",
           "runtime/adapters/kokoro_adapter.py","runtime/adapters/onnx_inputs.py","runtime/adapters/vits_adapter.py","runtime/adapters/chatterbox_adapter.py",
           "runtime/adapters/f5_adapter.py","runtime/adapters/qwen_adapter.py","runtime/adapters/turbo_adapter.py","runtime/adapters/voxcpm_adapter.py","runtime/adapters/openf5_adapter.py","runtime/adapters/indextts_adapter.py","runtime/adapters/dia_adapter.py","runtime/adapters/xtts2_adapter.py","runtime/adapters/timing.py","runtime/adapters/styletts2_adapter.py","runtime/adapters/gpu_choice.py","runtime/adapters/fishs2_adapter.py","runtime/whisper_check.py","resources/avs.ico","runtime/requirements-base-cpu.txt","runtime/requirements-base-cuda.txt"]
    missing=[f for f in files if not bundled(f).is_file()]
    Store()
    if getattr(sys,"frozen",False):
        if os.name=="nt" and not bundled("resources/AVS_Move_Helper.exe").is_file():missing.append("standalone Move folder helper")
        if not bundled("studio_src/core.py").is_file():missing.append("program sources for update files (studio_src)")
        try:
            import imageio_ffmpeg
            if not Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file():missing.append("bundled FFmpeg executable")
        except Exception:missing.append("bundled FFmpeg executable")
    import ast
    runner=bundled("runtime/runner.py")
    if runner.is_file():
        try: ast.parse(runner.read_text(encoding="utf-8"))
        except SyntaxError: missing.append("runtime/runner.py (invalid syntax)")
    state={"result":"PASS" if not missing else "FAIL", "version":APP_VERSION, "missing":missing,"data_dir":str(root()),"model_count":len(catalog()["models"])}
    (root()/"packaged-self-check.json").write_text(json.dumps(state,indent=2),encoding="utf-8")
    return 0 if not missing else 3


def request_gui_elevation():
    """Ask Windows for elevation once for GUI runs only; normal launch remains possible."""
    import os
    if os.name != "nt" or not getattr(sys, "frozen", False) or "--elevated-launch" in sys.argv or "--task-launch" in sys.argv or "--no-admin" in sys.argv:
        return False
    import ctypes
    from ctypes import wintypes
    # The installer helper and resource self-check deliberately never enter this branch.
    if ctypes.windll.shell32.IsUserAnAdmin():
        return False
    # A standard user can use the per-user runtime without supplying a different admin account.
    token = wintypes.HANDLE()
    kernel = ctypes.windll.kernel32
    advapi = ctypes.windll.advapi32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        return False
    try:
        kind = wintypes.DWORD()
        size = wintypes.DWORD()
        if not advapi.GetTokenInformation(token, 18, ctypes.byref(kind), ctypes.sizeof(kind), ctypes.byref(size)):
            return False
        if kind.value != 3:  # TokenElevationTypeLimited: same-account split admin token
            return False
    finally:
        kernel.CloseHandle(token)
    shell = ctypes.windll.shell32.ShellExecuteW
    shell.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int)
    shell.restype = ctypes.c_void_p
    import subprocess
    params = subprocess.list2cmdline([*sys.argv[1:], "--elevated-launch"])
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


def main():
    if "--self-check" in sys.argv:
        return self_check()
    if "--prepare-runtime" in sys.argv or "--prepare-runtime-offline" in sys.argv:
        # Avoid the frozen program's DLL directory leaking into external Python/WinGet.
        import os
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        from studio.preflight import prepare_runtime
        return prepare_runtime(self_check,offline="--prepare-runtime-offline" in sys.argv)
    import avs_boot
    avs_boot.activate()      # applies a prepared update / go-back / restore, then loads the update layer first
    try:
        return run_gui()
    except Exception:
        import os,subprocess,traceback
        if os.environ.get("AVS_OVERLAY_DIR"):
            # An update layer that cannot start must never lock the person out: put it aside and start the original program.
            avs_boot.quarantine("The update could not start:\n"+traceback.format_exc()[-1500:])
            subprocess.Popen([sys.executable]+(sys.argv[1:] if getattr(sys,"frozen",False) else sys.argv))
            return 0
        raise


def _wait_for_previous_window(acquire):
    """After Restart now the old window needs a moment to close and release its lock."""
    import time
    end=time.time()+20
    while time.time()<end:
        if acquire():return True
        time.sleep(0.4)
    return False


def run_gui():
    from studio.migration import block_launch, acquire_gui_lock
    if block_launch():
        return 0
    try:
        from studio.elevate import ensure_admin     # no permission question at every start: a saved task opens the administrator copy
        if ensure_admin():
            return 0
    except ImportError:
        if request_gui_elevation():
            return 0
    if not (acquire_gui_lock() or ("--restarted" in sys.argv and _wait_for_previous_window(acquire_gui_lock))):
        if os.name=="nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,"AI Voice Studio is already open. Use the existing window.","AI Voice Studio",0x40)
        else:
            print("AI Voice Studio is already open. Use the existing window.")
        return 0
    if block_launch():
        return 0
    from PySide6.QtWidgets import QApplication
    from studio.ui_v5 import StudioWindow
    from PySide6.QtGui import QIcon
    from studio.core import bundled
    import os
    if os.name=="nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AI.Voice.Studio.Desktop")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QGuiApplication
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app=QApplication(sys.argv)
    app.setStyle("Fusion")
    if sys.platform!="darwin":      # a Mac keeps its own system font
        base_font=QFont("Segoe UI");base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting);base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        app.setFont(base_font)
    app.setApplicationName("AI Voice Studio")
    app.setWindowIcon(QIcon(str(bundled("resources/avs.ico"))))
    w=StudioWindow()
    w.show()
    from PySide6.QtCore import QTimer
    import avs_boot
    QTimer.singleShot(4000,avs_boot.mark_good)   # the window opened: an update layer, if any, works
    return app.exec()

if __name__=='__main__':
    sys.exit(main())
