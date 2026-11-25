"""Voice Training Lab: prepare recordings, check them, build a clone voice, export for fine-tuning.

Honest scope: this builds a high-quality reference voice (usable at once with the cloning engines)
and writes training datasets in standard layouts. It does not run a fine-tuning job itself.
"""
from __future__ import annotations
import array
import json
import math
import os
import re
import shutil
import subprocess
import uuid
import wave
from pathlib import Path

from .core import subdir, join_wave, is_audio, safe_file_name
from . import audio_fx, external

RATE = 24000
TEXT_EXT = (".txt", ".lab")


def base() -> Path:
    return subdir("training")


def _file(pid: str) -> Path:
    return base() / pid / "project.json"


def projects() -> list[dict]:
    out = []
    for f in sorted(base().glob("*/project.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return sorted(out, key=lambda p: p.get("name", "").lower())


def load(pid: str) -> dict:
    return json.loads(_file(pid).read_text(encoding="utf-8"))


def save(project: dict) -> None:
    f = _file(project["id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def create(name: str, language: str = "English") -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Give the voice project a name.")
    project = {"id": uuid.uuid4().hex[:12], "name": name, "language": language, "clips": []}
    (base() / project["id"] / "wavs").mkdir(parents=True, exist_ok=True)
    save(project)
    return project


def delete(pid: str) -> None:
    shutil.rmtree(base() / pid, ignore_errors=True)


def analyze(path: Path) -> dict:
    """Length and level of a 16-bit mono WAV."""
    with wave.open(str(path), "rb") as w:
        frames, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
        data = w.readframes(frames)
    seconds = frames / max(1, rate)
    if width != 2 or not data:
        return {"seconds": seconds, "rms_dbfs": -99.0, "peak": 0.0, "clipped": False}
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    peak = max(max(samples), -min(samples)) / 32768 if samples else 0.0
    step = max(1, len(samples) // 60000)
    picked = samples[::step]
    rms = math.sqrt(sum(x * x for x in picked) / max(1, len(picked))) / 32768
    return {"seconds": round(seconds, 2), "rms_dbfs": round(20 * math.log10(rms), 1) if rms > 0 else -99.0,
            "peak": round(peak, 3), "clipped": peak >= 0.999}


def _convert(src: Path, dst: Path, rate: int = RATE) -> None:
    ffmpeg = audio_fx.ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("Audio tools (FFmpeg) are missing. Use Settings > Check software and dependencies.")
    r = external.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-ac", "1",
                      "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)], capture_output=True, timeout=600,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode or not is_audio(dst):
        dst.unlink(missing_ok=True)
        raise RuntimeError("Could not read this audio file: " + r.stderr.decode(errors="replace")[-200:])


def sidecar_text(path: Path) -> str:
    for ext in TEXT_EXT:
        f = path.with_suffix(ext)
        if f.is_file() and f.stat().st_size < 20000:
            return re.sub(r"\s+", " ", f.read_text(encoding="utf-8-sig", errors="replace")).strip()
    return ""


def add_clips(pid: str, paths, progress=None) -> tuple[list[dict], list[str]]:
    """Convert recordings to clean mono WAV. Returns (added clips, problems)."""
    project = load(pid)
    folder = base() / pid / "wavs"
    folder.mkdir(parents=True, exist_ok=True)
    added, problems = [], []
    for i, source in enumerate(paths):
        source = Path(source)
        cid = uuid.uuid4().hex[:8]
        target = folder / f"{cid}.wav"
        try:
            _convert(source, target)
            clip = {"id": cid, "name": source.name, "file": f"wavs/{cid}.wav", "text": sidecar_text(source), **analyze(target)}
            project["clips"].append(clip)
            added.append(clip)
        except Exception as exc:
            problems.append(f"{source.name}: {exc}")
        if progress:
            progress((i + 1) / max(1, len(paths)), source.name)
    save(project)
    return added, problems


def set_text(pid: str, cid: str, text: str) -> None:
    project = load(pid)
    for clip in project["clips"]:
        if clip["id"] == cid:
            clip["text"] = re.sub(r"\s+", " ", text).strip()
    save(project)


def remove_clip(pid: str, cid: str) -> None:
    project = load(pid)
    keep = []
    for clip in project["clips"]:
        if clip["id"] == cid:
            (base() / pid / clip["file"]).unlink(missing_ok=True)
        else:
            keep.append(clip)
    project["clips"] = keep
    save(project)


def clip_issues(clip: dict) -> list[str]:
    issues = []
    if not clip.get("text"):
        issues.append("no transcript")
    if clip["seconds"] < 1:
        issues.append("under 1 second")
    if clip["seconds"] > 30:
        issues.append("over 30 seconds")
    if clip.get("clipped"):
        issues.append("distorted (clipped)")
    if clip["rms_dbfs"] < -40:
        issues.append("very quiet")
    return issues


def report(pid: str) -> dict:
    clips = load(pid)["clips"]
    minutes = sum(c["seconds"] for c in clips) / 60
    with_text = sum(1 for c in clips if c.get("text"))
    bad = {c["id"]: clip_issues(c) for c in clips if clip_issues(c)}
    if not clips:
        verdict = "Add recordings of one speaker to begin."
    elif minutes < 1:
        verdict = "Too little to build a voice. Add at least a minute of clear speech."
    elif minutes < 5:
        verdict = "Good for a clone voice. Not enough for fine-tuning."
    elif minutes < 30:
        verdict = "Enough for a basic fine-tuning attempt, and very good for a clone voice."
    else:
        verdict = "A solid dataset for fine-tuning."
    return {"clips": len(clips), "minutes": round(minutes, 1), "with_text": with_text, "problems": bad, "verdict": verdict,
            "note": "Minute counts are rules of thumb, not guarantees."}


def build_reference(pid: str, max_seconds: float = 25.0) -> tuple[Path, str]:
    """Join the best clips into one clean reference (with its exact transcript)."""
    project = load(pid)
    good = [c for c in project["clips"] if c.get("text") and not clip_issues(c) and 3 <= c["seconds"] <= 20]
    if not good:
        raise ValueError("No usable clips yet. Each clip needs a transcript, a length of 3 to 20 seconds, and clean audio.")
    good.sort(key=lambda c: abs(c["rms_dbfs"] + 23))
    chosen, total = [], 0.0
    for clip in good:
        if total + clip["seconds"] <= max_seconds or not chosen:
            chosen.append(clip)
            total += clip["seconds"]
    out = base() / pid / "reference_built.wav"
    join_wave([base() / pid / c["file"] for c in chosen], out, 250)
    return out, " ".join(c["text"] for c in chosen)


def export_dataset(pid: str, engine: str, dest: Path) -> Path:
    """Write a training dataset: 'xtts' (22.05 kHz) or 'styletts2' (24 kHz)."""
    if engine not in ("xtts", "styletts2"):
        raise ValueError("Unknown training format.")
    project = load(pid)
    clips = [c for c in project["clips"] if c.get("text") and not clip_issues(c)]
    if len(clips) < 2:
        raise ValueError("Need at least 2 clean clips with transcripts to export.")
    rate = 22050 if engine == "xtts" else RATE
    out = Path(dest) / safe_file_name(f"{project['name']}-{engine}-dataset")
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    speaker = safe_file_name(project["name"]).replace(" ", "_") or "speaker"
    rows = []
    for clip in clips:
        name = f"{clip['id']}.wav"
        _convert(base() / pid / clip["file"], out / "wavs" / name, rate)
        text = clip["text"].replace("|", " ").replace("\n", " ")
        rows.append((f"wavs/{name}", text))
    if engine == "xtts":
        lines = ["audio_file|text|speaker_name"] + [f"{a}|{t}|{speaker}" for a, t in rows]
        (out / "metadata.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        rows_sp = [f"{a}|{t}|{speaker}" for a, t in rows]
        cut = max(1, len(rows_sp) // 10) if len(rows_sp) >= 10 else 0
        (out / "train_list.txt").write_text("\n".join(rows_sp[cut:]) + "\n", encoding="utf-8")
        (out / "val_list.txt").write_text("\n".join(rows_sp[:cut] or rows_sp[-1:]) + "\n", encoding="utf-8")
    (out / "README.txt").write_text(
        f"Dataset for {engine} fine-tuning: {len(rows)} clips, mono 16-bit WAV at {rate} Hz.\n"
        "Layout is the common one for this engine; check your trainer's documentation for the exact options.\n"
        "Only train on voices you have permission to use.\n", encoding="utf-8")
    return out
