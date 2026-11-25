"""Which voice models suit THIS PC. A rough guide in plain words, not a measurement:
the speed words come from the model's size and from what this PC has (processor only, an older NVIDIA card, a newer card, a big card).
When the software has timed a model on this PC, the measured speed is shown as well."""
from __future__ import annotations
import json

# how heavy a model is (light < medium < heavy < xheavy)
WEIGHT = {"piper": "light", "kokoro": "light", "styletts2": "light", "wordcheck": "light",
          "turbo": "medium", "f5": "medium", "openf5": "medium", "xtts2": "medium", "voxcpm": "medium",
          "chatterbox": "heavy", "qwen06bclone": "heavy", "qwen06bvoice": "heavy",
          "qwen17bclone": "xheavy", "qwen17bvoice": "xheavy", "qwen17bdesign": "xheavy", "indextts2": "xheavy", "dia": "xheavy", "fishs2": "xheavy"}
ORDER = ("light", "medium", "heavy", "xheavy")
TIERS = ("fast", "good", "slow", "very slow")
# weight x where it runs -> speed tier (index into TIERS)
MATRIX = {"light": {"cpu": 0, "old": 0, "mid": 0, "high": 0, "apple": 0},
          "medium": {"cpu": 2, "old": 1, "mid": 0, "high": 0, "apple": 1},
          "heavy": {"cpu": 3, "old": 2, "mid": 1, "high": 0, "apple": 1},
          "xheavy": {"cpu": 3, "old": 3, "mid": 2, "high": 1, "apple": 2}}
WORDS = {"fast": "Fast on this PC", "good": "Good speed on this PC", "slow": "Slow on this PC", "very slow": "Very slow on this PC"}
# among models of the same speed, the ones tried first when choosing what to recommend
PREFERENCE = ("f5", "turbo", "xtts2", "kokoro", "piper", "chatterbox", "qwen06bclone", "qwen06bvoice", "voxcpm", "openf5", "styletts2")


def device_class(model_id: str, entry: dict | None = None) -> str:
    """'cpu', 'old' (older NVIDIA card), 'mid' or 'high' (newer card with 12 GB or more): where this model will run on this PC."""
    from . import load_control
    if entry is not None and not entry.get("torch"):
        return "cpu"
    how = load_control.plan(model_id, entry)
    if how["mode"] == "mps":
        return "apple"
    if how["mode"] == "cpu":
        return "cpu"
    card = load_control.gpu_profile()
    if not card:
        return "cpu"
    if card["slow_half"]:
        return "old"
    return "high" if card["vram_gb"] >= 12 else "mid"


def _noun() -> str:
    import sys
    return "Mac" if sys.platform == "darwin" else "PC"


def rate(model_id: str, entry: dict | None = None) -> dict:
    """{'tier', 'text', 'download_only', 'where'}"""
    from . import capabilities
    entry = entry or {}
    reason = capabilities.unavailable_reason(entry) if entry else ""
    if reason:
        return {"tier": "download only", "text": f"Download only: it cannot make audio on this {_noun()}", "download_only": True, "where": "", "why": reason}
    weight = WEIGHT.get(model_id, "heavy")
    where = device_class(model_id, entry)
    tier = TIERS[MATRIX[weight][where]]
    why = {"cpu": "runs on the processor", "old": "runs on your older graphics card (full precision) with the processor helping",
           "mid": "runs on your graphics card with the processor helping", "high": "runs on your strong graphics card",
           "apple": "runs on the Apple graphics (Metal) of your Mac"}[where]
    if weight == "light":
        why = "light model that is fast on any PC"
    return {"tier": tier, "text": WORDS[tier].replace("this PC", "this " + _noun()), "download_only": False, "where": where, "why": why}


def recommended(models: dict) -> set:
    """Up to three models per kind that are fast or good on this PC."""
    stars = set()
    for kind in ("tts", "clone", "design"):
        pool = []
        for mid, entry in models.items():
            if entry.get("kind") != kind:
                continue
            r = rate(mid, entry)
            if r["download_only"] or TIERS.index(r["tier"]) > 1:
                continue
            pref = PREFERENCE.index(mid) if mid in PREFERENCE else len(PREFERENCE)
            pool.append((TIERS.index(r["tier"]), ORDER.index(WEIGHT.get(mid, "heavy")), pref, mid))
        stars.update(m for *_x, m in sorted(pool)[:3])
    return stars


def suffix(model_id: str, entry: dict, stars: set) -> str:
    """Short text added to the model name in the Studio list."""
    r = rate(model_id, entry)
    if model_id in stars:
        return "  ★"
    if r["tier"] in ("slow", "very slow"):
        return "  · slow here"
    return ""


def badge(model_id: str, entry: dict, stars: set) -> str:
    r = rate(model_id, entry)
    star = f"★ Recommended for this {_noun()} · " if model_id in stars else ""
    return f"{star}{r['text']}"


def measured_rtf(model_id: str, limit: int = 40):
    """Seconds it took to make one second of audio in the last runs of this model on this PC (None when never timed)."""
    try:
        from .core import subdir
        path = subdir("logs") / "performance.log"
        if not path.is_file():
            return None
        wall = audio = 0.0
        taken = 0
        for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()[-600:]):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("model") == model_id and row.get("audio_s", 0) > 0:
                wall += float(row.get("wall_s", 0))
                audio += float(row["audio_s"])
                taken += 1
                if taken >= limit:
                    break
        return round(wall / audio, 2) if audio and taken >= 2 else None
    except Exception:
        return None


def duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} hours"
