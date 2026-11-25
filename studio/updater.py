"""Update files, the update layer, go-back history, and checking GitHub for a new version.

An update file is a small signed ZIP with the program's Python files. It is installed WITHOUT Inno Setup:
the files are placed in a layer in the data folder (see avs_boot.py) and take effect after a restart.
A new installer is only needed when the program's libraries change (the update file says so).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import avs_boot
from . import ed25519
from .core import APP_VERSION, bundled, root

FORMAT = 1
NAME = "AI Voice Studio"
TOP = ("studio/", "runtime/", "resources/")
SKIP_FILES = {"AVS_Move_Helper.exe"}
MAX_FILES, MAX_BYTES = 5000, 300 * 1024 * 1024
REPO = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}"
HOSTS = {"github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
         "github-releases.githubusercontent.com"}
DEFAULT_REPO = "fai839261-maker/Ai-Voice-Studio-Local-run"      # where every copy looks for updates (0.22)
ASSET = re.compile(r"AVS_Update_(\d+(?:\.\d+){1,3})\.zip", re.IGNORECASE)
PROJECT_ASSET = re.compile(r"AI_Voice_Studio_(?:COMPLETE|UPDATE)_(\d+(?:_\d+){1,3})[^/\\]*\.zip", re.IGNORECASE)


class UpdateError(RuntimeError):
    """A problem with an update, worded for the person using the software."""


def vtuple(text: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:4])


def is_newer(candidate: str, current: str) -> bool:
    a, b = vtuple(candidate), vtuple(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


# ------------------------------------------------------------------ versions and layers
def base_version() -> str:
    return os.getenv("AVS_BASE_VERSION") or APP_VERSION


def active_layer():
    return avs_boot.read_manifest(avs_boot.overlay_dir())


def source_dirs() -> dict:
    """Where the files that make up the running program can be read from."""
    layer = os.getenv("AVS_OVERLAY_DIR")
    if layer and (Path(layer) / "studio").is_dir():
        return {name: Path(layer) / name for name in avs_boot.LAYERS}
    studio = bundled("studio_src") if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return {"studio": studio, "runtime": bundled("runtime"), "resources": bundled("resources")}


def _files(dirs: dict):
    for layer, base in dirs.items():
        if not Path(base).is_dir():
            continue
        for path in sorted(Path(base).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo") or path.name in SKIP_FILES:
                continue
            yield f"{layer}/{path.relative_to(base).as_posix()}", path


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


# ------------------------------------------------------------------ publisher key
def key_file() -> Path:
    folder = root() / "config"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "update_signing.key"


def signing_secret(create: bool = False) -> bytes | None:
    path = key_file()
    if path.is_file():
        try:
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        except ValueError:
            return None
    if not create:
        return None
    secret = ed25519.generate_secret()
    path.write_text(secret.hex(), encoding="ascii")
    return secret


def own_public_hex() -> str:
    secret = signing_secret()
    return ed25519.public_key(secret).hex() if secret else ""


def pinned_key(store) -> str:
    return str(store.setting("update_trusted_key", "") or "")


def pin_key(store, public_hex: str) -> None:
    store.set_setting("update_trusted_key", public_hex)


def fingerprint_of(public_hex: str) -> str:
    return ed25519.fingerprint(bytes.fromhex(public_hex))


# ------------------------------------------------------------------ making an update file
def make_update_file(store, dest_folder: Path, notes: str = "") -> Path:
    """Package the running program's files into a signed AVS_Update_<version>.zip."""
    secret = signing_secret(create=True)
    public = ed25519.public_key(secret).hex()
    files = {}
    entries = list(_files(source_dirs()))
    if not any(a.startswith("studio/") for a, _ in entries):
        raise UpdateError("The program's source files were not found, so an update file cannot be made from this copy.")
    for arc, path in entries:
        files[arc] = _sha(path)
    manifest = {"format": FORMAT, "name": NAME, "version": APP_VERSION, "api": avs_boot.API,
                "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "notes": notes.strip()[:2000],
                "files": files, "public_key": public}
    signature = ed25519.sign(secret, canonical(manifest)).hex()
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    final = dest_folder / f"AVS_Update_{APP_VERSION}.zip"
    partial = final.with_name(final.name + ".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("AVS_UPDATE.json", json.dumps(manifest, indent=1, sort_keys=True))
            z.writestr("AVS_UPDATE.sig", signature)
            for arc, path in entries:
                z.write(path, "payload/" + arc)
        os.replace(partial, final)
    finally:
        partial.unlink(missing_ok=True)
    final.with_name(final.name + ".sha256.txt").write_text(_sha(final) + "  " + final.name + "\n", encoding="ascii")
    if not pinned_key(store):
        pin_key(store, public)
    return final


# ------------------------------------------------------------------ checking and installing an update file
def inspect_update_file(path: Path) -> dict:
    """Open an update file, check every file and the signature. Raises UpdateError with a plain message."""
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise UpdateError("This is not a valid update file.")
    with z:
        names = set(z.namelist())
        if not {"AVS_UPDATE.json", "AVS_UPDATE.sig"} <= names:
            raise UpdateError("This ZIP is not an AI Voice Studio update file.")
        try:
            manifest = json.loads(z.read("AVS_UPDATE.json").decode("utf-8"))
            signature = bytes.fromhex(z.read("AVS_UPDATE.sig").decode("ascii").strip())
            public = bytes.fromhex(manifest["public_key"])
        except (ValueError, KeyError, UnicodeDecodeError):
            raise UpdateError("The update file's information is damaged.")
        if manifest.get("format") != FORMAT or manifest.get("name") != NAME or not manifest.get("version"):
            raise UpdateError("This update file is from a different kind of software or a newer format.")
        if not ed25519.verify(public, canonical(manifest), signature):
            raise UpdateError("The update file's signature is not valid. It may have been changed. Do not install it.")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
            raise UpdateError("The update file lists no usable files.")
        payload = {n[len("payload/"):] for n in names if n.startswith("payload/") and not n.endswith("/")}
        if payload != set(files):
            raise UpdateError("The update file's contents do not match its list.")
        total = 0
        for arc, expected in files.items():
            if not arc.startswith(TOP) or ".." in arc.split("/") or arc.startswith("/") or "\\" in arc or ":" in arc:
                raise UpdateError("The update file contains a file in a place it must not touch: " + arc)
            info = z.getinfo("payload/" + arc)
            total += info.file_size
            if total > MAX_BYTES:
                raise UpdateError("The update file is unexpectedly large.")
            h = hashlib.sha256()
            with z.open(info) as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != expected:
                raise UpdateError("A file inside the update is damaged: " + arc)
    return {"manifest": manifest, "version": str(manifest["version"]), "api": int(manifest.get("api", 0)),
            "public_key": manifest["public_key"], "fingerprint": ed25519.fingerprint(public), "notes": manifest.get("notes", ""),
            "files": len(files)}


def stage_install(store, path: Path, trust=lambda fingerprint: False) -> dict:
    """Check an update file and prepare it. It takes effect after the program is restarted."""
    info = inspect_update_file(path)
    if info["api"] != avs_boot.API:
        raise UpdateError("This update needs a new setup file, not just an update file. Ask the person who gave it to you for the installer.")
    pinned = pinned_key(store)
    if pinned and pinned != info["public_key"]:
        raise UpdateError("This update was signed by a different publisher than the one you trust. It was not installed.")
    if not pinned:
        if not trust(info["fingerprint"]):
            raise UpdateError("Update cancelled.")
        pin_key(store, info["public_key"])
    if not is_newer(info["version"], APP_VERSION):
        raise UpdateError(f"This update (version {info['version']}) is not newer than the version you have ({APP_VERSION}). "
                          "To use an older version, choose it under Go back.")
    staging_root = avs_boot.updates_dir() / "staging"
    shutil.rmtree(staging_root, ignore_errors=True)
    folder = staging_root / uuid.uuid4().hex[:10]
    folder.mkdir(parents=True)
    with zipfile.ZipFile(path) as z:
        for arc in info["manifest"]["files"]:
            target = folder / arc
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open("payload/" + arc) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
        (folder / "AVS_UPDATE.json").write_bytes(z.read("AVS_UPDATE.json"))
        (folder / "AVS_UPDATE.sig").write_bytes(z.read("AVS_UPDATE.sig"))
    avs_boot.pending_path().write_text(json.dumps({"action": "install", "staged": str(folder), "version": info["version"]}), encoding="utf-8")
    return info


def pending() -> dict | None:
    try:
        job = json.loads(avs_boot.pending_path().read_text(encoding="utf-8"))
        return job if isinstance(job, dict) else None
    except (OSError, ValueError):
        return None


def cancel_pending() -> None:
    avs_boot.pending_path().unlink(missing_ok=True)
    shutil.rmtree(avs_boot.updates_dir() / "staging", ignore_errors=True)


# ------------------------------------------------------------------ the complete software as a ZIP or an extracted folder
def _project_root(folder: Path) -> Path:
    """The folder that holds AVS_SOURCE_UPDATE.json: this folder, or the single project folder inside it."""
    from .source_update import MANIFEST
    folder = Path(folder)
    if (folder / MANIFEST).is_file():
        return folder
    inner = [d for d in folder.iterdir() if d.is_dir() and (d / MANIFEST).is_file()] if folder.is_dir() else []
    if len(inner) == 1:
        return inner[0]
    raise UpdateError("This folder is not an AI Voice Studio update. Choose the folder that contains main.py and the studio folder "
                      "(the extracted project folder).")


def _zip_of_folder(root_dir: Path) -> Path:
    """Pack only the files that belong to the software, so build leftovers in the folder are ignored."""
    from .source_update import DIRS, ROOT_FILES
    out = Path(tempfile.mkdtemp(prefix="avs-folder-")) / "project.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in sorted(root_dir.iterdir()):
            if entry.is_file() and entry.name in ROOT_FILES:
                z.write(entry, entry.name)
            elif entry.is_dir() and entry.name in DIRS:
                for path in sorted(entry.rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
                        z.write(path, path.relative_to(root_dir).as_posix())
    return out


def _filtered_zip(archive: Path) -> Path:
    """Keep only the software's own files from a ZIP, so a project that was zipped together with build leftovers still works."""
    from .source_update import DIRS, MANIFEST, ROOT_FILES
    with zipfile.ZipFile(archive) as z:
        files = [i for i in z.infolist() if not i.is_dir()]
        manifests = [i.filename for i in files if i.filename.split("/")[-1] == MANIFEST]
        if len(manifests) != 1:
            raise UpdateError("This ZIP is not an AI Voice Studio update (AVS_SOURCE_UPDATE.json was not found inside).")
        prefix = manifests[0][: -len(MANIFEST)]
        out = Path(tempfile.mkdtemp(prefix="avs-zip-")) / "project.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
            for info in files:
                if not info.filename.startswith(prefix):
                    continue
                rel = info.filename[len(prefix):]
                parts = rel.split("/")
                if any(x in ("", "..") for x in parts) or ":" in rel or "\\" in rel or rel.startswith("/"):
                    raise UpdateError("The ZIP contains an unsafe file name: " + rel)
                if (len(parts) == 1 and parts[0] in ROOT_FILES) or (len(parts) > 1 and parts[0] in DIRS and "__pycache__" not in parts
                                                                    and not rel.endswith((".pyc", ".pyo"))):
                    w.writestr(rel, z.read(info))
    return out


def _api_of(project: Path) -> int:
    try:
        match = re.search(r"^API\s*=\s*(\d+)", (project / "avs_boot.py").read_text(encoding="utf-8"), re.MULTILINE)
        return int(match.group(1)) if match else avs_boot.API
    except OSError:
        return avs_boot.API


def stage_project(store, path: Path) -> dict:
    """Check the complete software (a ZIP or an extracted folder) and prepare it as an update. It starts after a restart."""
    from . import source_update
    path = Path(path)
    cleanup = []
    try:
        if path.is_dir():
            archive = _zip_of_folder(_project_root(path))
            cleanup.append(archive.parent)
        else:
            if not zipfile.is_zipfile(path):
                raise UpdateError("This is not a ZIP file or a folder.")
            archive = _filtered_zip(path)
            cleanup.append(archive.parent)
        try:
            package = source_update.stage_source(archive)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise UpdateError("This is not an AI Voice Studio update, or it is damaged: " + str(exc)) from exc
        project, version = Path(package.project), str(package.version)
        cleanup.append(project.parent)
        if _api_of(project) != avs_boot.API:
            raise UpdateError("This version needs a new setup file (a new build), not just an update. Build and install it the normal way once.")
        if not is_newer(version, APP_VERSION):
            raise UpdateError(f"This is version {version}. You already have version {APP_VERSION}, so there is nothing to update.")
        if not (project / "studio" / "__init__.py").is_file():
            raise UpdateError("The update has no studio/__init__.py file, so it was not installed.")
        staging_root = avs_boot.updates_dir() / "staging"
        shutil.rmtree(staging_root, ignore_errors=True)
        folder = staging_root / uuid.uuid4().hex[:10]
        files = {}
        for layer in avs_boot.LAYERS:
            base = project / layer
            if not base.is_dir():
                continue
            for src in sorted(base.rglob("*")):
                if not src.is_file() or "__pycache__" in src.parts or src.suffix in (".pyc", ".pyo") or src.name in SKIP_FILES:
                    continue
                arc = f"{layer}/{src.relative_to(base).as_posix()}"
                if src.suffix == ".py":
                    try:
                        compile(src.read_text(encoding="utf-8"), arc, "exec", dont_inherit=True)
                    except (SyntaxError, UnicodeDecodeError) as exc:
                        raise UpdateError(f"The file {arc} has a mistake, so the update was not installed ({exc.__class__.__name__}).") from exc
                target = folder / arc
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                files[arc] = _sha(target)
        if not files:
            raise UpdateError("The update contains no program files.")
        manifest = {"format": FORMAT, "name": NAME, "version": version, "api": avs_boot.API, "source": "project", "unsigned": True,
                    "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
        (folder / "AVS_UPDATE.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        avs_boot.pending_path().write_text(json.dumps({"action": "install", "staged": str(folder), "version": version}), encoding="utf-8")
        return {"manifest": manifest, "version": version, "api": avs_boot.API, "public_key": "", "fingerprint": "", "notes": "", "files": len(files)}
    finally:
        for item in cleanup:
            shutil.rmtree(item, ignore_errors=True)


def stage_any(store, path: Path, trust=lambda fingerprint: False) -> dict:
    """Install from whatever was chosen: a signed update file, the complete software ZIP, or its extracted folder."""
    path = Path(path)
    if path.is_dir():
        return stage_project(store, path)
    try:
        with zipfile.ZipFile(path) as z:
            signed = "AVS_UPDATE.json" in z.namelist()
    except (OSError, zipfile.BadZipFile):
        raise UpdateError("This is not a valid ZIP file.")
    return stage_install(store, path, trust) if signed else stage_project(store, path)


# ------------------------------------------------------------------ go back
def history() -> list[dict]:
    """Versions that can be restored, newest first."""
    rows = []
    for folder in avs_boot.history_dir().iterdir():
        manifest = avs_boot.read_manifest(folder) if folder.is_dir() else None
        if manifest:
            saved = datetime.fromtimestamp(folder.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            rows.append({"id": folder.name, "version": str(manifest["version"]), "saved": saved, "ts": folder.stat().st_mtime})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    if active_layer():
        rows.append({"id": "base", "version": base_version(), "saved": "original program", "ts": 0})
    return rows


def request_rollback(entry_id: str) -> None:
    if entry_id != "base" and not (avs_boot.history_dir() / entry_id).is_dir():
        raise UpdateError("That version is no longer saved.")
    avs_boot.pending_path().write_text(json.dumps({"action": "rollback", "id": entry_id}), encoding="utf-8")


# ------------------------------------------------------------------ GitHub
def clean_repo(value: str) -> str:
    v = (value or "").strip().rstrip("/")
    if v.lower().startswith("https://github.com/"):
        v = "/".join(urlsplit(v).path.strip("/").split("/")[:2])
    if v.endswith(".git"):
        v = v[:-4]
    if not re.fullmatch(REPO, v):
        raise UpdateError("Write the GitHub address as owner/name, for example unzipspark/voice-studio.")
    return v


def _release_from_page(repo: str, first_error: Exception | None = None) -> dict:
    """GitHub limits how often a program may ask its API (error 403, rate limit). The normal web pages have no such limit, so read the release from them."""
    from . import downloads

    def page(url: str):
        with downloads.open_secure(downloads.request(url, {"Accept": "text/html"}), timeout=30) as response:
            if urlsplit(response.geturl()).hostname not in HOSTS:
                raise UpdateError("GitHub sent the request to another website, so it was stopped.")
            return response.geturl(), response.read(3_000_000).decode("utf-8", "replace")
    try:
        final, _ = page(f"https://github.com/{repo}/releases/latest")
        match = re.search(r"/releases/tag/([^/?#]+)", final)
        if not match:
            return {"assets": []}
        tag = unquote(match.group(1))
        _, body = page(f"https://github.com/{repo}/releases/expanded_assets/{quote(tag)}")
    except UpdateError:
        raise
    except Exception as exc:
        raise (first_error or exc)
    links = list(re.finditer(r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"', body, re.IGNORECASE))
    assets, seen = [], set()
    for i, link in enumerate(links):
        name = unquote(link.group(1).rsplit("/", 1)[-1])
        if name in seen:
            continue
        seen.add(name)
        end = links[i + 1].start() if i + 1 < len(links) else len(body)
        digest = re.search(r"sha256:([0-9a-fA-F]{64})", body[link.end():end])
        assets.append({"name": name, "browser_download_url": "https://github.com" + link.group(1), "size": 0,
                       "digest": ("sha256:" + digest.group(1)) if digest else ""})
    return {"tag_name": tag, "html_url": f"https://github.com/{repo}/releases/tag/{quote(tag)}", "body": "", "assets": assets}


def latest_release(repo: str, fetch=None) -> dict | None:
    """The newest GitHub release that carries an AVS_Update_<version>.zip file, or None."""
    repo = clean_repo(repo)
    if fetch is None:
        from .downloads import fetch_json
        try:
            data = fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception as exc:                      # most often: HTTP 403 rate limit
            data = _release_from_page(repo, exc)
    else:
        data = fetch(f"https://api.github.com/repos/{repo}/releases/latest")
    best = None
    for asset in data.get("assets", []) or []:
        name = str(asset.get("name", ""))
        match = ASSET.fullmatch(name)
        version = match.group(1) if match else None
        if not version:
            alt = PROJECT_ASSET.fullmatch(name)
            version = alt.group(1).replace("_", ".") if alt else None
        if version and (best is None or is_newer(version, best["version"])):
            best = {"version": version, "name": asset["name"], "url": asset.get("browser_download_url", ""),
                    "sha256": str(asset.get("digest") or "").lower().removeprefix("sha256:") if str(asset.get("digest") or "").lower().startswith("sha256:") else "",
                    "size": int(asset.get("size") or 0), "tag": data.get("tag_name", ""), "notes": str(data.get("body") or "")[:2000],
                    "page": data.get("html_url", "")}
    return best


def update_repo() -> str:
    """The GitHub address updates come from. Fixed in the program; AVS_UPDATE_REPO only exists for tests."""
    return os.environ.get("AVS_UPDATE_REPO") or DEFAULT_REPO


def check_online(store, fetch=None) -> dict:
    """{'status': 'no_repo'|'none'|'current'|'newer', 'release': {...}}  (raises on network trouble)."""
    repo = update_repo()
    release = latest_release(repo, fetch)
    store.set_setting("update_last_check", time.time())
    if not release:
        return {"status": "none"}
    if is_newer(release["version"], APP_VERSION):
        store.set_setting("update_available", release["version"])
        return {"status": "newer", "release": release}
    store.set_setting("update_available", "")
    return {"status": "current", "release": release}


def auto_check_due(store, now: float | None = None, interval_hours: float = 24) -> bool:
    if not store.setting("update_auto_check", True):
        return False
    return (now or time.time()) - float(store.setting("update_last_check", 0) or 0) >= interval_hours * 3600


def download_release(release: dict, dest_folder: Path, progress=None, cancel=None) -> Path:
    """Download the update file from GitHub over HTTPS (only GitHub's own hosts are accepted)."""
    from . import downloads
    url = release.get("url", "")
    if urlsplit(url).hostname not in HOSTS:
        raise UpdateError("The update is not hosted on GitHub, so it was not downloaded.")
    if int(release.get("size") or 0) > MAX_BYTES:
        raise UpdateError("The update file is unexpectedly large.")
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    final = dest_folder / release["name"]
    partial = final.with_name(final.name + ".part")
    try:
        with downloads.open_secure(downloads.request(url), timeout=60) as response:
            if urlsplit(response.geturl()).hostname not in HOSTS:
                raise UpdateError("GitHub sent the download to another website, so it was stopped.")
            total = int(response.headers.get("Content-Length") or release.get("size") or 0)
            got = 0
            digest = hashlib.sha256()
            with partial.open("wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateError("Download cancelled.")
                    chunk = response.read(1 << 18)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise UpdateError("The update file is unexpectedly large.")
                    out.write(chunk)
                    digest.update(chunk)
                    if progress and total:
                        progress(min(1.0, got / total))
        if total and got != total:
            raise UpdateError("The download was cut short. Try again.")
        expected = str(release.get("sha256") or "")
        if expected and digest.hexdigest() != expected:
            raise UpdateError("The downloaded file does not match the one GitHub published, so it was not installed. Try again.")
        os.replace(partial, final)
        return final
    finally:
        partial.unlink(missing_ok=True)
