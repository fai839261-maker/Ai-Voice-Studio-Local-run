"""Channels: a YouTube / brand channel with its scripts, prompts, voice direction and assigned voices.

Stored in one small file (state/channels.json) that is part of every backup.
"""
from __future__ import annotations
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from .core import root

WPM = 150                       # speaking speed used for the estimated duration
KINDS = {"script": "Prompt for script", "video": "For video generation", "image": "Image prompts"}
CHANNEL_FIELDS = {"name", "category", "description", "emoji", "status", "master_prompt", "master_on", "voice_direction", "logo", "competitors", "countries"}
SCRIPT_FIELDS = {"title", "text", "status", "notes", "country", "sections", "vo", "voice"}
LOCK_KINDS = {"section": "Channels section (everything)", "channels": "Chosen channels only", "prompts": "Prompts tab only"}
PROMPT_FIELDS = {"title", "text", "kind"}
TEMPLATES = [
    {"id": "t-doc-long", "kind": "script", "title": "Documentary · long form",
     "text": "Write a YouTube documentary script on: [TOPIC]. Start with a strong hook, follow a clear story flow "
             "(background → key events → impact → conclusion), use simple language and keep it engaging for a general audience."},
    {"id": "t-short", "kind": "script", "title": "Short · quick video",
     "text": "Write a short, fast-paced video script on: [TOPIC]. One hook sentence, a few quick facts, and one closing line that makes people follow."},
    {"id": "t-explainer", "kind": "script", "title": "Explainer · tools and tips",
     "text": "Write a friendly explainer script on: [TOPIC]. Explain it like I am new to it, give practical tips and finish with a short summary."},
    {"id": "t-video", "kind": "video", "title": "Video generation · scene by scene",
     "text": "Create realistic, cinematic video prompts for scene-by-scene generation based on the script. Include setting, subject, action, "
             "camera movement, lighting and atmosphere. Keep the people and key objects consistent."},
    {"id": "t-image", "kind": "image", "title": "Image · thumbnail and B-roll",
     "text": "Create high-quality, realistic thumbnail and B-roll image prompts for the video topic. Include subject, environment, mood, composition, "
             "text space (if needed) and style (realistic, cinematic, documentary)."},
]


def new_id() -> str:
    return secrets.token_hex(4)


def word_count(text: str) -> int:
    return len((text or "").split())


def duration_seconds(text: str, wpm: int = WPM) -> int:
    return round(word_count(text) / max(1, wpm) * 60)


def clock(seconds: int) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def split_by_length(text: str, max_words: int) -> list[str]:
    """Cut a script into pieces of at most max_words words, at paragraph ends first and sentence ends if a paragraph is longer."""
    max_words = max(20, int(max_words))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")) if p.strip()]
    units = []
    for para in paragraphs:
        if word_count(para) <= max_words:
            units.append(para)
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            while word_count(sentence) > max_words:          # a very long sentence is cut at the limit
                pieces = sentence.split()
                units.append(" ".join(pieces[:max_words]))
                sentence = " ".join(pieces[max_words:])
            if sentence.strip():
                units.append(sentence.strip())
    chunks, current = [], []
    for unit in units:
        if current and word_count(" ".join(current + [unit])) > max_words:
            chunks.append("\n\n".join(current))
            current = []
        current.append(unit)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


