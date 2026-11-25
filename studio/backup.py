"""Portable Pack: copy the whole software plus downloaded voices to another PC.

The pack is one ZIP:
    Install_On_New_PC.bat      double-click on the new PC
    Install-On-New-PC.ps1      installs Python through WinGet if missing, copies files, makes shortcuts
    Software\\                  the installed program
    Data\\                      settings, saved voices, downloaded voice files, package caches

Python environments are not portable between PCs (they contain absolute paths), so by
default they are left out and rebuilt on the new PC from the packed package cache. The
owner may include them anyway.
"""
from __future__ import annotations
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from .core import root, APP_VERSION

# Folders inside the data folder that are never packed (temporary, rebuilt, or machine specific)
SKIP_DATA = {"logs", "jobs", "tests", "updates", "temp", "previews", "py", "__pycache__"}
OPTIONAL_DATA = {"env": "voice engine environments", "outputs": "finished audio files"}
SKIP_SUFFIX = {".tmp", ".partial", ".lock", ".sqlite3-wal", ".sqlite3-shm"}
STORE_SUFFIX = {".onnx", ".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".whl", ".zip", ".wav", ".mp3", ".gz", ".npz", ".pyd", ".dll", ".exe"}

BAT = r"""@echo off
title AI Voice Studio - install on this PC
echo Starting the installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-On-New-PC.ps1"
echo.
pause
"""

PS1 = r"""$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app  = Join-Path $env:LOCALAPPDATA 'Programs\AI Voice Studio'
$data = Join-Path $env:LOCALAPPDATA 'AVS'

function Find-Python311 {
    try {
        $out = & py -3.11 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return $out }
    } catch { }
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path $candidate) { return $candidate }
    return $null
}

Write-Host ''
Write-Host '=== AI Voice Studio : install on this PC ===' -ForegroundColor Cyan

Write-Host '[1/4] Checking Python 3.11 ...'
if (Find-Python311) {
    Write-Host '      Python 3.11 found.' -ForegroundColor Green
} else {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Host '      Python 3.11 is missing and WinGet is not available on this PC.' -ForegroundColor Yellow
        Write-Host '      Install "App Installer" from the Microsoft Store, or install Python 3.11 (64-bit) from python.org, then run this file again.'
    } else {
        Write-Host '      Python 3.11 not found. Installing it with WinGet (this can take a few minutes) ...'
        & winget install --id Python.Python.3.11 --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
        if (Find-Python311) { Write-Host '      Python installed.' -ForegroundColor Green }
        else { Write-Host '      Python was installed but is not visible yet. Restart Windows if the app asks for Python.' -ForegroundColor Yellow }
    }
}

Write-Host '[2/4] Copying the program ...'
if (Test-Path (Join-Path $here 'Software')) {
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    robocopy (Join-Path $here 'Software') $app /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Copying the program failed.' }
}

Write-Host '[3/4] Copying settings and downloaded voices ...'
$copyData = $true
if ((Test-Path $data) -and (Get-ChildItem $data -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    $answer = Read-Host '      This PC already has AI Voice Studio data. Merge the pack into it? (Y/N)'
    if ($answer -notmatch '^[Yy]') { $copyData = $false }
}
if ($copyData -and (Test-Path (Join-Path $here 'Data'))) {
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    robocopy (Join-Path $here 'Data') $data /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Copying the data failed.' }
}

Write-Host '[4/4] Creating shortcuts ...'
$exe = Join-Path $app 'Voice Studio V0.1.exe'
if (-not (Test-Path $exe)) { $exe = Join-Path $app 'AI_Voice_Studio.exe' }
if (Test-Path $exe) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {
        $link = $shell.CreateShortcut((Join-Path $folder 'Voice Studio V0.1.lnk'))
        $link.TargetPath = $exe
        $link.WorkingDirectory = $app
        $link.Save()
    }
}

Write-Host ''
Write-Host 'Done. Opening AI Voice Studio.' -ForegroundColor Green
Write-Host 'Next: Settings > Check software and dependencies > Fix problems.'
if (Test-Path $exe) { Start-Process $exe }
"""

README = """AI Voice Studio - Portable Pack
===============================
Made: {date}   Version: {version}

On the new PC
1. Unzip this whole folder anywhere (for example the Desktop).
2. Double-click  Install_On_New_PC.bat
3. Wait until it says Done. The app opens by itself.
4. In the app open Settings > Check software and dependencies, then press "Fix problems".
   Everything that is already packed is reused; only small missing parts are fetched.

If Windows says the file is unknown, choose "More info" then "Run anyway" (only for packs you made yourself).
Python 3.11 is installed automatically through WinGet when it is missing.
"""


def software_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _skip_file(path: Path) -> bool:
    return path.suffix.lower() in SKIP_SUFFIX or path.name.endswith(("-wal", "-shm")) or "__pycache__" in path.parts


def _walk(base: Path, skip_top: set[str] = frozenset()):
    for current, dirs, files in os.walk(base):
        rel = Path(current).relative_to(base)
        if rel == Path("."):
            dirs[:] = [d for d in dirs if d not in skip_top]
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = Path(current) / name
            if not _skip_file(path) and path.is_file():
                yield path, path.relative_to(base)


def data_skip(include_env: bool, include_outputs: bool) -> set[str]:
    skip = set(SKIP_DATA)
    if not include_env:
        skip.add("env")
    if not include_outputs:
        skip.add("outputs")
    return skip


def plan(include_env: bool = False, include_outputs: bool = False, software: Path | None = None, data: Path | None = None) -> dict:
    """Quick size estimate: {'software': (bytes, files), 'data': (bytes, files)}."""
    result = {}
    for label, base, skip in (("software", software or software_dir(), set()),
                              ("data", data or root(), data_skip(include_env, include_outputs))):
        total = count = 0
        for path, _ in _walk(base, skip):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                pass
        result[label] = (total, count)
    return result


def snapshot_database(source: Path, target: Path) -> bool:
    """Consistent copy of the live settings database (safe while the app is running)."""
    if not source.is_file():
        return False
    src = sqlite3.connect(str(source), timeout=15)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return True


class Cancelled(Exception):
    pass


def create_pack(dest_folder: Path, include_env: bool = False, include_outputs: bool = False, progress=None,
                cancel: threading.Event | None = None, software: Path | None = None, data: Path | None = None) -> Path:
    """Write the Portable Pack ZIP and return its path. Raises Cancelled if cancelled."""
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    software = software or software_dir()
    data = data or root()
    skip = data_skip(include_env, include_outputs)
    sizes = plan(include_env, include_outputs, software, data)
    total = sum(v[0] for v in sizes.values()) or 1
    free = shutil.disk_usage(dest_folder).free
    if free < total * 1.02 + 200 * 1024 ** 2:
        raise RuntimeError(f"Not enough free space in the chosen folder. Needed about {total / 1e9:.1f} GB, free {free / 1e9:.1f} GB.")
    name = f"AVS_Portable_Pack_{APP_VERSION}_{datetime.now():%Y%m%d_%H%M}.zip"
    final = dest_folder / name
    partial = dest_folder / (name + ".partial")
    done = 0
    snapshot = Path(tempfile.mkdtemp(prefix="avs-snapshot-"))
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True) as z:
            z.writestr("Install_On_New_PC.bat", BAT.replace("\n", "\r\n"))
            z.writestr("Install-On-New-PC.ps1", "\ufeff" + PS1.replace("\n", "\r\n"))
            z.writestr("README.txt", README.format(date=f"{datetime.now():%Y-%m-%d %H:%M}", version=APP_VERSION).replace("\n", "\r\n"))

            def add(path: Path, arc: str):
                nonlocal done
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                method = zipfile.ZIP_STORED if path.suffix.lower() in STORE_SUFFIX else zipfile.ZIP_DEFLATED
                try:
                    z.write(path, arc, compress_type=method)
                    done += path.stat().st_size
                except OSError:
                    return  # file in use or vanished: skip it, the pack stays valid
                if progress:
                    progress(min(1.0, done / total), arc)

            for path, rel in _walk(software):
                add(path, "Software/" + rel.as_posix())
            db = data / "state" / "studio.sqlite3"
            snap = snapshot / "studio.sqlite3"
            has_db = snapshot_database(db, snap)
            for path, rel in _walk(data, skip):
                if has_db and rel.as_posix() == "state/studio.sqlite3":
                    continue
                add(path, "Data/" + rel.as_posix())
            if has_db:
                add(snap, "Data/state/studio.sqlite3")
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        with zipfile.ZipFile(partial) as check:
            bad = check.testzip()
            if bad:
                raise RuntimeError("The pack failed its self-check at " + bad)
        os.replace(partial, final)
        return final
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


