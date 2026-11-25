"""Saved voice samples: one folder, one short WAV per voice, so the Library can play any voice instantly.

samples/voices/<model>/<voice>.wav   built-in voices (made once, after the model is installed)
samples/clones/<voice id>.wav        saved clone voices
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path

from .core import root, is_audio, models, safe_file_name
from . import clones, preview


def folder() -> Path:
    path = root() / "samples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def voice_path(model: str, voice: str) -> Path:
    return folder() / "voices" / safe_file_name(model) / f"{safe_file_name(voice) or 'voice'}.wav"


def clone_path(profile_id: str) -> Path:
    return folder() / "clones" / f"{safe_file_name(profile_id)}.wav"


def row_path(row: dict) -> Path:
    """Where the sample of a Library row is (or will be) kept."""
    return clone_path(row["voice"]) if row["kind"] == "clone" else voice_path(row["model"], row["voice"])


def has(path: Path) -> bool:
    return is_audio(path)


def save(source: Path, dest: Path) -> Path:
    """Put a finished sample in its place without ever leaving a half-written file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(dest.stem + ".saving.wav")
    try:
        shutil.copyfile(source, temp)
        if not is_audio(temp):
            raise RuntimeError("The sample is not a valid audio file.")
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
    return dest


def build(store, row: dict, device: str = "cpu", log=lambda m: None) -> Path:
    """Make the sample for one Library row with its model and keep it. Raises when the model cannot do it right now."""
    entry = models()[row["model"]]
    options = {"output_speed": 1.0, "pitch_semitones": 0.0}
    reference = transcript = ""
    voice = row["voice"]
    language = row.get("language", "") if row["kind"] == "tts" else ""
    if row["kind"] == "clone":
        profile = next((p for p in clones.profiles(store) if p["id"] == row["voice"]), None)
        if not profile:
            raise ValueError("That voice no longer exists.")
        if "ref_text" in entry.get("supports", []) and not profile.get("transcript", "").strip():
            raise ValueError("This model needs the words spoken in the sample first.")
        reference, transcript, voice = profile["reference"], profile.get("transcript", ""), ""
        options.update(profile.get("options", {}))
    if row["model"].startswith("qwen"):
        options["language"] = "Auto"
    elif entry.get("lang_option"):
        options["language"] = "English"
    made = preview.make_preview(store, row["model"], voice, reference, transcript, options, language=language, device=device, log=log)
    return save(made, row_path(row))


def remove_clone(profile_id: str) -> None:
    clone_path(profile_id).unlink(missing_ok=True)


def clear_all() -> int:
    count = 0
    for path in folder().rglob("*.wav"):
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    return count
