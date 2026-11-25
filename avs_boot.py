"""Start-up helper that lets the program update itself without a new installer.

It uses only the Python standard library and runs before any program module is loaded:
  1. applies an update, a go-back, or a backup restore that was prepared while the program was running;
  2. if an update layer ("overlay") exists and fits this program, makes Python load its files first;
  3. if the program fails to start twice in a row with an update layer, puts the layer aside so the original program runs.
The update layer lives in the data folder, so the installed program files are never touched.
"""
from __future__ import annotations
import importlib.machinery
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path

API = 1                      # bump only when an update needs a new installer (new libraries etc.)
LAYERS = ("studio", "runtime", "resources")
STAMP = "%Y%m%d-%H%M%S"


def data_root() -> Path:
    home = os.getenv("AI_VOICE_STUDIO_HOME")
    if home:
        base = Path(home)
    elif os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "AVS"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AVS"
    else:
        base = Path.home() / ".local" / "share" / "ai-voice-studio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def updates_dir() -> Path:
    d = data_root() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def overlay_dir() -> Path:
    return data_root() / "app_overlay"


def history_dir() -> Path:
    d = updates_dir() / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_manifest(folder: Path):
    try:
        data = json.loads((Path(folder) / "AVS_UPDATE.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("version") else None
    except (OSError, ValueError):
        return None


def _log(message: str) -> None:
    try:
        with (updates_dir() / "update.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def _boot_state() -> dict:
    try:
        return json.loads((updates_dir() / "boot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_boot(state: dict) -> None:
    try:
        (updates_dir() / "boot.json").write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- loading the update layer
class OverlayFinder:
    """Makes the 'studio' package come from the update layer first, whatever order Python searches in."""

    def __init__(self, folder: Path):
        self.folder = str(folder)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] != "studio":
            return None
        search = [self.folder] if "." not in fullname else path
        if not search:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, search, target)


def _purge_modules() -> None:
    for name in [n for n in sys.modules if n == "studio" or n.startswith("studio.")]:
        del sys.modules[name]


def _vtuple(text: str) -> tuple:
    import re
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:4]) or (0,)


def _read_base_version() -> str:
    try:
        from studio import core
        return str(core.APP_VERSION)
    except Exception:
        return "unknown"
    finally:
        _purge_modules()


def archive_current(reason: str = "") -> Path | None:
    """Move the current update layer into history so it can be restored later."""
    current = overlay_dir()
    manifest = read_manifest(current)
    if not current.is_dir():
        return None
    name = f"{(manifest or {}).get('version', 'unknown')}-{time.strftime(STAMP)}"
    target = history_dir() / name
    counter = 1
    while target.exists():                 # two saves in the same second must not collide
        target = history_dir() / f"{name}-{counter}"
        counter += 1
    shutil.move(str(current), str(target))
    _log(f"Layer {name} saved to history. {reason}")
    return target


def quarantine(reason: str) -> None:
    """Put the update layer aside because it does not work. The original program then runs."""
    current = overlay_dir()
    if current.is_dir():
        bad = updates_dir() / "quarantine"
        bad.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(bad / f"{(read_manifest(current) or {}).get('version', 'unknown')}-{time.strftime(STAMP)}"))
    _save_boot({})
    os.environ.pop("AVS_OVERLAY_DIR", None)
    try:
        (updates_dir() / "last-problem.txt").write_text(reason, encoding="utf-8")
    except OSError:
        pass
    _log("Update layer put aside: " + reason)
    for name in [n for n in sys.meta_path if isinstance(n, OverlayFinder)]:
        sys.meta_path.remove(name)
    _purge_modules()


def activate():
    """Call once, first thing. Returns the manifest of the active update layer, or None."""
    os.environ.setdefault("AVS_BASE_VERSION", _read_base_version())
    if "--reset-updates" in sys.argv:
        quarantine("Reset by the user (--reset-updates).")
    apply_pending()
    folder = overlay_dir()
    manifest = read_manifest(folder)
    if not manifest:
        os.environ.pop("AVS_OVERLAY_DIR", None)
        return None
    # An update older than the program that was just installed must not win over it (a stale layer once kept an old owner key alive).
    base = os.environ.get("AVS_BASE_VERSION", "")
    if base and base != "unknown" and _vtuple(str(manifest.get("version", ""))) < _vtuple(base):
        archive_current(f"The installed program ({base}) is newer than this update ({manifest.get('version')}).")
        _save_boot({})
        os.environ.pop("AVS_OVERLAY_DIR", None)
        return None
    if int(manifest.get("api", 0)) != API:
        quarantine(f"Update {manifest.get('version')} needs a newer installer (needs level {manifest.get('api')}, this program is level {API}).")
        return None
    state = _boot_state()
    if int(state.get("unconfirmed", 0)) >= 2:
        quarantine(f"The program did not start twice in a row with update {manifest.get('version')}.")
        return None
    state["unconfirmed"] = int(state.get("unconfirmed", 0)) + 1
    _save_boot(state)
    _purge_modules()
    sys.meta_path.insert(0, OverlayFinder(folder))
    os.environ["AVS_OVERLAY_DIR"] = str(folder)
    os.environ["AVS_OVERLAY_VERSION"] = str(manifest["version"])
    return manifest


def mark_good() -> None:
    """Call once the window is open: the update layer works."""
    state = _boot_state()
    if state.get("unconfirmed"):
        _save_boot({"unconfirmed": 0})


# ---------------------------------------------------------------- work prepared while the program ran
def pending_path() -> Path:
    return updates_dir() / "pending.json"


def apply_pending() -> str:
    path = pending_path()
    if not path.is_file():
        return ""
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return ""
    done = ""
    try:
        action = job.get("action")
        if action == "install":
            staged = Path(job["staged"])
            if read_manifest(staged):
                archive_current("Replaced by an update.")
                shutil.move(str(staged), str(overlay_dir()))
                _save_boot({})
                done = "Update installed."
        elif action == "rollback":
            target = job.get("id")
            if target == "base":
                archive_current("Went back to the original program.")
                _save_boot({})
                done = "Went back to the original program."
            else:
                source = history_dir() / str(target)
                if source.is_dir() and read_manifest(source) and source.resolve().parent == history_dir().resolve():
                    archive_current("Went back to an earlier version.")
                    shutil.move(str(source), str(overlay_dir()))
                    _save_boot({})
                    done = "Went back to an earlier version."
        elif action == "restore":
            done = restore_backup(Path(job["zip"]))
    except Exception as exc:       # never block start-up
        _log(f"Pending {job.get('action')} failed: {exc}")
        done = "Failed: " + str(exc)[:200]
    finally:
        path.unlink(missing_ok=True)
        if done:
            _log(done)
    return done


def restore_backup(archive: Path) -> str:
    """Copy the Data folder of a backup over the data folder (the current database is kept aside first)."""
    base = data_root().resolve()
    count = 0
    with zipfile.ZipFile(archive) as z:
        members = [i for i in z.infolist() if not i.is_dir() and i.filename.startswith("Data/")]
        if not members:
            raise ValueError("This ZIP does not contain a Data folder.")
        db = base / "state" / "studio.sqlite3"
        if db.is_file():
            shutil.copy2(db, db.with_name("studio.before-restore.sqlite3"))
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        for info in members:
            rel = Path(info.filename[len("Data/"):])
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Unsafe path in backup: " + info.filename)
            target = (base / rel).resolve()
            if base not in target.parents:
                raise ValueError("Unsafe path in backup: " + info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".restoring")
            with z.open(info) as src, tmp.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            os.replace(tmp, target)
            count += 1
    return f"Backup restored ({count} files)."
