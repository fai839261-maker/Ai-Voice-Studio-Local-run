"""Load strength: one place that decides how hard the software may work.

Before 0.12 the CPU / memory limits only decided *when* a voice-over could start and how many
threads a voice engine used. Everything else (installing engines, checking packages, building
parts, the start-up check) ran at full speed and at normal priority, so the PC felt busy even
when no voice was being made. Now every helper process the software starts:

* runs at a lower Windows priority (so your other programs stay fast),
* is told how many threads it may use (Python, NumPy, compilers),
* and, for long installs, is slowed down when it goes above the CPU limit.

Works with or without a graphics card. Nothing here needs a GPU, and nothing is imported that is
not in the frozen program (psutil is already required by the meters).
"""
from __future__ import annotations

import atexit
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path

NORMAL, BELOW, IDLE = "normal", "below", "idle"
WIN_FLAGS = {NORMAL: 0, BELOW: 0x00004000, IDLE: 0x00000040}       # BELOW_NORMAL / IDLE priority classes

# name -> what it means. cpu/ram/gpu are the limits in percent (100 = no limit).
LEVELS = {
    "gentle": dict(title="Gentle", info="Keeps the PC calm. Best for low-end PCs and for working while a voice-over runs.",
                   cpu=50, ram=70, gpu=70, thread_share=0.5, work=BELOW, background=IDLE, parts=1, preload=False, samples=False, warm=False),
    "balanced": dict(title="Balanced", info="Good speed and a responsive PC. Recommended for most computers.",
                     cpu=75, ram=80, gpu=85, thread_share=0.75, work=NORMAL, background=BELOW, parts=1, preload=True, samples=True, warm=True),
    "full": dict(title="Full power", info="Fastest. The software may use the whole PC, including the graphics card. Other programs may slow down.",
                 cpu=100, ram=100, gpu=100, thread_share=1.0, work=NORMAL, background=BELOW, parts="auto", preload=True, samples=True, warm=True),
}
ORDER = ("gentle", "balanced", "full")
_state = {"level": "balanced"}
_hw: dict | None = None


# ------------------------------------------------------------------------------------ this PC
def _nvidia_present() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    if os.name == "nt":
        for base in (os.environ.get("WINDIR", "C:/Windows"), os.environ.get("ProgramFiles", "C:/Program Files")):
            for rel in ("System32/nvidia-smi.exe", "NVIDIA Corporation/NVSMI/nvidia-smi.exe"):
                if (Path(base) / rel).is_file():
                    return True
    return False


def apple_profile(refresh: bool = False):
    """The built-in graphics of an M-series Mac as {'name','vram_gb','family','driver','apple': True}, or None (Windows, Linux, Intel Macs).
    'vram_gb' is the part of the shared (unified) memory that macOS lets the graphics use."""
    if _apple["done"] and not refresh:
        return _apple["value"]
    value = None
    try:
        from . import platform_info as pi
        if pi.IS_MAC and pi.apple_silicon():
            ram = pi.memory_gb()
            value = {"name": pi.chip_name(), "family": pi.chip_family(), "vram_gb": pi.usable_gpu_memory_gb(ram), "ram_gb": ram, "cc": None,
                     "driver": "Metal", "slow_half": False, "nvidia": False, "apple": True, "macos": pi.macos_version_tuple(),
                     "metal": pi.macos_version_tuple() >= (12, 3)}          # PyTorch's Metal (MPS) backend needs macOS 12.3 or newer
    except Exception:
        value = None
    _apple.update(done=True, value=value)
    return value


_apple = {"done": False, "value": None}
# models whose engine has a Metal (MPS) path in this software, and the ones Automatic trusts on an M-series Mac
MPS_CAPABLE = {"chatterbox", "turbo", "f5", "openf5", "xtts2", "qwen06bclone", "qwen06bvoice", "qwen17bclone", "qwen17bvoice", "qwen17bdesign"}
MPS_AUTO = {"chatterbox", "turbo", "f5", "openf5"}        # documented by their authors for Apple GPUs; the others use the processor unless the owner picks Apple GPU


