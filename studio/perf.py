"""Speed report: how long each part takes, on which device, and what to do about it."""
from __future__ import annotations
import json
import threading
import time

from .core import subdir

_LOCK = threading.Lock()
_JOBS: dict[str, list[dict]] = {}
_LAST: str = ""
MAX_LOG = 2 * 1024 * 1024


def parse_line(line: str) -> dict | None:
    if not line.startswith("AVS_TIMING "):
        return None
    try:
        data = json.loads(line[len("AVS_TIMING "):])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def record(job_id: str, index: int, wall_s: float, info: dict | None, model: str = "") -> None:
    global _LAST
    row = {"index": index, "wall_s": round(wall_s, 2), "model": model, **(info or {})}
    with _LOCK:
        _JOBS.setdefault(job_id, []).append(row)
        _LAST = job_id
    try:
        path = subdir("logs") / "performance.log"
        if path.exists() and path.stat().st_size > MAX_LOG:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "job": job_id, **row}) + "\n")
    except OSError:
        pass


def rows(job_id: str = "") -> list[dict]:
    with _LOCK:
        return list(_JOBS.get(job_id or _LAST, []))


def summary(job_id: str = "") -> dict | None:
    data = rows(job_id)
    if not data:
        return None
    wall = sum(r["wall_s"] for r in data)
    audio = sum(r.get("audio_s", 0) for r in data)
    devices = sorted({r["device"] for r in data if r.get("device")})
    return {"parts": len(data), "wall_s": round(wall, 1), "avg_s": round(wall / len(data), 1), "audio_s": round(audio, 1),
            "rtf": round(wall / audio, 2) if audio else None, "devices": devices,
            "cuda_available": any(r.get("cuda_available") for r in data),
            "cached": sum(1 for r in data if r.get("reference_cached")), "loads": sum(1 for r in data if r.get("load_s", 0) > 1),
            "precision": sorted({r["precision"] for r in data if r.get("precision")}), "model": data[-1].get("model", "")}


def eta_text(job_id: str, parts_left: int) -> str:
    s = summary(job_id)
    if not s or parts_left <= 0:
        return ""
    seconds = int(s["avg_s"] * parts_left)
    left = f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"
    return f"{s['avg_s']:.1f} s per part · about {left} left"


def verdict(rtf: float | None) -> str:
    if rtf is None:
        return "Speed could not be measured."
    if rtf <= 1:
        return f"Faster than real time ({1 / rtf:.1f}× speed)."
    return f"Slower than real time: it takes {rtf:.1f}× longer than the audio it makes."


def explain(s: dict | None, wanted_device: str = "") -> list[str]:
    """Plain-language findings from measured numbers only."""
    if not s:
        return ["No speed data yet. Generate something first."]
    out = [f"{s['parts']} parts in {s['wall_s']} s, {s['avg_s']} s per part. {verdict(s['rtf'])}"]
    ran_cpu = s["devices"] and all(d.startswith("cpu") for d in s["devices"])
    if ran_cpu and (wanted_device == "cuda" or s["cuda_available"]):
        out.append("It ran on the processor, not the graphics card. Check Settings > Processing > Compute, and the NVIDIA driver.")
    elif s["devices"]:
        out.append("Device used: " + ", ".join(s["devices"]) + ".")
    if s["loads"] > 1:
        out.append(f"The voice engine was loaded {s['loads']} times. Turn on 'Keep the last voice loaded' in Settings > Performance.")
    if s["cached"] and s["parts"] > 1:
        out.append(f"The reference voice was analysed once and reused for {s['cached']} parts.")
    if s["rtf"] and s["rtf"] > 1.5:
        out.append("Tips: try Chatterbox Turbo (lighter), the 'Faster generation' option, or fewer simultaneous programs.")
    return out


BENCH_TEXT = "This is a short speed test. It shows how fast this voice works on this computer."


def benchmark(store, model, voice, reference, ref_text, options, device, log=lambda m: None) -> list[dict]:
    """Make the same short sentence twice: a first run (may include loading) and a second, steady run."""
    from . import preview
    from .audio_fx import wav_seconds
    results = []
    for label in ("first run", "second run"):
        seen: dict = {}

        def collect(line, seen=seen):
            found = parse_line(line)
            if found:
                seen.update(found)
            log(line)
        started = time.monotonic()
        path = preview.make_preview(store, model, voice, reference, ref_text, options, text=BENCH_TEXT, device=device, log=collect, force=True)
        wall = time.monotonic() - started
        audio = wav_seconds(path)
        results.append({"run": label, "wall_s": round(wall, 1), "audio_s": round(audio, 1),
                        "rtf": round(wall / audio, 2) if audio else None, "device": seen.get("device", ""),
                        "cuda_available": seen.get("cuda_available")})
    return results


def fmt_duration(seconds) -> str:
    """'4 min 12 s', '1 h 05 min 09 s' or '38 s'."""
    s = max(0, int(round(float(seconds or 0))))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min {sec:02d} s"
    if m:
        return f"{m} min {sec:02d} s"
    return f"{sec} s"
