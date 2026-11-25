"""Word check: did the voice-over really speak every word of the script?

The finished audio is turned back into text by a small speech-to-text model (Whisper, runs on the processor) and the
spoken words are compared with the script word by word. The result lists the words that were NOT spoken, the words that
were spoken instead, and extra words. Off by default; it runs only when the person asks for it.
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import tempfile
from pathlib import Path

MODEL_ID = "wordcheck"
_NUMBER_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
                 "10": "ten", "11": "eleven", "12": "twelve", "20": "twenty", "30": "thirty", "40": "forty", "50": "fifty", "100": "hundred"}


def words(text: str) -> list[str]:
    """Lower-case words without punctuation. Pause tags like [pause 1s] are not words. Apostrophes stay (don't)."""
    text = re.sub(r"\[[^\]]{0,40}\]", " ", text or "")
    text = text.replace("’", "'").replace("–", " ").replace("—", " ").replace("-", " ")
    return [w for w in re.findall(r"[a-z0-9']+", text.lower()) if w.strip("'")]


def _same(a: str, b: str) -> bool:
    """Equal words, allowing a digit written as a word and the harmless 's / ' endings."""
    if a == b:
        return True
    a2, b2 = a.strip("'"), b.strip("'")
    if a2 == b2:
        return True
    return _NUMBER_WORDS.get(a) == b or _NUMBER_WORDS.get(b) == a


def compare(script: str, spoken: str) -> dict:
    """Word-by-word comparison. 'missing' = in the script but not spoken; 'extra' = spoken but not in the script."""
    want, heard = words(script), words(spoken)
    sm = difflib.SequenceMatcher(a=want, b=heard, autojunk=False)
    missing, extra, changed = [], [], []
    matched = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            matched += i2 - i1
        elif tag == "delete":
            missing.append({"at": i1, "words": " ".join(want[i1:i2]), "context": " ".join(want[max(0, i1 - 4):i2 + 4])})
        elif tag == "insert":
            extra.append({"at": i1, "words": " ".join(heard[j1:j2]), "context": " ".join(want[max(0, i1 - 4):i1 + 4])})
        else:                                           # replace: tolerate number/word and tiny spelling differences
            a, b = want[i1:i2], heard[j1:j2]
            if len(a) == len(b) and all(_same(x, y) or difflib.SequenceMatcher(a=x, b=y).ratio() >= 0.8 for x, y in zip(a, b)):
                matched += len(a)
            else:
                changed.append({"at": i1, "script": " ".join(a), "spoken": " ".join(b), "context": " ".join(want[max(0, i1 - 4):i2 + 4])})
    total = max(1, len(want))
    spoken_ok = matched
    return {"script_words": len(want), "spoken_words": len(heard), "matched": spoken_ok, "accuracy": round(100.0 * spoken_ok / total, 1),
            "missing": missing, "extra": extra, "changed": changed,
            "perfect": not missing and not extra and not changed}


def summary_line(result: dict) -> str:
    if result.get("error"):
        return "Word check could not run: " + result["error"]
    if result["perfect"]:
        return f"Word check: all {result['script_words']} words were spoken (100%)."
    return (f"Word check: {result['accuracy']}% of {result['script_words']} words matched · "
            f"{sum(len(x['words'].split()) for x in result['missing'])} not spoken · "
            f"{len(result['changed'])} spoken differently · {sum(len(x['words'].split()) for x in result['extra'])} extra")


def report_text(result: dict, audio_name: str = "") -> str:
    lines = [f"Word check {('for ' + audio_name) if audio_name else ''}".strip(), summary_line(result), ""]
    if result.get("perfect"):
        return "\n".join(lines)
    if result["missing"]:
        lines.append("NOT SPOKEN (in the script, missing in the audio):")
        lines += [f"  - \"{x['words']}\"   …{x['context']}…" for x in result["missing"]]
        lines.append("")
    if result["changed"]:
        lines.append("SPOKEN DIFFERENTLY:")
        lines += [f"  - script: \"{x['script']}\"  →  heard: \"{x['spoken']}\"   …{x['context']}…" for x in result["changed"]]
        lines.append("")
    if result["extra"]:
        lines.append("EXTRA WORDS HEARD (not in the script):")
        lines += [f"  - \"{x['words']}\"   near …{x['context']}…" for x in result["extra"]]
    return "\n".join(lines)


def installed() -> bool:
    from . import managed
    try:
        return managed.engine_python(MODEL_ID).is_file() and managed.downloaded(MODEL_ID, "") and managed.marker_current(MODEL_ID)
    except Exception:
        return False


def transcribe(audio: Path, threads: int = 4, timeout: int = 1800) -> dict:
    """Run the speech-to-text model on a WAV/MP3 file. Needs the Word checker installed (Models page)."""
    from . import external, managed, services
    from .core import bundled
    if not installed():
        raise RuntimeError("The Word checker is not installed yet. Open Models and download 'Word checker'.")
    folder = services.model_folder(MODEL_ID, "")
    out = Path(tempfile.mkdtemp(prefix="avs-wc-")) / "out.json"
    cmd = [str(managed.engine_python(MODEL_ID)), str(bundled("runtime/whisper_check.py")), str(folder), str(audio), str(out), str(max(1, threads))]
    r = external.run(cmd, capture_output=True, timeout=timeout, env=managed.env(), priority="background")
    if r.returncode or not out.is_file():
        raise RuntimeError("Speech-to-text failed: " + (r.stderr or b"").decode(errors="replace")[-400:])
    return json.loads(out.read_text(encoding="utf-8"))


def check_audio(script: str, audio: Path, threads: int = 4) -> dict:
    """Transcribe the audio and compare with the script. Returns the comparison plus the heard text."""
    heard = transcribe(Path(audio), threads)
    result = compare(script, heard.get("text", ""))
    result["heard"] = heard.get("text", "")
    result["audio_seconds"] = heard.get("duration", 0)
    return result