def detect_hardware(refresh: bool = False) -> dict:
    """Cores, memory and whether an NVIDIA graphics card is present. Cheap, remembered."""
    global _hw
    if _hw is not None and not refresh:
        return _hw
    cores = os.cpu_count() or 2
    ram = 8.0
    try:
        import psutil
        ram = psutil.virtual_memory().total / 1024 ** 3
    except Exception:
        pass
    _hw = {"cores": cores, "ram_gb": round(ram, 1), "gpu": _nvidia_present(), "apple": bool(apple_profile())}
    return _hw


_gpu_names = None


def gpu_names(refresh: bool = False) -> list:
    """Names of the graphics cards in this PC, any maker (asked from Windows 10/11 once; empty if it cannot be read)."""
    global _gpu_names
    if _gpu_names is not None and not refresh:
        return _gpu_names
    names = []
    if sys.platform == "darwin":
        try:
            from . import platform_info as pi
            names = [g["name"] for g in pi.gpus() if g["name"]]
        except Exception:
            names = []
    elif os.name == "nt":
        try:
            import subprocess
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
                               capture_output=True, text=True, timeout=10, creationflags=0x08000000)
            names = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        except Exception:
            names = []
    _gpu_names = names
    return names


def recommend(hw: dict | None = None) -> str:
    hw = hw or detect_hardware()
    if hw["cores"] <= 4 or hw["ram_gb"] <= 8.5:
        return "gentle"
    if hw["cores"] >= 12 and hw["ram_gb"] >= 30:
        return "full"
    return "balanced"


def describe(hw: dict | None = None) -> str:
    hw = hw or detect_hardware()
    n = hw["cores"]
    return f"This PC: {n} processor thread{'s' if n != 1 else ''} · {hw['ram_gb']:.0f} GB memory · " + ("NVIDIA graphics card" if hw["gpu"] else "no NVIDIA graphics card (the processor does the work)")


# ------------------------------------------------------------------------------------ the chosen level
def chosen(store) -> str:
    """The level in use: the saved choice, or the one that suits this PC (Automatic)."""
    pick = store.setting("load_level", "auto")
    return pick if pick in LEVELS else recommend()


def profile(name: str | None = None) -> dict:
    return LEVELS.get(name or _state["level"], LEVELS["balanced"])


def set_level(name: str) -> None:
    _state["level"] = name if name in LEVELS else "balanced"


def level_name() -> str:
    return _state["level"]


def apply_level_settings(store, name: str) -> dict:
    """Write the limits that belong to a level into the settings (they stay adjustable afterwards)."""
    p, hw = LEVELS[name], detect_hardware()
    cores = hw["cores"]
    threads = 0 if name == "full" else max(1, math.floor(cores * p["thread_share"]))
    values = {
        "cpu_limit": p["cpu"], "ram_limit": p["ram"], "gpu_limit": p["gpu"],
        "cpu_threads_override": threads,
        "parts_at_once": (p["parts"] if (p["parts"] != "auto" or hw["gpu"]) else 1),
        "preload_voice": bool(p["preload"] and (hw["gpu"] or hw["ram_gb"] >= 15)),
        "auto_samples": bool(p["samples"]),
        "keep_model_warm": bool(p["warm"]),
    }
    for key, value in values.items():
        store.set_setting(key, value)
    return values


def preload_allowed(store) -> bool:
    """Loading a voice engine in the background is heavy: only when the PC can afford it."""
    explicit = store.setting("preload_voice", None)
    if explicit is not None:
        return bool(explicit) and level_name() != "gentle"
    return bool(profile()["preload"] and (detect_hardware()["gpu"] or detect_hardware()["ram_gb"] >= 15))


# ------------------------------------------------------------------------------------ helper processes
def thread_cap(kind: str = "background") -> int:
    """How many threads a helper process (installer, compiler, package check) may use."""
    cores = detect_hardware()["cores"]
    p = profile()
    share = p["thread_share"] if kind == "work" else min(p["thread_share"], 0.5)
    return max(1, math.floor(cores * share))


