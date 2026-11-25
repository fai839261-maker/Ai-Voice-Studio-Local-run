"""Emotion styles. Engines that accept a written instruction get a sentence; others get safe numeric settings."""
from __future__ import annotations

LABELS = {"none": "No emotion style", "happy": "Happy", "excited": "Excited", "sad": "Sad", "angry": "Angry",
          "fearful": "Afraid", "confident": "Confident", "whisper": "Whisper", "storytelling": "Storytelling",
          "calm": "Calm", "authoritative": "Authoritative"}
INSTRUCT = {
    "happy": "Speak happily, with a warm, bright tone.", "excited": "Speak with excitement and high energy.",
    "sad": "Speak sadly, softly and a little slowly.", "angry": "Speak angrily, with firm intensity.",
    "fearful": "Speak fearfully, with a tense, unsteady voice.", "confident": "Speak confidently, clear and steady.",
    "whisper": "Speak in a quiet whisper.", "storytelling": "Narrate like a storyteller, with expressive pacing.",
    "calm": "Speak calmly and gently.", "authoritative": "Speak with calm authority and weight.",
}
# (exaggeration, cfg_weight) for engines that only have numeric expression controls
NUMERIC = {"happy": (0.65, 0.5), "excited": (0.9, 0.4), "sad": (0.35, 0.6), "angry": (0.85, 0.45), "fearful": (0.7, 0.45),
           "confident": (0.6, 0.5), "storytelling": (0.7, 0.45), "calm": (0.3, 0.55)}


def mode(entry: dict) -> str:
    supports = entry.get("supports", [])
    if "emotion_text" in supports:
        return "text"
    if "exaggeration" in supports:
        return "numeric"
    return ""


def available(entry: dict) -> list[str]:
    m = mode(entry)
    if m == "text":
        return ["none", *INSTRUCT]
    if m == "numeric":
        return ["none", *NUMERIC]
    return []


def values(entry: dict, preset: str) -> dict:
    """Widget values to apply for a preset: {'emotion_text': ...} or {'exaggeration': x, 'cfg_weight': y}."""
    m = mode(entry)
    if preset == "none" or preset not in available(entry):
        return {"emotion_text": ""} if m == "text" else {}
    if m == "text":
        return {"emotion_text": INSTRUCT[preset]}
    exaggeration, cfg = NUMERIC[preset]
    return {"exaggeration": exaggeration, "cfg_weight": cfg}
