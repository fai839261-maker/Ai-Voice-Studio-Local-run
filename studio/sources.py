"""Voice model repositories: add a GitHub or Hugging Face repository and have it checked online.

A check reads public metadata only (licence, tags, size, last update). It never runs repository code.
Adding a repository does not make an arbitrary model runnable: only engines with an adapter in this
app, and standard VITS/MMS voices, can be used. Everything else is reported honestly.
"""
from __future__ import annotations
import json
import os
import re
import time
from urllib.parse import urlsplit

from .core import subdir, models

REPO = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}"
OPEN = {"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "cc0-1.0", "unlicense", "isc"}
DAY = 86400


def parse_source(value: str) -> tuple[str, str]:
    """('hf'|'github', 'owner/name') from a URL, 'hf:owner/name', 'gh:owner/name' or plain owner/name (Hugging Face)."""
    v = value.strip().rstrip("/")
    kind = "hf"
    if v.lower().startswith(("gh:", "github:")):
        kind, v = "github", v.split(":", 1)[1]
    elif v.lower().startswith(("hf:", "huggingface:")):
        v = v.split(":", 1)[1]
    elif "://" in v:
        u = urlsplit(v)
        if u.scheme != "https" or u.query or u.fragment or u.username:
            raise ValueError("Use a plain https link to a repository.")
        host = (u.hostname or "").lower()
        parts = [p for p in u.path.split("/") if p]
        if host in ("github.com", "www.github.com"):
            kind = "github"
        elif host in ("huggingface.co", "www.huggingface.co"):
            if parts[:1] in (["datasets"], ["spaces"]):
                raise ValueError("Add a model repository, not a dataset or Space.")
        else:
            raise ValueError("Only github.com and huggingface.co repositories are supported.")
        v = "/".join(parts[:2])
    if v.endswith(".git"):
        v = v[:-4]
    if not re.fullmatch(REPO, v) or ".." in v:
        raise ValueError("Use owner/name, for example owner/voice-model.")
    return kind, v


def url_for(kind: str, repo: str) -> str:
    return ("https://github.com/" if kind == "github" else "https://huggingface.co/") + repo


def license_class(value: str) -> str:
    v = (value or "").lower()
    if v in OPEN:
        return "open"
    if not v or v in ("unknown", "other", "none", "null"):
        return "unknown"
    if "nc" in re.split(r"[-_. ]", v) or any(x in v for x in ("non-commercial", "noncommercial", "cpml", "coqui", "research")):
        return "noncommercial"
    return "restricted"


LICENSE_TEXT = {
    "open": "Open licence (commercial use is normally allowed; read the terms).",
    "noncommercial": "Non-commercial or research licence: do not use it in a paid product without permission.",
    "restricted": "Licence has conditions. Read it before using the voices.",
    "unknown": "Licence not clearly stated. Read the repository before using it.",
}


def known_engine(kind: str, repo: str):
    """Catalog engine that already uses this repository, if any."""
    low = repo.lower()
    for m in models().values():
        if kind == "hf" and m.get("hf_repo", "").lower() == low:
            return m
        if kind == "github" and urlsplit(m.get("repo", "")).path.strip("/").lower() == low:
            return m
        if any(v.get("repo", "").lower() == low for v in m.get("voices", [])) and kind == "hf":
            return m
    return None


def check(kind: str, repo: str, fetch) -> dict:
    """Read public metadata and return a plain-language report. fetch(url) -> parsed JSON."""
    notes: list[str] = []
    report = {"kind": kind, "repo": repo, "url": url_for(kind, repo), "checked": time.time(), "notes": notes}
    if kind == "github":
        info = fetch("https://api.github.com/repos/" + repo)
        spdx = ((info.get("license") or {}).get("spdx_id") or "")
        report.update(license=spdx if spdx != "NOASSERTION" else "", updated=info.get("pushed_at", ""), stars=info.get("stargazers_count"),
                      about=info.get("description") or "", archived=bool(info.get("archived")), gated=False, size_gb=None,
                      tags=list(info.get("topics") or []))
        if info.get("archived"):
            notes.append("This repository is archived (no longer maintained).")
    else:
        info = fetch("https://huggingface.co/api/models/" + repo + "?blobs=true")
        card = info.get("cardData") or {}
        weights = [s.get("size") or 0 for s in info.get("siblings", []) if str(s.get("rfilename", "")).endswith((".safetensors", ".bin", ".pt", ".pth", ".onnx", ".ckpt"))]
        report.update(license=str(card.get("license") or next((t[8:] for t in info.get("tags", []) if t.startswith("license:")), "")),
                      updated=info.get("lastModified", ""), downloads=info.get("downloads"), about="", archived=False,
                      gated=bool(info.get("gated")) or bool(info.get("private")), size_gb=round(sum(weights) / 1e9, 2) if weights else None,
                      tags=list(info.get("tags") or [])[:12], pipeline=info.get("pipeline_tag", ""))
        if report["gated"]:
            notes.append("This model is gated or private. This app only uses public, ungated models.")
        if report["size_gb"] and report["size_gb"] > 8:
            notes.append(f"Large download: about {report['size_gb']} GB of weights.")
        tagged = report.get("pipeline") == "text-to-speech" or "text-to-speech" in report["tags"] or "tts" in report["tags"]
        if not tagged:
            notes.append("Not tagged as text-to-speech.")
    report["license_class"] = license_class(report.get("license", ""))
    report["license_text"] = LICENSE_TEXT[report["license_class"]]
    engine = known_engine(kind, repo)
    importable = False
    if engine:
        report["verdict"], report["verdict_text"] = "supported", f"Already supported as '{engine['name']}'. Use it from AI Setup."
        report["engine"] = engine["id"]
    elif report.get("gated"):
        report["verdict"], report["verdict_text"] = "blocked", "Cannot be used: gated or private."
    elif kind == "hf" and any(t in report["tags"] for t in ("vits", "mms")) or (kind == "hf" and "vits" in repo.lower()):
        try:
            config = fetch(f"https://huggingface.co/{repo}/resolve/main/config.json")
            importable = config.get("model_type") == "vits" and not config.get("auto_map")
        except Exception:
            importable = False
        if importable:
            report["verdict"], report["verdict_text"] = "importable", "Standard VITS voice. It can be added as a voice and tested."
        else:
            report["verdict"], report["verdict_text"] = "needs_adapter", "Looks like a VITS variant this app cannot load safely yet."
    else:
        report["verdict"], report["verdict_text"] = "needs_adapter", ("Recognised as a voice project, but this app has no adapter for it yet. "
                                                                    "It is saved and re-checked, and can be turned into an engine later.")
    return report


def _path():
    return subdir("state") / "voice-sources.json"


def saved() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("repo") and x.get("kind") in ("hf", "github")] if isinstance(data, list) else []


