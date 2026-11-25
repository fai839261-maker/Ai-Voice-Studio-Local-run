"""Backup permits: a signed note, made by the owner, that lets one copy of the software make backups."""
from __future__ import annotations
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import ed25519
from .core import root

NAME = "backup_permit.json"


def path() -> Path:
    folder = root() / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / NAME


def _body(token: dict) -> bytes:
    return json.dumps(token, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def make(secret: bytes, label: str = "") -> dict:
    """Sign a permit with the owner key. The label is only a name for the owner's own records."""
    token = {"v": 1, "perm": "backup", "label": (label or "").strip()[:60], "id": secrets.token_hex(6),
             "issued": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    return {"token": token, "signature": ed25519.sign(secret, _body(token)).hex()}


def verify(data, owner_public_hex: str) -> bool:
    try:
        token, signature = data["token"], bytes.fromhex(data["signature"])
        if token.get("v") != 1 or token.get("perm") != "backup":
            return False
        return ed25519.verify(bytes.fromhex(owner_public_hex), _body(token), signature)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def load(file: Path | None = None):
    try:
        return json.loads(Path(file or path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(data: dict, file: Path | None = None) -> None:
    target = Path(file or path())
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, target)