def child_env(env: dict | None, kind: str) -> dict:
    """Environment for a helper process with thread limits added (never overrides a limit already set)."""
    result = dict(os.environ if env is None else env)
    n = str(thread_cap(kind))
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMBA_NUM_THREADS"):
        result.setdefault(key, n)
    result.setdefault("MAX_JOBS", n)                       # PyTorch extensions
    result.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", n)     # CMake builds (for example Kokoro's Japanese helper)
    result.setdefault("MAKEFLAGS", "-j" + n)
    return result


def creation_flags(kind: str) -> int:
    if os.name != "nt":
        return 0
    p = profile()
    return WIN_FLAGS[p["background"] if kind == "background" else p["work"]]


def lower_priority(pid: int, kind: str) -> None:
    """Best effort: set the priority of a process that was just started (non-Windows computers)."""
    if os.name == "nt":
        return
    try:
        import psutil
        p = profile()
        level = p["background"] if kind == "background" else p["work"]
        psutil.Process(pid).nice({NORMAL: 0, BELOW: 8, IDLE: 15}[level])
    except Exception:
        pass


def apply_to_self() -> None:
    """The window itself: a little lower in Gentle mode, normal otherwise."""
    try:
        import psutil
        want = BELOW if level_name() == "gentle" else NORMAL
        if os.name == "nt":
            psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if want == BELOW else psutil.NORMAL_PRIORITY_CLASS)
        else:
            psutil.Process().nice(5 if want == BELOW else 0)
    except Exception:
        pass


# ------------------------------------------------------------------------------------ what this software uses
_known: dict = {}


def app_usage():
    """(share of the whole PC's processor in percent, memory in MB) used by this software and its helper processes."""
    try:
        import psutil
        me = psutil.Process()
        procs = [me, *me.children(recursive=True)]
    except Exception:
        return None
    cores = detect_hardware()["cores"]
    cpu, memory, alive = 0.0, 0, set()
    for proc in procs:
        try:
            alive.add(proc.pid)
            memory += proc.memory_info().rss
            if proc.pid in _known:
                cpu += _known[proc.pid].cpu_percent(None)
            else:
                _known[proc.pid] = proc
                proc.cpu_percent(None)          # the first reading is only a starting point
        except Exception:
            pass
    for pid in [p for p in _known if p not in alive]:
        _known.pop(pid, None)
    return min(100.0, cpu / cores), memory / 1024 ** 2


# ------------------------------------------------------------------------------------ CPU governor
_SUSPENDED: set = set()
_SUSPEND_LOCK = threading.Lock()


def _resume_all() -> None:
    with _SUSPEND_LOCK:
        procs = list(_SUSPENDED)
        _SUSPENDED.clear()
    for proc in procs:
        try:
            proc.resume()
        except Exception:
            pass


atexit.register(_resume_all)


class Governor:
    """Slows a long helper process (an install or a build) down when it uses more than the CPU limit.

    It measures the process and its children about twice a second and, when they are above the limit,
    pauses them for a fraction of the next second. Downloads keep working (the pauses are short).
    Does nothing for processes that stay under the limit, which is almost always the case for a
    single-threaded installer.
    """

    WINDOW = 0.5

    def __init__(self, pid: int, cap_percent: int):
        self.pid, self.cap = pid, max(10, min(100, int(cap_percent)))
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "Governor":
        if self.cap < 100:
            self.thread.start()
        return self

    def close(self) -> None:
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        _resume_all()

    @staticmethod
    def _tree(root):
        try:
            return [root, *root.children(recursive=True)]
        except Exception:
            return [root]

    @staticmethod
    def _seconds(procs) -> float:
        total = 0.0
        for proc in procs:
            try:
                t = proc.cpu_times()
                total += t.user + t.system
            except Exception:
                pass
        return total

    def _run(self) -> None:
        try:
            import psutil
            root = psutil.Process(self.pid)
        except Exception:
            return
        cores = detect_hardware()["cores"]
        try:
            while not self.stop.is_set():
                procs = self._tree(root)
                before, started = self._seconds(procs), time.monotonic()
                if self.stop.wait(self.WINDOW):
                    break
                used = self._seconds(procs) - before
                elapsed = max(0.05, time.monotonic() - started)
                allowed = self.cap / 100 * cores * elapsed
                if used <= allowed or used <= 0:
                    continue
                pause = min(1.0, self.WINDOW * (used / allowed - 1))     # so that running / (running + paused) = the limit
                held = []
                for proc in self._tree(root):
                    try:
                        proc.suspend()
                        held.append(proc)
                    except Exception:
                        pass
                with _SUSPEND_LOCK:
                    _SUSPENDED.update(held)
                self.stop.wait(pause)
                for proc in held:
                    try:
                        proc.resume()
                    except Exception:
                        pass
                with _SUSPEND_LOCK:
                    _SUSPENDED.difference_update(held)
        finally:
            _resume_all()