def _write(items: list[dict]) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    os.replace(tmp, _path())


def add(value: str, fetch) -> dict:
    kind, repo = parse_source(value)
    report = check(kind, repo, fetch)
    items = [x for x in saved() if not (x["kind"] == kind and x["repo"].lower() == repo.lower())]
    items.append(report)
    _write(items)
    return report


def remove(kind: str, repo: str) -> None:
    _write([x for x in saved() if not (x["kind"] == kind and x["repo"].lower() == repo.lower())])


def recheck_all(fetch) -> list[str]:
    """Re-read every saved repository. Returns short descriptions of what changed."""
    changes, items = [], []
    for old in saved():
        try:
            new = check(old["kind"], old["repo"], fetch)
        except Exception as exc:
            old["notes"] = [n for n in old.get("notes", []) if not n.startswith("Could not")] + [f"Could not check: {exc}"]
            items.append(old)
            continue
        if new.get("license") != old.get("license"):
            changes.append(f"{old['repo']}: licence changed from '{old.get('license') or 'none'}' to '{new.get('license') or 'none'}'")
        if new.get("verdict") != old.get("verdict"):
            changes.append(f"{old['repo']}: {new['verdict_text']}")
        if new.get("updated") and new.get("updated") != old.get("updated"):
            changes.append(f"{old['repo']}: updated")
        items.append(new)
    _write(items)
    return changes


def auto_due(store, now: float | None = None, interval_days: float = 7) -> bool:
    if not store.setting("sources_auto_check", True):
        return False
    return (now or time.time()) - float(store.setting("sources_last_auto", 0)) >= interval_days * DAY


def builtin() -> list[dict]:
    """Repositories behind the built-in engines (read-only list)."""
    seen, rows = set(), []
    for m in models().values():
        for kind, repo in (("hf", m.get("hf_repo", "")), ("github", urlsplit(m.get("repo", "")).path.strip("/"))):
            if repo and (kind, repo) not in seen and re.fullmatch(REPO, repo):
                seen.add((kind, repo))
                rows.append({"kind": kind, "repo": repo, "url": url_for(kind, repo), "engine": m["name"]})
    return rows
