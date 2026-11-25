"""Which settings each voice engine accepts, and the safe range for every setting.

The interface only shows controls an engine supports, and every value is clamped to a
range that is known to be safe before it reaches the engine, so a wrong setting cannot
break voice generation.
"""
from __future__ import annotations
import os
import sys

# key: (minimum, maximum, default)
BASE = {
    "output_speed": (0.5, 2.0, 1.0), "pitch_semitones": (-12.0, 12.0, 0.0),
    "temperature": (0.1, 2.0, 0.8), "guidance": (1.0, 8.0, 3.0), "emotion_weight": (0.0, 1.0, 1.0),
    "exaggeration": (0.0, 1.0, 0.5), "cfg_weight": (0.0, 1.0, 0.5),
    "style_alpha": (0.0, 1.0, 0.3), "style_beta": (0.0, 1.0, 0.7), "diffusion_steps": (3, 15, 5), "embedding_scale": (1.0, 3.0, 1.0),
    "pause_ms": (0, 2500, 220), "fade_ms": (0, 2000, 0), "volume_pct": (25, 200, 100),
}
# Per-engine safe ranges that differ from the general ones
OVERRIDES = {
    "xtts2": {"temperature": (0.1, 1.0, 0.75)},
    "chatterbox": {"exaggeration": (0.25, 1.0, 0.5)},
}
LOW_END = {"diffusion_steps": (3, 8, 5)}


def limits(model: str, key: str, low_end: bool = False):
    if low_end and key in LOW_END:
        return LOW_END[key]
    return OVERRIDES.get(model, {}).get(key) or BASE.get(key)


def clamp_inplace(model: str, options: dict, low_end: bool = False) -> dict:
    """Pull every known numeric setting into its safe range (unknown keys are left alone)."""
    for key in list(options):
        spec = limits(model, key, low_end)
        if not spec or isinstance(options[key], bool):
            continue
        try:
            value = float(options[key])
        except (TypeError, ValueError):
            options[key] = spec[2]
            continue
        value = max(spec[0], min(spec[1], value))
        options[key] = int(round(value)) if isinstance(spec[2], int) else value
    return options


def download_allowed(entry: dict) -> bool:
    """Every model may be downloaded on every PC (for use here, or to copy to another PC)."""
    return True


def unavailable_reason(entry: dict, os_name: str | None = None) -> str:
    """Plain-language reason an engine cannot MAKE AUDIO on this PC, or '' when it can. It does not stop a download."""
    need = entry.get("requires") or {}
    here = os_name or ("nt" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux")
    if need.get("os") == "linux" and here in ("nt", "darwin"):
        return need.get("note") or "This engine is not available on this computer."
    if here == "darwin" and entry.get("torch"):
        try:
            from . import platform_info as pi
            intel = not pi.apple_silicon()
        except Exception:
            intel = False
        if intel:
            return ("PyTorch no longer publishes Intel-Mac builds that this voice engine needs, so it cannot run on an Intel MacBook. "
                    "Piper, Kokoro and the Word checker work here; use an Apple Silicon Mac (M1 or newer) or a Windows PC for this model.")
    return ""


# Characters a model reads reliably in ONE piece. Conservative estimates: longer text is split automatically.
MAX_CHARS = {"chatterbox": 600, "turbo": 600, "dia": 400, "f5": 800, "openf5": 800, "indextts2": 700, "voxcpm": 800,
             "styletts2": 500, "qwen06bclone": 1200, "qwen17bclone": 1200, "qwen06bvoice": 1200, "qwen17bvoice": 1200,
             "xtts2": 2000, "kokoro": 3000, "piper": 4000}


def voice_unavailable(model: str, voice: dict) -> str:
    """Why a single voice cannot be used on this PC ('' when it can)."""
    if model == "kokoro" and str(voice.get("language", "")).lower().startswith("japanese"):
        try:
            from . import managed
            if managed.is_reduced("kokoro"):
                return "Japanese needs build tools that are not installed on this PC."
        except Exception:
            pass
    return ""


def max_chars(model: str) -> int:
    return MAX_CHARS.get(model, 1500)
