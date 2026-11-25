"""Model sizes and update checks: fast, in parallel, and remembered between starts.

Why this exists: the Models page used to look up every model one after the other each time the
program started (with long waits when the internet was slow), so the sizes stayed on "Size pending".
Now:

* the last known sizes are saved on this PC and shown at once,
* missing or old sizes are looked up for all models at the same time (one short request per model),
* a lookup that cannot finish quickly gives up and tries again later instead of blocking.

"Find updates" compares what is on the PC with what the source offers now, for the model files and
for the engine's packages, and lists what can be updated.
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .core import models, subdir

TTL = 7 * 24 * 3600          # a saved size is trusted for a week
WORKERS = 6
_REV = re.compile(r"/resolve/([^/]+)/")


# ------------------------------------------------------------------------------------ saved sizes
def cache_path() -> Path:
    return subdir("state") / "model_sizes.json"


def load() -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def known_total(key: str, data: dict | None = None):
    row = (data if data is not None else load()).get(key)
    return row.get("total") if isinstance(row, dict) else None


def is_fresh(key: str, data: dict) -> bool:
    row = data.get(key)
    return bool(isinstance(row, dict) and row.get("total") and time.time() - float(row.get("when", 0)) < TTL)


def stale_keys(keys, data: dict | None = None) -> list[str]:
    data = load() if data is None else data
    return [k for k in keys if not is_fresh(k, data)]


# ------------------------------------------------------------------------------------ looking things up
def revision_of(items) -> str:
    """The source revision recorded in the file addresses of a download list ('' when there is none)."""
    for item in items or ():
        m = _REV.search(str(item.get("url", "")))
        if m:
            return m.group(1)
    return ""


def _plans(key: str, voices, remote: bool):
    from . import managed
    return [(v, managed.file_manifest(key, v, persist=False, remote=remote)) for v in voices]


def lookup_size(key: str, voices) -> dict:
    """Total size of the model files for one model (quick lookup; raises when the source cannot be reached)."""
    from . import downloads
    with downloads.fast_mode():
        plans = _plans(key, voices, remote=True)
    items = [i for _, plan in plans for i in plan]
    sized = bool(items) and all(i.get("size") is not None for i in items)
    return {"total": sum(i["size"] for i in items) if sized else None, "files": len(items), "rev": revision_of(items), "when": time.time()}


def refresh_sizes(keys, voices_of, on_result=lambda key, row: None, should_stop=lambda: False) -> dict:
    """Look up the sizes of many models at once. Saves what was found. Models that fail keep their old value."""
    data = load()
    found = {}

    def one(key):
        return key, lookup_size(key, voices_of(key))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(one, k) for k in keys]
        for fut in as_completed(futures):
            if should_stop():
                for other in futures:
                    other.cancel()
                break
            try:
                key, row = fut.result()
            except Exception:
                continue
            if row.get("total"):
                data[key] = row
                found[key] = row
                on_result(key, row)
    if found:
        try:
            save(data)
        except OSError:
            pass
    return found


# ------------------------------------------------------------------------------------ updates
def local_state(key: str, voices) -> dict:
    """What is on this PC for one model: are all files there, and which source revision were they taken from."""
    from . import managed, services
    complete = all(managed.downloaded(key, v) for v in voices)
    saved = []
    for v in voices:
        try:
            saved += json.loads(managed.manifest_path(key, v).read_text(encoding="utf-8")) or []
        except (OSError, ValueError):
            pass
    return {"complete": complete, "rev": revision_of(saved), "folder": {v: services.model_folder(key, v) for v in voices}}


def check_update(key: str, voices) -> dict | None:
    """None when the model is not installed. Otherwise what can be updated: files and/or engine packages."""
    from . import downloads, managed
    state = local_state(key, voices)
    if not state["complete"]:
        return None
    with downloads.fast_mode():
        plans = _plans(key, voices, remote=True)
    changed, size, remote_rev = [], 0, ""
    for voice, plan in plans:
        folder = state["folder"][voice]
        remote_rev = remote_rev or revision_of(plan)
        for item in plan:
            dest = folder / item["name"]
            try:
                same = dest.is_file() and (item.get("size") is None or dest.stat().st_size == item["size"])
            except OSError:
                same = False
            if not same:
                changed.append(item["name"])
                size += int(item.get("size") or 0)
    if state["rev"] and remote_rev and state["rev"] != remote_rev and not changed:
        changed.append("newer version")
    deps = not managed.marker_current(key)
    if not changed and not deps:
        return {"files": 0, "bytes": 0, "deps": False}
    return {"files": len(changed), "bytes": size, "deps": deps}


def find_updates(keys, voices_of, on_result=lambda key, row: None, should_stop=lambda: False):
    """Check every installed model at the same time. Returns ({key: result} for installed models, number that could not be checked)."""
    results, failed = {}, 0

    def one(key):
        return key, check_update(key, voices_of(key))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(one, k) for k in keys]
        for fut in as_completed(futures):
            if should_stop():
                for other in futures:
                    other.cancel()
                break
            try:
                key, row = fut.result()
            except Exception:
                failed += 1
                continue
            if row is not None:
                results[key] = row
                on_result(key, row)
    return results, failed


def prepare_update(key: str, voices) -> None:
    """Forget the saved file list so the next download asks the source for the current list (only changed files are fetched)."""
    from . import managed
    for v in voices:
        managed.manifest_path(key, v).unlink(missing_ok=True)


def describe(row: dict) -> str:
    parts = []
    if row.get("files"):
        parts.append(f"new files {row['bytes'] / 1e9:.2f} GB" if row.get("bytes") else "new files")
    if row.get("deps"):
        parts.append("engine packages")
    return " + ".join(parts)
