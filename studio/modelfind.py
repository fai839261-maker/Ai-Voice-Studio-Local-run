"""Find AI model files that are already on this PC or on a drive (for example from a backup folder) and connect them."""
from __future__ import annotations
import os
import shutil
import string
from pathlib import Path

from .core import root

FOLDERS = ("models", "wheels", "hf_cache", "torch_cache", "pip_cache", "native")


def looks_like_data(folder: Path) -> list[str]:
    """Which model folders (with something in them) this folder holds."""
    found = []
    for name in FOLDERS:
        sub = Path(folder) / name
        try:
            if sub.is_dir() and any(sub.iterdir()):
                found.append(name)
        except OSError:
            pass
    return found


def _drives() -> list[Path]:
    if os.name == "nt":
        return [Path(f"{letter}:/") for letter in string.ascii_uppercase if Path(f"{letter}:/").exists()]
    return [Path("/media"), Path("/mnt")]


def candidates(extra: list | None = None, depth: int = 3, limit: int = 12) -> list[dict]:
    """Folders that hold model files: a backup's Data folder, an old AVS folder, a copied models folder. The current data folder is skipped."""
    here = root().resolve()
    roots = [Path.home() / x for x in ("Desktop", "Downloads", "Documents")] + _drives() + [Path(p) for p in (extra or [])]
    seen, out = set(), []

    def visit(folder: Path, level: int):
        if len(out) >= limit:
            return
        try:
            resolved = folder.resolve()
        except OSError:
            return
        if resolved in seen or resolved == here or here in resolved.parents:
            return
        seen.add(resolved)
        for target in (folder, folder / "Data", folder / "AVS"):
            found = looks_like_data(target) if target.is_dir() else []
            if found and target.resolve() not in {c["path"] for c in out}:
                out.append({"path": target.resolve(), "found": found})
        if level >= depth:
            return
        try:
            children = [c for c in folder.iterdir() if c.is_dir() and not c.name.startswith((".", "$")) and c.name not in ("Windows", "Program Files", "Program Files (x86)", "ProgramData", "AppData")]
        except OSError:
            return
        for child in children[:80]:
            visit(child, level + 1)

    for base in roots:
        if base.is_dir():
            visit(base, 0)
    return out[:limit]


def plan(source: Path, target: Path | None = None) -> dict:
    """{'files': n, 'bytes': n} that would be copied (files missing here or of a different size)."""
    target = Path(target or root())
    files = size = 0
    for name in FOLDERS:
        base = Path(source) / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                dest = target / name / path.relative_to(base)
                try:
                    if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
                        files += 1
                        size += path.stat().st_size
                except OSError:
                    pass
    return {"files": files, "bytes": size}


class Cancelled(Exception):
    pass


def connect(source: Path, progress=None, cancel=None, target: Path | None = None) -> int:
    """Copy the model files that are missing here. Existing identical files are left alone. Returns the number copied."""
    target = Path(target or root())
    todo = []
    for name in FOLDERS:
        base = Path(source) / name
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file():
                    dest = target / name / path.relative_to(base)
                    try:
                        if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
                            todo.append((path, dest))
                    except OSError:
                        pass
    total = sum(p.stat().st_size for p, _ in todo) or 1
    free = shutil.disk_usage(target).free
    if free < total * 1.02:
        raise RuntimeError(f"Not enough free space: the models need {total / 1e9:.1f} GB and {free / 1e9:.1f} GB is free.")
    done = count = 0
    for src, dest in todo:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".copying")
        try:
            shutil.copyfile(src, part)
            os.replace(part, dest)
        finally:
            part.unlink(missing_ok=True)
        done += src.stat().st_size
        count += 1
        if progress:
            progress(min(1.0, done / total), dest.name)
    return count
