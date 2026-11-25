"""Script helpers: tidy up pasted text and apply a pronunciation list before the voice reads it."""
from __future__ import annotations
import re

KEY = "pronunciations"
SMART = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": " - ", "\u2026": "...", "\u00a0": " ", "\u200b": ""}


def clean(text: str) -> str:
    """Remove stray spaces, repeated blank lines and invisible characters. Wording is never changed."""
    for old, new in SMART.items():
        text = text.replace(old, new)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    return "\n".join(out).strip()


def counts(text: str) -> tuple[int, int]:
    """(words, characters without surrounding whitespace)."""
    return len(text.split()), len(text.strip())


def entries(store) -> list[dict]:
    items = store.setting(KEY, [])
    return [x for x in items if isinstance(x, dict) and x.get("word", "").strip()] if isinstance(items, list) else []


def save_entries(store, items: list[dict]) -> None:
    cleaned, seen = [], set()
    for x in items:
        word, say = str(x.get("word", "")).strip(), str(x.get("say", "")).strip()
        if word and say and word.lower() not in seen:
            seen.add(word.lower())
            cleaned.append({"word": word, "say": say})
    store.set_setting(KEY, cleaned)


def apply(text: str, items: list[dict]) -> str:
    """Replace whole words (any capitalisation) with how they should be spoken. Longer entries win."""
    for x in sorted(items, key=lambda e: -len(e["word"])):
        word = re.escape(x["word"])
        edge_l = r"(?<!\w)" if re.match(r"\w", x["word"]) else ""
        edge_r = r"(?!\w)" if re.search(r"\w$", x["word"]) else ""
        text = re.sub(edge_l + word + edge_r, lambda _m, s=x["say"]: s, text, flags=re.IGNORECASE)
    return text