# ============================================================ backups shown in Settings > Backup
BACKUP_MANIFEST = "AVS_BACKUP.json"
ALWAYS_SKIP = {"logs", "jobs", "tests", "updates", "temp", "previews", "py", "backups", "app_overlay", "outputs", "__pycache__"}
MODEL_DIRS = {"models", "wheels", "hf_cache", "torch_cache", "pip_cache", "native"}
ENGINE_DIRS = {"env"}                 # Python environments: only useful on the same PC
PRIVATE_DIRS = {"clones", "training"}  # a person's own voice samples and projects: never given to others
AUTO_DIRS = {"state", "config", "clones"}
LABELS = {"auto": "auto", "owner": "owner", "others": "for others", "complete": "with setup"}
PRIVATE_FILES = {"config/update_signing.key", "config/owner_proof.json"}   # never given to other people
SETUP_BAT = r"""@echo off
title AI Voice Studio by Unzip Spark - full install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Everything.ps1"
echo.
pause
"""
SETUP_PS1 = r"""$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path    # wherever this folder is, on any drive
$data = Join-Path $env:LOCALAPPDATA 'AVS'
Write-Host ''
Write-Host '=== AI Voice Studio by Unzip Spark : full install ===' -ForegroundColor Cyan

Write-Host '[1/4] Checking the setup file ...'
$setup = Get-ChildItem -LiteralPath (Join-Path $here 'Setup') -Filter '*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $setup) { throw 'The Setup folder has no setup file.' }
$expected = (Get-Content -LiteralPath (Join-Path $here 'Setup\SHA256.txt') -Raw).Trim().Split(' ')[0].ToLower()
$actual = (Get-FileHash -LiteralPath $setup.FullName -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) { throw 'The setup file does not match its checksum. It may be damaged or changed. Do not install it.' }
Write-Host '      The setup file is intact.' -ForegroundColor Green

Write-Host '[2/4] Installing the program ...'
$run = Start-Process -FilePath $setup.FullName -ArgumentList '/SILENT','/NORESTART' -Wait -PassThru
if ($run.ExitCode -ne 0) { throw "The installer stopped with code $($run.ExitCode)." }

Write-Host '[3/4] Copying settings and downloaded AI models from this folder ...'
$copy = $true
if ((Test-Path $data) -and (Get-ChildItem $data -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    $answer = Read-Host '      This PC already has AI Voice Studio data. Merge this folder into it? (Y/N)'
    if ($answer -notmatch '^[Yy]') { $copy = $false }
}
if ($copy -and (Test-Path (Join-Path $here 'Data'))) {
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    robocopy (Join-Path $here 'Data') $data /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw 'Copying the data failed.' }
}

Write-Host '[4/4] Opening the program ...'
$app = Join-Path $env:LOCALAPPDATA 'Programs\AI Voice Studio'
$exe = Join-Path $app 'Voice Studio V0.1.exe'
if (-not (Test-Path $exe)) { $exe = Join-Path $app 'AI_Voice_Studio.exe' }
Write-Host 'Done. Your AI models are found automatically. If a voice engine asks for repair, open Settings > Tools > Check everything.' -ForegroundColor Green
if (Test-Path $exe) { Start-Process $exe }
"""
SETUP_README = """AI Voice Studio by Unzip Spark - full install
=============================================
Made: {date}   Version: {version}

On the new PC
1. Unzip this whole folder anywhere (a drive, a USB stick, the Desktop).
2. Double-click Install_Everything.bat
3. Wait until it says Done. The program opens by itself.

The setup file is checked against its checksum first, then installed. Your settings and downloaded AI models are copied
from the Data folder next to it, so they are found automatically.
"""


