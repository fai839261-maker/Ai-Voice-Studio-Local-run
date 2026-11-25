"""One-click check of everything the software depends on, with plain-language results."""
from __future__ import annotations
import os
import shutil
import sys
from dataclasses import dataclass

from .core import root, models


@dataclass
class Check:
    name: str
    status: str      # 'ok' | 'warn' | 'bad' | 'info'
    detail: str
    fix: str = ""    # 'python' | 'engines' | ''
    keys: tuple = ()  # engine ids for fix == 'engines'


def managed_tools_ok() -> bool:
    from . import managed
    return managed.xcode_tools_present()


def system_checks() -> list[Check]:
    from . import managed, audio_fx
    out: list[Check] = []
    if sys.platform == "darwin":
        from . import platform_info as pi
        fam = pi.chip_family()
        out.append(Check("Mac", "ok" if fam.startswith("m") else "info", f"{pi.chip_name()} · macOS {pi.macos_version()}" + ("" if fam.startswith("m") else " · Intel: Piper, Kokoro and the Word checker work; the cloning models need an Apple Silicon Mac")))
        if pi.rosetta():
            out.append(Check("Running under Rosetta", "warn", "This is the Intel version running on an M-series Mac. Use the Apple Silicon (arm64) version for full speed."))
    else:
        out.append(Check("Windows 64-bit", "ok" if (os.name == "nt" and sys.maxsize > 2**32) else "warn",
                         "64-bit Windows" if os.name == "nt" else "This check is made for Windows"))
    py = None
    try:
        py = managed.runtime_python()
    except Exception as exc:  # registry/launcher problems must not stop the report
        out.append(Check("Python 3.11", "bad", f"Could not look for Python: {exc}", "python"))
    else:
        if py:
            out.append(Check("Python 3.11", "ok", str(py)))
        else:
            out.append(Check("Python 3.11", "bad", "Not found. The Fix button installs it (Homebrew or the official installer)." if sys.platform == "darwin" else "Not found. The Fix button installs it with WinGet.", "python"))
    if sys.platform == "darwin":
        out.append(Check("Apple developer tools", "ok" if managed_tools_ok() else "warn", "Installed" if managed_tools_ok() else "Not installed. Settings > Tools > Install build tools opens the macOS installer (needed only by a few engine parts)."))
    elif not py:
        winget = shutil.which("winget")
        out.append(Check("WinGet", "ok" if winget else "warn",
                         "Available" if winget else "Not found. Install Microsoft 'App Installer' from the Store, or install Python 3.11 manually."))
    out.append(Check("Audio tools (FFmpeg)", "ok" if audio_fx.ffmpeg_path() else "bad",
                     "Ready" if audio_fx.ffmpeg_path() else "Missing. Reinstall the software."))
    try:
        free = shutil.disk_usage(root()).free / 1e9
        out.append(Check("Free disk space", "ok" if free >= 10 else "warn" if free >= 3 else "bad",
                         f"{free:.1f} GB free on the data drive" + ("" if free >= 10 else ". Voice engines need several GB.")))
    except OSError:
        pass
    try:
        import psutil
        ram = psutil.virtual_memory().total / 1e9
        cores = psutil.cpu_count(logical=True) or 1
        low = ram < 8 or cores < 4
        out.append(Check("This PC", "info", f"{ram:.0f} GB memory, {cores} processor threads" +
                         (". Low-end mode is recommended (Settings > Performance)." if low else "")))
    except Exception:
        pass
    gpu = managed.nvidia_smi()
    if sys.platform == "darwin":
        from . import load_control
        a = load_control.apple_profile()
        out.append(Check("Apple graphics (Metal)", "info", f"{a['name']}: can speed up Chatterbox, Chatterbox Turbo and F5-TTS" if a else "Not available (Intel Mac): voices use the processor."))
    else:
        out.append(Check("NVIDIA graphics card", "info", "Found, can speed up some voices" if gpu else "Not found. Voices will use the processor."))
    return out


def engine_checks(card_voices) -> list[Check]:
    """card_voices(model_id) -> list of voice ids to test (same helper the AI Setup page uses)."""
    from . import managed, services
    out: list[Check] = []
    repair = []
    for key, m in models().items():
        try:
            voices = card_voices(key)
            files = all(managed.downloaded(key, v) for v in voices)
            env_ready = managed.marker_current(key)
        except Exception as exc:
            out.append(Check(m["name"], "warn", f"Could not check: {exc}"))
            continue
        if files and env_ready:
            out.append(Check(m["name"], "ok", "Ready"))
        elif files:
            out.append(Check(m["name"], "warn", "Voice files present, engine needs a quick repair", "engines", (key,)))
            repair.append(key)
        elif managed.engine_python(key).is_file():
            out.append(Check(m["name"], "info", "Engine installed, voice files not downloaded"))
        else:
            out.append(Check(m["name"], "info", "Not installed (optional)"))
    return out


def summarize(checks: list[Check]) -> tuple[int, int, int]:
    return (sum(c.status == "ok" for c in checks), sum(c.status == "warn" for c in checks), sum(c.status == "bad" for c in checks))


def fixable(checks: list[Check]) -> dict:
    """What the Fix button would do: {'python': bool, 'engines': [ids]}."""
    engines = []
    for c in checks:
        if c.fix == "engines":
            engines.extend(k for k in c.keys if k not in engines)
    return {"python": any(c.fix == "python" for c in checks), "engines": engines}