# the CPU limit that the governor follows; the window updates it when the limit changes
CPU_LIMIT = {"value": LEVELS["balanced"]["cpu"]}


def set_cpu_limit(percent: int) -> None:
    CPU_LIMIT["value"] = max(10, min(100, int(percent)))


class _Nothing:
    def close(self) -> None:
        pass


def govern(proc):
    """Start a Governor for a helper process when the level asks for it. Returns an object with close()."""
    try:
        if level_name() == "full" or CPU_LIMIT["value"] >= 100:
            return _Nothing()
        return Governor(proc.pid, CPU_LIMIT["value"]).start()
    except Exception:
        return _Nothing()


# ------------------------------------------------------------------------------------ the graphics card, judged honestly
import re as _re
_PRE_VOLTA = _re.compile(r"(GTX\s*(10\d\d|9\d\d|7\d\d|TITAN)|GT\s*10\d\d|Quadro\s*[PMK]\d|Tesla\s*[PMK]\d|TITAN\s*(X|Xp)|MX\s*[123]\d\d)", _re.I)
_gpu = {"done": False, "value": None}


def gpu_profile(refresh: bool = False):
    """{'name','vram_gb','cc','driver','slow_half','nvidia'} for the first NVIDIA card, or None when there is none."""
    if _gpu["done"] and not refresh:
        return _gpu["value"]
    value = None
    try:
        import subprocess
        from .guard import smi_path
        tool = smi_path()
        if tool:
            flags = 0x08000000 if os.name == "nt" else 0
            out = subprocess.run([tool, "--query-gpu=name,memory.total,compute_cap,driver_version", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=6, creationflags=flags)
            if out.returncode != 0:      # older drivers cannot tell the compute capability
                out = subprocess.run([tool, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=6, creationflags=flags)
                parts = [x.strip() for x in out.stdout.splitlines()[0].split(",")]
                name, mem, cc, driver = parts[0], parts[1], None, (parts[2] if len(parts) > 2 else "")
            else:
                parts = [x.strip() for x in out.stdout.splitlines()[0].split(",")]
                name, mem, cc, driver = parts[0], parts[1], parts[2], parts[3]
            try:
                cc_value = float(cc) if cc not in (None, "", "[N/A]") else None
            except ValueError:
                cc_value = None
            slow = (cc_value < 7.0) if cc_value is not None else bool(_PRE_VOLTA.search(name))
            value = {"name": name, "vram_gb": round(float(mem) / 1024, 1), "cc": cc_value, "driver": driver, "slow_half": slow, "nvidia": True}
    except Exception:
        value = None
    _gpu.update(done=True, value=value)
    return value


# Estimated graphics memory a model needs at FULL precision (fp32), in GB, with room for the work area.
# These are estimates, not measurements: if one is too small the engine runs out of memory and the section is redone on the processor at once.
FP32_GB = {"turbo": 2.6, "f5": 2.2, "openf5": 2.2, "xtts2": 3.0, "styletts2": 1.6, "qwen06bclone": 3.2, "qwen06bvoice": 3.2,
           "chatterbox": 4.3, "voxcpm": 3.9, "qwen17bclone": 7.5, "qwen17bvoice": 7.5, "qwen17bdesign": 7.5, "indextts2": 9.5, "dia": 8.0, "fishs2": 9.0}
MEMORY_USE = 0.90          # share of the card's memory that the work may use


def model_need_gb(model_id: str, half: bool = False) -> float:
    need = FP32_GB.get(model_id, 6.0)
    return need / 2 if half else need


def plan(model_id: str, entry: dict | None = None) -> dict:
    """What to do for one model when Run on is Automatic.
    {'mode': 'cpu' | 'gpu' | 'hybrid', 'precision': 'full' | 'half' | '', 'reason': plain words}
    - graphics card, NVIDIA only (the voice engines are built on CUDA);
    - an older card (before the 2018 generation) is slow at half precision but fast at FULL precision, so it gets full precision;
    - a newer card uses half precision when that makes the model fit;
    - the processor shares the work (hybrid) when the card fits the model and the PC has enough cores and free memory.
    """
    if entry is not None and not entry.get("torch"):
        return {"mode": "cpu", "precision": "", "reason": "This engine is built for the processor and runs there."}
    a = apple_profile()
    if a:
        need = model_need_gb(model_id)
        usable = a["vram_gb"] * 0.8                 # the system and the program share this memory too
        if not a["metal"]:
            return {"mode": "cpu", "precision": "", "reason": "The Apple graphics need macOS 12.3 or newer: the processor does the work."}
        if model_id in MPS_AUTO and need <= usable:
            return {"mode": "mps", "precision": "full", "reason": f"{a['name']} graphics (Metal, {a['ram_gb']:.0f} GB shared memory) make the voice-over; if Metal ever fails the processor takes over at once."}
        if model_id in MPS_CAPABLE and need <= usable:
            return {"mode": "cpu", "precision": "", "reason": "The processor does the work (this engine is not yet trusted on Apple graphics; pick Apple GPU under Run on to try it)."}
        why = f"This model needs about {need:.1f} GB and {a['name']} can give the graphics about {usable:.0f} GB, so the processor does the work." if need > usable else "The processor does the work."
        return {"mode": "cpu", "precision": "", "reason": why}
    p = gpu_profile()
    if not p:
        return {"mode": "cpu", "precision": "", "reason": "No NVIDIA graphics card was found: the processor does the work."}
    usable = p["vram_gb"] * MEMORY_USE
    if p["slow_half"]:
        precision, need = "full", model_need_gb(model_id)
    else:
        half = model_need_gb(model_id, True)
        precision, need = ("half", half) if half <= usable else ("full", model_need_gb(model_id))
    if need > usable:
        return {"mode": "cpu", "precision": "", "reason": f"This model needs about {need:.1f} GB of graphics memory and {p['name']} has {p['vram_gb']:.0f} GB, so the processor does the work."}
    hw = detect_hardware()
    from .resource_policy import physical_cores
    cores = hw.get("physical") or physical_cores()          # real cores, not hyper-threads
    free_ram = hw.get("ram_free_gb") or hw["ram_gb"]
    helpers = cores >= 4 and hw["ram_gb"] >= 16 and free_ram >= need + 6
    kind = "an older-generation card, used at full precision (fast on it)" if p["slow_half"] else "used at " + ("half" if precision == "half" else "full") + " precision"
    if helpers:
        return {"mode": "hybrid", "precision": precision, "reason": f"{p['name']} ({p['vram_gb']:.0f} GB, {kind}) and the processor ({cores} cores) share the parts: whichever is free takes the next part."}
    return {"mode": "gpu", "precision": precision, "reason": f"{p['name']} ({p['vram_gb']:.0f} GB, {kind}) makes the voice-over."}


def auto_device(model_id: str, entry: dict | None = None) -> str:
    """'cuda' or 'cpu' for one model when Run on is Automatic: the graphics card whenever the model fits it."""
    mode = plan(model_id, entry)["mode"]
    return "mps" if mode == "mps" else ("cuda" if mode in ("gpu", "hybrid") else "cpu")


def auto_device_reason(model_id: str, entry: dict | None = None) -> str:
    return plan(model_id, entry)["reason"]


def gpu_fits(model_id: str, entry: dict | None = None) -> bool:
    """True when the graphics card can hold this model (used by the Graphics card and Both together choices)."""
    if entry is not None and not entry.get("torch"):
        return False
    a = apple_profile()
    if a:
        return bool(a["metal"]) and model_id in MPS_CAPABLE and model_need_gb(model_id) <= a["vram_gb"] * 0.8
    p = gpu_profile()
    if not p:
        return False
    need = model_need_gb(model_id, False if p["slow_half"] else True)
    return need <= p["vram_gb"] * MEMORY_USE or model_need_gb(model_id) <= p["vram_gb"] * MEMORY_USE


def gpu_precision(model_id: str) -> str:
    """'full' or 'half' for the graphics card."""
    p = gpu_profile()
    if apple_profile() or not p or p["slow_half"]:
        return "full"
    return "half" if model_need_gb(model_id, True) <= p["vram_gb"] * MEMORY_USE else "full"


def hybrid_threads(total: int) -> tuple[int, int]:
    """(threads for the graphics-card helper, threads for the processor helper) out of the allowed total."""
    total = max(1, int(total))
    gpu = 1 if total < 4 else 2
    return gpu, max(1, total - gpu)


def auto_chunk_chars(model_id: str, device: str, fast: bool = False, entry: dict | None = None) -> int:
    """Part size in letters that keeps one part quick on this PC (0 = let the engine decide)."""
    if entry is not None and not entry.get("torch"):
        return 0
    if fast:
        return 160
    return 320 if device == "cuda" else 280 if device == "mps" else 220


# ------------------------------------------------------------------------------------ PC information
_cpu_name = {"value": None}


def cpu_name() -> str:
    if _cpu_name["value"] is not None:
        return _cpu_name["value"]
    name = ""
    if sys.platform == "darwin":
        from . import platform_info as pi
        name = pi.chip_name() if pi.chip_name() != "unknown" else ""
    if not name and os.name == "nt":
        try:
            import subprocess
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"],
                               capture_output=True, text=True, timeout=10, creationflags=0x08000000)
            name = r.stdout.strip()
        except Exception:
            name = ""
    if not name:
        import platform
        name = platform.processor() or platform.machine()
    _cpu_name["value"] = name
    return name


def windows_name() -> str:
    import platform
    if sys.platform == "darwin":
        from . import platform_info as pi
        return f"macOS {pi.macos_version()}" if pi.macos_version() else platform.platform()
    if os.name != "nt":
        return platform.platform()
    try:
        build = int(platform.version().split(".")[-1])
    except (ValueError, IndexError):
        build = 0
    return f"Windows {'11' if build >= 22000 else '10'} (build {build})" if build else platform.platform()


def pc_info(data_dir=None) -> dict:
    """Everything the PC section shows. May take a few seconds the first time (asks Windows); run it off the window thread."""
    from .resource_policy import physical_cores
    hw = detect_hardware(True)
    info = {"cpu": cpu_name(), "cores": physical_cores(), "threads": hw["cores"], "ram_gb": hw["ram_gb"], "os": windows_name(),
            "gpus": gpu_names(True), "nvidia": gpu_profile(True)}
    if sys.platform == "darwin":
        from . import platform_info as pi
        info["mac"] = pi.mac_summary()
        info["apple"] = apple_profile(True)
    try:
        import psutil
        info["ram_free_gb"] = round(psutil.virtual_memory().available / 1024 ** 3, 1)
    except Exception:
        info["ram_free_gb"] = None
    try:
        disk = shutil.disk_usage(str(data_dir or Path.home()))
        info["disk_free_gb"] = round(disk.free / 1024 ** 3, 1)
    except Exception:
        info["disk_free_gb"] = None
    return info


def pc_report(info: dict) -> str:
    """Plain words about this PC and what they mean for voice-overs."""
    lines = [f"Processor: {info['cpu'] or 'unknown'}  ·  {info['cores']} cores / {info['threads']} threads",
             f"Memory: {info['ram_gb']:.0f} GB" + (f" ({info['ram_free_gb']:.0f} GB free now)" if info.get("ram_free_gb") else ""),
             f"{'macOS' if info.get('mac') else 'Windows'}: {info['os'].replace('macOS ', '') if info.get('mac') else info['os']}"]
    mac = info.get("mac")
    if mac:
        cores = mac["cores"]
        layout = f"{cores['performance']} performance + {cores['efficiency']} efficiency cores" if cores.get("efficiency") else f"{cores['physical']} cores"
        lines[0] = f"Processor: {info['cpu'] or 'unknown'}  ·  {layout} / {cores['logical']} threads"
        if mac["apple_silicon"]:
            lines[1] += "  (unified: the graphics share it)"
    names = info.get("gpus") or []
    p = info.get("nvidia")
    a = info.get("apple")
    if a:
        lines.append(f"Graphics: {a['name']} graphics (Metal)  ·  about {a['vram_gb']:.0f} GB usable")
    elif p:
        lines.append(f"Graphics: {p['name']}  ·  {p['vram_gb']:.0f} GB" + (f"  ·  compute {p['cc']:.1f}" if p.get("cc") else "") + f"  ·  driver {p['driver']}")
    elif names:
        lines.append("Graphics: " + ", ".join(names) + "  (not NVIDIA)")
    else:
        lines.append("Graphics: none found")
    if info.get("disk_free_gb") is not None:
        lines.append(f"Free disk space: {info['disk_free_gb']:.0f} GB")
    lines.append("")
    if mac:
        if mac.get("rosetta"):
            lines.append("⚠ This program is running under Rosetta (Intel mode) on an M-series Mac. It works but is much slower. Use the Apple Silicon (arm64) version of the software.")
        if a:
            lines.append(f"✔ {a['name']}: Automatic uses the Apple graphics (Metal) for Chatterbox, Chatterbox Turbo and F5-TTS, and the processor for the other models. "
                         "If Metal ever fails for a part, the processor takes over at once. Apple GPU under Run on tries it for Qwen3-TTS and XTTS-v2 too.")
            if a["ram_gb"] <= 8.5:
                lines.append("ℹ 8 GB of shared memory is tight: use Gentle load strength, small parts, and close other apps while a voice-over runs.")
            if not a.get("metal"):
                lines.append("ℹ The Apple graphics need macOS 12.3 or newer; on this macOS the processor does the work.")
        else:
            lines.append("ℹ This is an Intel MacBook. PyTorch no longer publishes Intel-Mac builds that these voice engines need, so Piper, Kokoro and the Word checker work here, "
                         "and the cloning and Qwen models are download-only. For cloning use an Apple Silicon Mac (M1 or newer) or a Windows PC.")
    elif p and p["slow_half"]:
        lines.append(f"ℹ {p['name']} is an older-generation NVIDIA card ({p['vram_gb']:.0f} GB). It is slow at half precision but fast at full precision, "
                     "so Automatic runs the models that fit on it at full precision and lets the processor help. Models that are too big for its memory use the processor.")
    elif p and not mac:
        lines.append(f"✔ {p['name']} is a good match: Automatic uses it for models that fit in {p['vram_gb']:.0f} GB, and the processor helps.")
    elif names and not mac:
        lines.append("Your graphics card is not NVIDIA, so voice engines use the processor.")
    elif not mac:
        lines.append("Voice engines use the processor.")
    if info["ram_gb"] < 12:
        lines.append("Memory is low: Gentle load strength and small parts are best.")
    return "\n".join(lines)


def optimize(store) -> list:
    """Apply the best settings for this PC. Returns plain lines that say what was set."""
    hw = detect_hardware(True)
    level = recommend(hw)
    store.set_setting("load_level", "auto")
    apply_level_settings(store, level)
    store.set_setting("compute_mode", "auto")
    store.set_setting("compute_cpu_temp", False)
    store.set_setting("chunk_chars", 0)
    store.set_setting("guard_on", True)
    store.set_setting("guard_wait", 45)
    store.set_setting("keep_model_warm", hw["ram_gb"] >= 12)
    store.set_setting("parts_at_once", 1)
    p = gpu_profile()
    a = apple_profile()
    lines = [f"Load strength: Automatic ({LEVELS[level]['title']})",
             "Run on: Automatic (Apple graphics for the models that suit them, the processor for the rest)" if a else
             "Run on: Automatic (the graphics card and the processor share the work for every model that fits the card)",
             "Part size: Automatic (the software chooses for this PC)",
             "Parts at once: " + ("Automatic (graphics card + processor each take parts)" if p else "1 (this PC)"),
             "Keep the last voice loaded: " + ("on" if hw["ram_gb"] >= 12 else "off (low memory)"),
             "PC protection: on, waits at most 45 seconds"]
    return lines