def backups_dir() -> Path:
    folder = root() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def extra_sizes(data: Path | None = None) -> dict:
    """Bytes the 'also include downloaded AI models' checkbox would add: {'models': n, 'engines': n}."""
    data = Path(data or root())
    out = {}
    for label, names in (("models", MODEL_DIRS), ("engines", ENGINE_DIRS)):
        total = 0
        for name in names:
            folder = data / name
            if folder.is_dir():
                for path, _ in _walk(folder):
                    try:
                        total += path.stat().st_size
                    except OSError:
                        pass
        out[label] = total
    return out


def sanitize_database(db: Path) -> None:
    """Remove a person's script history and saved voices from a COPY of the settings database (used for 'for others')."""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM sections")
        conn.execute("DELETE FROM jobs")
        for key in ("clone_profiles", "favorite_voices", "sources_last_auto", "update_last_check", "update_available"):
            conn.execute("DELETE FROM kv WHERE key=?", (key,))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _skip_for(kind: str, include_models: bool) -> set:
    skip = set(ALWAYS_SKIP)
    if kind in ("others", "complete"):
        skip.discard("app_overlay")        # the installed update goes along, so the other PC starts with the same version
    if kind == "auto":
        return skip | {p for p in ("models", "wheels", "hf_cache", "torch_cache", "pip_cache", "native", "env", "training")}
    if not include_models:
        skip |= MODEL_DIRS | ENGINE_DIRS
    if kind in ("others", "complete"):
        skip |= PRIVATE_DIRS | ENGINE_DIRS
    return skip


