"""Protect the PC: wait while the graphics card is too hot or memory is almost full, instead of pushing on."""
from __future__ import annotations
import shutil
import subprocess
import time

COOL_MARGIN = 5          # degrees below the limit before work continues
RAM_LIMIT = 97       # computer memory %. Graphics memory is no longer a reason to wait: the voice engine handles it itself and falls back to the processor.
MAX_WAIT = 45        # default for "Wait at most" (seconds). Settings: 10 seconds to 5 minutes. Protection off = it never waits.
MIN_SETTING, MAX_SETTING = 10, 300
GRACE_SECONDS = 600  # after one wait ran out, the software does not wait again for 10 minutes (a card that stays warm must not hold up every part)
_grace = {"until": 0.0}


def reset_grace() -> None:
    _grace["until"] = 0.0


def wait_seconds(store) -> int:
    """How long the software may wait before a part: the saved choice, 10 to 300 seconds. 0 when protection is off."""
    if not store.setting("guard_on", True):
        return 0
    try:
        return max(MIN_SETTING, min(MAX_SETTING, int(store.setting("guard_wait", MAX_WAIT))))
    except (TypeError, ValueError):
        return MAX_WAIT


def smi_path():
    """The NVIDIA tool wherever Windows 10/11 or Linux put it (it is often not in PATH)."""
    import os
    from pathlib import Path
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        for base in (os.environ.get("WINDIR", "C:/Windows"), os.environ.get("ProgramFiles", "C:/Program Files")):
            for rel in ("System32/nvidia-smi.exe", "NVIDIA Corporation/NVSMI/nvidia-smi.exe"):
                if (Path(base) / rel).is_file():
                    return str(Path(base) / rel)
    return None
_cache = {"time": 0.0, "value": None}


class TooHot(RuntimeError):
    """The PC did not become safe to work on in time."""


def gpu_state():
    """(temperature C, graphics memory %) of the hottest card, or (None, None). Cached for 2 seconds."""
    if time.monotonic() - _cache["time"] < 2.0:
        return _cache["value"] or (None, None)
    value = (None, None)
    tool = smi_path()
    if tool:
        try:
            out = subprocess.run([tool, "--query-gpu=temperature.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=4, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if out.returncode == 0:
                temps, mems = [], []
                for line in out.stdout.strip().splitlines():
                    t, used, total = [float(x) for x in line.split(",")]
                    temps.append(t)
                    mems.append(100 * used / total if total else 0)
                if temps:
                    value = (max(temps), max(mems))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    _cache.update(time=time.monotonic(), value=value)
    return value


def ram_percent():
    try:
        import psutil
        return psutil.virtual_memory().percent
    except Exception:
        return None


def reason(store, recovering: bool = False, gpu=None, ram=None) -> str:
    """Why work should wait right now ('' when it is safe). 'recovering' demands a cooler card before continuing."""
    if not store.setting("guard_on", True):
        return ""
    temp, vram = gpu if gpu is not None else gpu_state()
    limit = int(store.setting("guard_temp", 88)) - (COOL_MARGIN if recovering else 0)
    if temp is not None and temp >= limit:
        return f"the graphics card is hot ({temp:.0f}°C)"
    used = ram if ram is not None else ram_percent()
    if used is not None and used >= RAM_LIMIT - (3 if recovering else 0):
        return "computer memory is almost full"
    return ""


def wait_until_safe(store, log, should_stop, sleep=time.sleep, max_wait: int | None = None, check=reason) -> bool:
    """Block while unsafe. Returns True to carry on, False if the person stopped or paused meanwhile."""
    waited, announced = 0, False
    if max_wait is None:
        max_wait = wait_seconds(store)
    if max_wait <= 0:                       # protection is off: never wait
        return not should_stop()
    while True:
        if should_stop():
            return False
        why = check(store, announced)
        if why and not announced and time.monotonic() < _grace["until"]:
            return True                      # already waited once recently: carry on
        if not why:
            _grace["until"] = 0.0
            if announced:
                log("Protection: safe again, continuing.")
            return True
        if not announced:
            log(f"Protection: {why}. Waiting until it is safe. Nothing is lost.")
            announced = True
        if waited >= max_wait:
            # Never sit idle for half an hour: carry on. The engine switches to the processor by itself if the card cannot cope.
            log(f"Protection: {why} for {max_wait} seconds. Continuing carefully so the voice-over is not held up "
                f"(it will not wait again for {GRACE_SECONDS // 60} minutes).")
            _grace["until"] = time.monotonic() + GRACE_SECONDS
            return True
        sleep(5)
        waited += 5
