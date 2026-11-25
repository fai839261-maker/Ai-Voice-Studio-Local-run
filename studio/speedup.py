"""Making several parts at the same time (each one uses its own loaded voice engine)."""
from __future__ import annotations
import shutil
import subprocess

# Rough graphics memory one loaded engine needs, in GB. Estimates, only used by "Automatic".
VRAM_GB = {"chatterbox": 5.0, "turbo": 4.0, "f5": 4.0, "openf5": 4.0, "xtts2": 5.0, "styletts2": 3.0, "qwen06bclone": 4.0, "qwen17bclone": 7.0,
           "qwen06bvoice": 4.0, "qwen17bvoice": 7.0, "indextts2": 8.0, "voxcpm": 5.0, "dia": 8.0, "kokoro": 1.0, "piper": 0.5}
CHOICES = (("Off", 1), ("Automatic", "auto"), ("2 parts", 2), ("3 parts", 3), ("4 parts", 4))


def free_vram_gb():
    from .guard import smi_path
    tool = smi_path()
    if not tool:
        return None
    try:
        out = subprocess.run([tool, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode == 0:
            return max(float(x) for x in out.stdout.split()) / 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def choose_parts(store, model: str, device: str, available: int, free_vram=free_vram_gb) -> int:
    """How many parts to make at once: the setting, or (Automatic) what the free graphics memory allows. Never more than the parts left."""
    setting = store.setting("parts_at_once", 1)
    if setting == "auto":
        if device != "cuda":
            n = 1
        else:
            free = free_vram()
            n = 1 if free is None else int((free - 1.5) // VRAM_GB.get(model, 6.0))
    else:
        try:
            n = int(setting or 1)
        except (TypeError, ValueError):
            n = 1
    return max(1, min(4, n, max(1, available)))