def _wanted(rel: Path, model_keys) -> bool:
    """With a chosen list of models only those models (and the shared wheel cache) go into the backup."""
    if model_keys is None or not rel.parts:
        return True
    top = rel.parts[0]
    if top == "models":
        return len(rel.parts) > 1 and rel.parts[1] in model_keys
    if top in MODEL_DIRS and top != "wheels":
        return False
    return True


def plan_backup(kind: str, include_models: bool = False, software: Path | None = None, data: Path | None = None,
                installer: Path | None = None, model_keys=None) -> int:
    data = Path(data or root())
    total = sum(p.stat().st_size for p, rel in _walk(data, _skip_for(kind, include_models)) if p.exists() and _wanted(rel, model_keys))
    if kind == "others":
        total += sum(p.stat().st_size for p, _ in _walk(software or software_dir()) if p.exists())
    if kind == "complete" and installer and Path(installer).is_file():
        total += Path(installer).stat().st_size
    return total


def find_installers(extra: list | None = None, limit: int = 8) -> list[Path]:
    """Setup files made by the build, found in the usual places (newest first)."""
    names = ("AI_Voice_Studio_Setup_*.exe", "AI Voice Studio by Unzip Spark Setup*.exe")
    places = [Path.home() / x for x in ("Desktop", "Downloads", "Documents")] + [Path(p) for p in (extra or [])]
    found = {}
    for place in places:
        if not place.is_dir():
            continue
        for pattern in names:
            for depth in range(4):
                for hit in place.glob("/".join(["*"] * depth + [pattern])):
                    if hit.is_file():
                        found[hit] = hit.stat().st_mtime
    return [p for p, _ in sorted(found.items(), key=lambda kv: kv[1], reverse=True)][:limit]


