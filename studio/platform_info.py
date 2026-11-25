"""What kind of computer this is, for the Mac edition (Apple Silicon M1 to M4, and Intel MacBooks with an i5, i7 or i9).

Everything reads the system through its own tools (sysctl, sw_vers, system_profiler) and answers in plain data.
`run_text` is the one place that asks the system, so tests can replace it with recorded answers.
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"


def run_text(cmd, timeout=8) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def sysctl(name: str) -> str:
    return run_text(["/usr/sbin/sysctl", "-n", name])


def app_support_dir():
    """Where a Mac keeps the software's data: ~/Library/Application Support/AVS."""
    from pathlib import Path
    return Path.home() / "Library" / "Application Support" / "AVS"


def local_base():
    """The per-user data base folder of this system (LOCALAPPDATA on Windows, Application Support on a Mac, home elsewhere)."""
    from pathlib import Path
    if IS_WIN:
        return Path(os.getenv("LOCALAPPDATA", str(Path.home())))
    if IS_MAC:
        return Path.home() / "Library" / "Application Support"
    return Path.home()


def font_stack() -> str:
    """The font list for the style sheets: Segoe UI on Windows, the Mac system font and Helvetica Neue on a Mac."""
    if IS_MAC:
        try:
            from PySide6.QtGui import QFontDatabase
            family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        except Exception:
            family = ""
        return ", ".join(f"'{f}'" for f in [family, "Helvetica Neue", "Helvetica", "Arial"] if f)
    return "'Segoe UI Variable Text','Segoe UI',Arial"


def apple_silicon() -> bool:
    """True on an M-series Mac (also when this program runs under Rosetta, which is reported separately)."""
    if not IS_MAC:
        return False
    return sysctl("hw.optional.arm64") == "1" or "Apple" in sysctl("machdep.cpu.brand_string")


def rosetta() -> bool:
    """True when an Intel (x86_64) program is being translated on an M-series Mac: it works but runs much slower."""
    return IS_MAC and sysctl("sysctl.proc_translated") == "1"


def macos_version() -> str:
    return run_text(["/usr/bin/sw_vers", "-productVersion"]) if IS_MAC else ""


def macos_version_tuple() -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", macos_version())[:3]) or (0,)


def chip_name() -> str:
    """'Apple M2 Pro', 'Intel(R) Core(TM) i9-9980HK CPU @ 2.40GHz' ..."""
    return sysctl("machdep.cpu.brand_string") or "unknown"


def chip_family(name: str | None = None) -> str:
    """'m1', 'm2', 'm3', 'm4' (any later M chip counts as 'm4'), 'intel' or ''."""
    name = name if name is not None else chip_name()
    match = re.search(r"Apple M(\d+)", name)
    if match:
        return "m" + str(min(4, int(match.group(1))))
    return "intel" if "Intel" in name else ""


def core_layout() -> dict:
    """{'performance': n, 'efficiency': n, 'physical': n, 'logical': n}. Intel Macs have no efficiency cores."""
    physical = int(sysctl("hw.physicalcpu") or 0)
    logical = int(sysctl("hw.logicalcpu") or 0)
    perf = sysctl("hw.perflevel0.physicalcpu")
    eff = sysctl("hw.perflevel1.physicalcpu")
    return {"performance": int(perf) if perf.isdigit() else physical, "efficiency": int(eff) if eff.isdigit() else 0,
            "physical": physical, "logical": logical}


def memory_gb() -> float:
    raw = sysctl("hw.memsize")
    return round(int(raw) / 1024 ** 3, 1) if raw.isdigit() else 0.0


def gpus() -> list:
    """[{'name', 'cores', 'vendor'}] from system_profiler (the built-in chip, and the AMD card of an Intel MacBook Pro)."""
    text = run_text(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"], timeout=20)
    found = []
    try:
        for item in json.loads(text).get("SPDisplaysDataType", []):
            name = item.get("sppci_model") or item.get("_name") or ""
            cores = item.get("sppci_cores")
            found.append({"name": name, "cores": int(cores) if str(cores).isdigit() else 0, "vendor": str(item.get("spdisplays_vendor", ""))})
    except (ValueError, AttributeError, TypeError):
        pass
    return found


def usable_gpu_memory_gb(total_gb: float) -> float:
    """macOS lets the built-in GPU use about two thirds of the unified memory on smaller Macs and three quarters on big ones."""
    return round(total_gb * (0.75 if total_gb > 36 else 0.66), 1)


def mac_summary() -> dict:
    """One dictionary with everything the software needs to know about this Mac."""
    name = chip_name()
    fam = chip_family(name)
    cores = core_layout()
    ram = memory_gb()
    card = gpus()
    return {"chip": name, "family": fam, "apple_silicon": fam.startswith("m"), "rosetta": rosetta(), "macos": macos_version(),
            "cores": cores, "ram_gb": ram, "gpus": card, "gpu_usable_gb": usable_gpu_memory_gb(ram) if fam.startswith("m") else 0.0}
