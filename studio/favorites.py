"""Favourite voices and a flat list of every voice the person can use.

A voice is identified by (model id, voice id). Cloned voices use the saved profile id
as the voice id, built-in voices use the engine's own voice id.
"""
from __future__ import annotations
from pathlib import Path

from .core import root, models
from . import clones, capabilities

KEY = "favorite_voices"


def favorites(store) -> list[dict]:
    items = store.setting(KEY, [])
    return [x for x in items if isinstance(x, dict) and x.get("model") and x.get("voice") is not None]


def is_favorite(store, model: str, voice: str) -> bool:
    return any(x["model"] == model and x["voice"] == voice for x in favorites(store))


def set_favorite(store, model: str, voice: str, name: str, on: bool) -> bool:
    items = [x for x in favorites(store) if not (x["model"] == model and x["voice"] == voice)]
    if on:
        items.append({"model": model, "voice": voice, "name": name})
    store.set_setting(KEY, items)
    return on


def toggle(store, model: str, voice: str, name: str) -> bool:
    return set_favorite(store, model, voice, name, not is_favorite(store, model, voice))


def language_family(language: str) -> str:
    """'American English' -> 'English', 'English (UK)' -> 'English', 'Mandarin Chinese' -> 'Chinese'."""
    words = [w for w in language.replace("(", " ").replace(")", " ").split() if w.upper() not in ("UK", "US")]
    return words[-1] if words else ""


def _piper_installed(voice_id: str) -> bool:
    base = root() / "models" / "piper" / voice_id
    return all((base / (voice_id + suffix)).is_file() for suffix in (".onnx", ".onnx.json"))


def all_voices(store, engine_ready=None) -> list[dict]:
    """Every selectable voice.

    engine_ready(model_id) -> bool tells whether the engine is installed; it is passed in
    so this function stays quick and testable.
    Each row: model, model_name, voice, name, kind ('tts'/'clone'), language, ready, favorite.
    """
    ready_cache: dict[str, bool] = {}

    def ready(model_id: str) -> bool:
        if model_id not in ready_cache:
            ready_cache[model_id] = bool(engine_ready(model_id)) if engine_ready else True
        return ready_cache[model_id]

    rows = []
    catalog = models()
    for m in catalog.values():
        if m["kind"] == "clone":
            for p in clones.profiles(store):
                if p["model"] != m["id"]:
                    continue
                rows.append(dict(model=m["id"], model_name=m["name"], voice=p["id"], name=p["name"], kind="clone",
                                 language="Cloned", gender="", family="Cloned", ready=ready(m["id"])))
            continue
        for v in m.get("voices", []):
            if m["id"] == "piper" and not _piper_installed(v["id"]):
                continue
            if capabilities.voice_unavailable(m["id"], v):
                continue
            rows.append(dict(model=m["id"], model_name=m["name"], voice=v["id"], name=v["name"], kind="tts",
                             language=v.get("language", ""), gender=v.get("gender", ""),
                             family=language_family(v.get("language", "")), ready=ready(m["id"])))
    fav = {(x["model"], x["voice"]) for x in favorites(store)}
    for r in rows:
        r["favorite"] = (r["model"], r["voice"]) in fav
    return rows


def matches(row: dict, text: str = "", favorites_only: bool = False, kind: str = "", gender: str = "", family: str = "",
            model: str = "", availability: str = "") -> bool:
    if favorites_only and not row["favorite"]:
        return False
    if kind and row["kind"] != kind:
        return False
    if gender and row.get("gender", "").lower() != gender.lower():
        return False
    if family and row.get("family", "") != family:
        return False
    if model and row["model"] != model:
        return False
    if availability == "ready" and not row["ready"]:
        return False
    if availability == "setup" and row["ready"]:
        return False
    text = text.strip().lower()
    if not text:
        return True
    return text in " ".join((row["name"], row["model_name"], row["language"], row["voice"])).lower()