def file_sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(kind: str = "owner", include_models: bool = False, progress=None, cancel: threading.Event | None = None,
                  software: Path | None = None, data: Path | None = None, dest: Path | None = None, note: str = "",
                  installer: Path | None = None, permit: dict | None = None, model_keys=None) -> Path:
    """kind: 'owner' (everything of yours), 'others' (software + settings, without your private voices and history),
    'complete' (the setup file + settings + optional models, installs itself) or 'auto'.
    permit: a signed backup permit from the owner key; it lets the receiving copy make its own backups."""
    if kind not in LABELS:
        raise ValueError("Unknown backup kind.")
    if kind == "complete" and not (installer and Path(installer).is_file()):
        raise ValueError("Choose the setup file first (the .exe made by the build).")
    data = Path(data or root())
    dest = Path(dest or backups_dir())
    dest.mkdir(parents=True, exist_ok=True)
    if model_keys is not None:
        model_keys = set(model_keys)
        include_models = bool(model_keys)
    skip = _skip_for(kind, include_models)
    total = max(1, plan_backup(kind, include_models, software, data, installer, model_keys))
    free = shutil.disk_usage(dest).free
    if free < total * 1.02 + 100 * 1024 ** 2:
        raise RuntimeError(f"Not enough free space for this backup. It needs about {total / 1e9:.1f} GB and {free / 1e9:.1f} GB is free.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final = dest / f"AVS_{kind}_{APP_VERSION}_{stamp}.zip"
    partial = final.with_name(final.name + ".partial")
    snapshot = Path(tempfile.mkdtemp(prefix="avs-backup-"))
    done = 0
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True) as z:
            manifest = {"kind": kind, "version": APP_VERSION, "created": datetime.now().isoformat(timespec="seconds"),
                        "models": bool(include_models and kind != "auto"), "note": note}
            try:
                import avs_boot
                layer = avs_boot.read_manifest(avs_boot.overlay_dir())
                if layer and kind in ("others", "complete"):
                    manifest["update_layer"] = str(layer.get("version", ""))      # the update that is inside this backup
            except Exception:
                pass
            if kind == "complete":
                manifest["installer"] = Path(installer).name
            z.writestr(BACKUP_MANIFEST, json.dumps(manifest, indent=1))

            def add(path: Path, arc: str):
                nonlocal done
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                method = zipfile.ZIP_STORED if path.suffix.lower() in STORE_SUFFIX else zipfile.ZIP_DEFLATED
                try:
                    z.write(path, arc, compress_type=method)
                    done += path.stat().st_size
                except OSError:
                    return
                if progress:
                    progress(min(1.0, done / total), arc)

            if kind == "others":
                z.writestr("Install_On_New_PC.bat", BAT.replace("\n", "\r\n"))
                z.writestr("Install-On-New-PC.ps1", "\ufeff" + PS1.replace("\n", "\r\n"))
                z.writestr("README.txt", README.format(date=f"{datetime.now():%Y-%m-%d %H:%M}", version=APP_VERSION).replace("\n", "\r\n"))
                for path, rel in _walk(software or software_dir()):
                    add(path, "Software/" + rel.as_posix())
            db = data / "state" / "studio.sqlite3"
            snap = snapshot / "studio.sqlite3"
            has_db = snapshot_database(db, snap)
            if has_db and kind in ("others", "complete"):
                sanitize_database(snap)
            if kind == "complete":
                digest = file_sha256(installer)
                z.writestr("Install_Everything.bat", SETUP_BAT.replace("\n", "\r\n"))
                z.writestr("Install-Everything.ps1", "\ufeff" + SETUP_PS1.replace("\n", "\r\n"))
                z.writestr("README.txt", SETUP_README.format(date=f"{datetime.now():%Y-%m-%d %H:%M}", version=APP_VERSION).replace("\n", "\r\n"))
                z.writestr("Setup/SHA256.txt", f"{digest}  {Path(installer).name}\r\n")
                add(Path(installer), "Setup/" + Path(installer).name)
            if model_keys:
                z.writestr("Data/state/restored_models.json", json.dumps({"models": sorted(model_keys), "from_version": APP_VERSION}))   # the other PC prepares these engines by itself
            for path, rel in _walk(data, skip):
                if not _wanted(rel, model_keys):
                    continue
                arc = rel.as_posix()
                if arc == "state/studio.sqlite3" or arc.endswith("studio.before-restore.sqlite3"):
                    continue
                if kind == "auto" and rel.parts[0] not in AUTO_DIRS:
                    continue
                if kind in ("others", "complete") and arc in PRIVATE_FILES:
                    continue
                if arc == "config/backup_permit.json" and kind in ("others", "complete"):
                    continue          # a permit is added below only when the owner chose to give one
                add(path, "Data/" + arc)
            if permit and kind in ("others", "complete"):
                z.writestr("Data/config/backup_permit.json", json.dumps(permit, indent=1))
            if has_db:
                add(snap, "Data/state/studio.sqlite3")
        with zipfile.ZipFile(partial) as check:
            bad = check.testzip()
            if bad:
                raise RuntimeError("The backup failed its self-check at " + bad)
        os.replace(partial, final)
        try:                                   # every backup is noted in the main folder
            log_path = dest / "backup_records.json"
            records = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else []
            records.append({"file": final.name, "kind": kind, "version": APP_VERSION, "created": datetime.now().isoformat(timespec="seconds"),
                            "models": sorted(model_keys) if model_keys else [], "size_mb": round(final.stat().st_size / 1e6, 1)})
            log_path.write_text(json.dumps(records[-500:], indent=1), encoding="utf-8")
        except Exception:
            pass
        return final
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def list_backups(folder: Path | None = None) -> list[dict]:
    """Backups in the backups folder, newest first: path, kind label, created, size, version."""
    rows = []
    for f in Path(folder or backups_dir()).glob("AVS_*.zip"):
        try:
            with zipfile.ZipFile(f) as z:
                info = json.loads(z.read(BACKUP_MANIFEST).decode("utf-8")) if BACKUP_MANIFEST in z.namelist() else {}
        except (OSError, zipfile.BadZipFile, ValueError):
            continue
        kind = info.get("kind", "owner")
        rows.append({"path": f, "kind": kind, "label": LABELS.get(kind, kind), "created": info.get("created") or datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                     "size": f.stat().st_size, "version": info.get("version", ""), "models": bool(info.get("models"))})
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def _signature(data: Path) -> str:
    parts = []
    for rel in ("state/studio.sqlite3", "config/access.json"):
        f = data / rel
        parts.append(f"{f.stat().st_mtime_ns}:{f.stat().st_size}" if f.is_file() else "-")
    clones = data / "clones"
    parts.append(str(sum(1 for _ in clones.glob("*.wav")) if clones.is_dir() else 0))
    return "|".join(parts)


def auto_snapshot(store=None, data: Path | None = None, keep: int = 20, dest: Path | None = None):
    """A small automatic backup (settings, voices, owner rules) when something changed since the last one."""
    data = Path(data or root())
    sig = _signature(data)
    marker = data / "state" / "last-auto-backup.txt"
    try:
        if marker.is_file() and marker.read_text(encoding="utf-8") == sig:
            return None
    except OSError:
        pass
    path = create_backup("auto", data=data, dest=dest)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(sig, encoding="utf-8")
    except OSError:
        pass
    autos = [r for r in list_backups(dest) if r["kind"] == "auto"]
    for old in autos[keep:]:
        old["path"].unlink(missing_ok=True)
    return path


def stage_restore(archive: Path) -> None:
    """Prepare a restore. It happens the next time the program starts (the database cannot be replaced while it is open)."""
    import avs_boot
    archive = Path(archive)
    with zipfile.ZipFile(archive) as z:
        if not any(n.startswith("Data/") for n in z.namelist()):
            raise ValueError("This ZIP is not an AI Voice Studio backup (no Data folder inside).")
    avs_boot.pending_path().write_text(json.dumps({"action": "restore", "zip": str(archive)}), encoding="utf-8")