class Channels:
    def __init__(self, file: Path | None = None):
        self.file = Path(file) if file else root() / "state" / "channels.json"
        self.data = self._load()

    # ---- storage
    def _load(self) -> dict:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("channels"), list):
                return data
        except (OSError, ValueError):
            pass
        return {"version": 1, "active": "", "channels": []}

    def save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)

    # ---- channels
    def all(self) -> list[dict]:
        return self.data["channels"]

    def channel(self, cid: str) -> dict | None:
        return next((c for c in self.all() if c["id"] == cid), None)

    def active(self) -> dict | None:
        return self.channel(self.data.get("active", "")) or (self.all()[0] if self.all() else None)

    def set_active(self, cid: str) -> None:
        if self.channel(cid):
            self.data["active"] = cid
            self.save()

    def add_channel(self, name: str, category: str = "", description: str = "", emoji: str = "🎙") -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Give the channel a name.")
        if any(c["name"].lower() == name.lower() for c in self.all()):
            raise ValueError("A channel with this name already exists.")
        channel = {"id": new_id(), "name": name[:60], "category": category.strip()[:80], "description": description.strip()[:500], "emoji": emoji or "🎙",
                   "status": "Active", "master_prompt": "", "master_on": True, "voice_direction": "", "created": time.time(),
                   "logo": "", "competitors": [], "countries": [],
                   "prompts": [], "voices": [], "scripts": []}
        self.all().append(channel)
        if not self.data.get("active"):
            self.data["active"] = channel["id"]
        self.save()
        return channel

    def update_channel(self, cid: str, **fields) -> dict:
        channel = self._need(cid)
        for key, value in fields.items():
            if key not in CHANNEL_FIELDS:
                raise ValueError("Unknown channel field: " + key)
            if key == "name":
                value = (value or "").strip()
                if not value:
                    raise ValueError("Give the channel a name.")
                if any(c["name"].lower() == value.lower() and c["id"] != cid for c in self.all()):
                    raise ValueError("A channel with this name already exists.")
            channel[key] = value
        self.save()
        return channel

    def duplicate_channel(self, cid: str) -> dict:
        source = self._need(cid)
        clone = copy.deepcopy(source)
        clone["id"] = new_id()
        names = {c["name"].lower() for c in self.all()}
        base, n = source["name"] + " copy", 1
        name = base
        while name.lower() in names:
            n += 1
            name = f"{base} {n}"
        clone["name"], clone["created"] = name, time.time()
        for group in ("prompts", "scripts"):
            for item in clone[group]:
                item["id"] = new_id()
        self.all().append(clone)
        self.save()
        return clone

    def delete_channel(self, cid: str) -> None:
        self._need(cid)
        self.data["channels"] = [c for c in self.all() if c["id"] != cid]
        if self.data.get("active") == cid:
            self.data["active"] = self.all()[0]["id"] if self.all() else ""
        self.save()

    def _need(self, cid: str) -> dict:
        channel = self.channel(cid)
        if not channel:
            raise ValueError("That channel no longer exists.")
        return channel

    # ---- scripts
    def script(self, cid: str, sid: str) -> dict | None:
        return next((s for s in self._need(cid)["scripts"] if s["id"] == sid), None)

    def add_script(self, cid: str, title: str, text: str = "", status: str = "Draft", country: str = "") -> dict:
        script = {"id": new_id(), "title": (title or "New script").strip()[:120] or "New script", "text": text or "", "status": status, "notes": "",
                  "country": country, "sections": [], "vo": False, "voice": None, "created": time.time(), "updated": time.time()}
        self._need(cid)["scripts"].append(script)
        self.save()
        return script

    def update_script(self, cid: str, sid: str, **fields) -> dict:
        script = self.script(cid, sid)
        if not script:
            raise ValueError("That script no longer exists.")
        for key, value in fields.items():
            if key not in SCRIPT_FIELDS:
                raise ValueError("Unknown script field: " + key)
            script[key] = value
        script["updated"] = time.time()
        self.save()
        return script

    def delete_script(self, cid: str, sid: str) -> None:
        channel = self._need(cid)
        channel["scripts"] = [s for s in channel["scripts"] if s["id"] != sid]
        self.save()

    def duplicate_script(self, cid: str, sid: str) -> dict:
        source = self.script(cid, sid)
        if not source:
            raise ValueError("That script no longer exists.")
        return self.add_script(cid, source["title"] + " (copy)", source["text"], "Draft")

    def merge_scripts(self, cid: str, ids: list[str], title: str = "") -> dict:
        scripts = [self.script(cid, i) for i in ids]
        scripts = [s for s in scripts if s]
        if len(scripts) < 2:
            raise ValueError("Select two or more scripts to merge.")
        return self.add_script(cid, title or f"{scripts[0]['title']} + {len(scripts) - 1} more", "\n\n".join(s["text"].strip() for s in scripts if s["text"].strip()),
                               country=scripts[0].get("country", ""))

    def split_script(self, cid: str, sid: str, max_words: int = 300) -> list[dict]:
        source = self.script(cid, sid)
        if not source:
            raise ValueError("That script no longer exists.")
        pieces = split_by_length(source["text"], max_words)
        if len(pieces) < 2:
            raise ValueError("This script is already shorter than that length.")
        return [self.add_script(cid, f"{source['title']} (part {i})", piece, country=source.get("country", "")) for i, piece in enumerate(pieces, 1)]

    # ---- prompts
    def add_prompt(self, cid: str, kind: str, title: str, text: str = "") -> dict:
        if kind not in KINDS:
            raise ValueError("Unknown prompt type.")
        prompt = {"id": new_id(), "kind": kind, "title": (title or KINDS[kind]).strip()[:80], "text": text, "applied": False}
        self._need(cid)["prompts"].append(prompt)
        if not self.applied_prompt(cid, kind):
            prompt["applied"] = True
        self.save()
        return prompt

    def add_from_template(self, cid: str, template_id: str) -> dict:
        template = next((t for t in TEMPLATES if t["id"] == template_id), None)
        if not template:
            raise ValueError("Unknown template.")
        return self.add_prompt(cid, template["kind"], template["title"], template["text"])

    def update_prompt(self, cid: str, pid: str, **fields) -> dict:
        prompt = next((p for p in self._need(cid)["prompts"] if p["id"] == pid), None)
        if not prompt:
            raise ValueError("That prompt no longer exists.")
        for key, value in fields.items():
            if key not in PROMPT_FIELDS:
                raise ValueError("Unknown prompt field: " + key)
            prompt[key] = value
        self.save()
        return prompt

    def delete_prompt(self, cid: str, pid: str) -> None:
        channel = self._need(cid)
        gone = next((p for p in channel["prompts"] if p["id"] == pid), None)
        channel["prompts"] = [p for p in channel["prompts"] if p["id"] != pid]
        if gone and gone["applied"]:
            same = [p for p in channel["prompts"] if p["kind"] == gone["kind"]]
            if same:
                same[0]["applied"] = True
        self.save()

    def apply_prompt(self, cid: str, pid: str) -> None:
        channel = self._need(cid)
        target = next((p for p in channel["prompts"] if p["id"] == pid), None)
        if not target:
            raise ValueError("That prompt no longer exists.")
        for p in channel["prompts"]:
            if p["kind"] == target["kind"]:
                p["applied"] = p["id"] == pid
        self.save()

    def applied_prompt(self, cid: str, kind: str) -> dict | None:
        return next((p for p in self._need(cid)["prompts"] if p["kind"] == kind and p["applied"]), None)

    # ---- voices
    def assign_voice(self, cid: str, model: str, voice: str, name: str, role: str = "main") -> dict:
        channel = self._need(cid)
        if role not in ("main", "alt"):
            raise ValueError("A voice is either the main voice or an alternative.")
        channel["voices"] = [v for v in channel["voices"] if not (v["model"] == model and v["voice"] == voice)]
        if len(channel["voices"]) >= 6:
            raise ValueError("A channel can have up to 6 voices.")
        if not channel["voices"]:
            role = "main"
        if role == "main":
            for v in channel["voices"]:
                v["role"] = "alt"
        entry = {"model": model, "voice": voice, "name": name or voice, "role": role}
        channel["voices"].append(entry)
        channel["voices"].sort(key=lambda v: v["role"] != "main")
        self.save()
        return entry

    def remove_voice(self, cid: str, model: str, voice: str) -> None:
        channel = self._need(cid)
        was_main = any(v["role"] == "main" and v["model"] == model and v["voice"] == voice for v in channel["voices"])
        channel["voices"] = [v for v in channel["voices"] if not (v["model"] == model and v["voice"] == voice)]
        if was_main and channel["voices"]:
            channel["voices"][0]["role"] = "main"
        self.save()

    def main_voice(self, cid: str) -> dict | None:
        return next((v for v in self._need(cid)["voices"] if v["role"] == "main"), None)

    # ---- numbers for the details panel
    def stats(self, cid: str, voice_overs: int = 0) -> dict:
        channel = self._need(cid)
        return {"scripts": len(channel["scripts"]), "voice_overs": voice_overs, "prompts": len(channel["prompts"]), "voices": len(channel["voices"])}

    # ---- competitors (every field optional: a name, a link, or both)
    def add_competitor(self, cid: str, name: str = "", link: str = "") -> dict:
        name, link = (name or "").strip()[:120], (link or "").strip()[:500]
        if not name and not link:
            raise ValueError("Type a name or a link (or both).")
        channel = self._need(cid)
        entry = {"id": new_id(), "name": name, "link": link}
        channel.setdefault("competitors", []).append(entry)
        self.save()
        return entry

    def update_competitor(self, cid: str, comp_id: str, name: str = "", link: str = "") -> dict:
        name, link = (name or "").strip()[:120], (link or "").strip()[:500]
        if not name and not link:
            raise ValueError("Type a name or a link (or both).")
        entry = next((c for c in self._need(cid).get("competitors", []) if c["id"] == comp_id), None)
        if not entry:
            raise ValueError("That competitor no longer exists.")
        entry["name"], entry["link"] = name, link
        self.save()
        return entry

    def remove_competitor(self, cid: str, comp_id: str) -> None:
        channel = self._need(cid)
        channel["competitors"] = [c for c in channel.get("competitors", []) if c["id"] != comp_id]
        self.save()

    def competitors_text(self, cid: str, links_only: bool = False) -> str:
        """Everything as plain text for the clipboard: 'Name - link' lines, or only the links."""
        lines = []
        for c in self._need(cid).get("competitors", []):
            if links_only:
                if c.get("link"):
                    lines.append(c["link"])
            else:
                lines.append(" - ".join(x for x in (c.get("name", ""), c.get("link", "")) if x))
        return "\n".join(lines)

    # ---- countries (optional; nothing is shown until one is added)
    def add_country(self, cid: str, name: str) -> str:
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("Type a country name.")
        channel = self._need(cid)
        countries = channel.setdefault("countries", [])
        if any(c.lower() == name.lower() for c in countries):
            raise ValueError("This country is already in the list.")
        countries.append(name)
        self.save()
        return name

    def remove_country(self, cid: str, name: str) -> None:
        channel = self._need(cid)
        channel["countries"] = [c for c in channel.get("countries", []) if c != name]
        for s in channel["scripts"]:
            if s.get("country") == name:
                s["country"] = ""
        self.save()

    # ---- sections inside one script (optional; a script without sections is plain text)
    @staticmethod
    def sections_text(sections: list[dict]) -> str:
        return "\n\n".join(x["text"].strip() for x in sections if x.get("text", "").strip())

    def set_sections(self, cid: str, sid: str, sections: list[dict]) -> dict:
        """Save the sections; the spoken text of the script is always the sections joined in order."""
        clean = [{"title": (x.get("title") or "Section").strip()[:60] or "Section", "text": x.get("text", "")} for x in sections]
        fields = {"sections": clean}
        if clean:
            fields["text"] = self.sections_text(clean)
        return self.update_script(cid, sid, **fields)

    # ---- voice over selection and per-script voice
    def set_vo(self, cid: str, sids: list[str], on: bool = True) -> int:
        n = 0
        for sid in sids:
            script = self.script(cid, sid)
            if script and bool(script.get("vo")) != on:
                script["vo"] = on
                n += 1
        self.save()
        return n

    def vo_scripts(self, cid: str) -> list[dict]:
        return [s for s in self._need(cid)["scripts"] if s.get("vo")]

    # ---- search across the scripts of one channel
    @staticmethod
    def keywords(text: str) -> list[str]:
        """'rome, ancient egypt' -> ['rome', 'ancient egypt'] (commas keep phrases together); without commas every word is a keyword."""
        text = (text or "").strip()
        parts = [p.strip() for p in text.split(",")] if "," in text else text.split()
        seen, out = set(), []
        for p in parts:
            if p and p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        return out

    def search(self, cid: str, text: str, match_all: bool = False, country: str = "") -> dict:
        """{'keywords': [...], 'per_keyword': {kw: scripts}, 'hits': [{'id','title','counts':{kw:n},'total'}]}"""
        words = self.keywords(text)
        result = {"keywords": words, "per_keyword": {w: 0 for w in words}, "hits": []}
        if not words:
            return result
        for s in self._need(cid)["scripts"]:
            if country and s.get("country", "") != country:
                continue
            haystack = ((s.get("title") or "") + "\n" + (s.get("text") or "") + "\n" + (s.get("notes") or "")).lower()
            counts = {w: haystack.count(w.lower()) for w in words}
            for w, n in counts.items():
                if n:
                    result["per_keyword"][w] += 1
            found = [w for w in words if counts[w]]
            if (len(found) == len(words)) if match_all else bool(found):
                result["hits"].append({"id": s["id"], "title": s["title"], "counts": counts, "total": sum(counts.values())})
        result["hits"].sort(key=lambda h: -h["total"])
        return result

    # ---- passcodes: three kinds, each with its own code (stored as a salted hash, never the code itself)
    def locks(self) -> dict:
        return self.data.setdefault("locks", {})

    @staticmethod
    def _hash(code: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()

    def lock_on(self, kind: str) -> bool:
        return bool(self.locks().get(kind, {}).get("hash"))

    def set_passcode(self, kind: str, code: str, channels: list[str] | None = None) -> None:
        if kind not in LOCK_KINDS:
            raise ValueError("Unknown passcode kind.")
        code = code or ""
        if len(code) < 3:
            raise ValueError("Use at least 3 characters for the passcode.")
        salt = secrets.token_hex(16)
        entry = {"salt": salt, "hash": self._hash(code, salt)}
        if kind == "channels":
            entry["channels"] = list(channels or [])
        self.locks()[kind] = entry
        self.save()

    def set_locked_channels(self, channels: list[str]) -> None:
        if not self.lock_on("channels"):
            raise ValueError("Set the passcode for chosen channels first.")
        self.locks()["channels"]["channels"] = list(channels)
        self.save()

    def locked_channels(self) -> list[str]:
        return list(self.locks().get("channels", {}).get("channels", [])) if self.lock_on("channels") else []

    def check_passcode(self, kind: str, code: str) -> bool:
        entry = self.locks().get(kind, {})
        if not entry.get("hash"):
            return True
        try:
            return hmac.compare_digest(self._hash(code or "", entry["salt"]), entry["hash"])
        except (ValueError, KeyError):
            return False

    def remove_passcode(self, kind: str) -> None:
        self.locks().pop(kind, None)
        self.save()
