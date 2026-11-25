"""Offline, transactional folder relocation. No AI runtimes are imported here.

All public paths stay at their old locations (Windows NTFS junctions). The
separately packaged helper runs outside both trees. A journal permits rollback
if interrupted before commit. Only the verified destination is used afterward.
"""
from __future__ import annotations
import ctypes
import hashlib
import json
import os
import shutil
import stat
import struct
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import robocopy_engine

RESERVE = 256 * 1024**2
REPARSE = 0x400
LINK_TAGS = {0xA0000003, 0xA000000C}
CHUNK = 8 * 1024**2


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.writing')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def exists(path):
    return os.path.lexists(path)


def is_link(path):
    st = Path(path).lstat()
    if stat.S_ISLNK(st.st_mode):
        return True
    if getattr(st, 'st_file_attributes', 0) & REPARSE:
        if getattr(st, 'st_reparse_tag', 0) not in LINK_TAGS:
            raise RuntimeError(f'Unsupported Windows reparse point: {path}. Make this file available locally first.')
        return True
    return False


def inside(path, root):
    return Path(path).is_relative_to(Path(root))


def overlap(a, b):
    return inside(a, b) or inside(b, a)


def next_destination(parent):
    """Never overwrite a previous move or a folder already owned by the user."""
    parent = Path(parent).resolve(strict=True)
    for n in range(1, 10000):
        name = 'AI Voice Studio' if n == 1 else f'AI Voice Studio ({n})'
        p = parent / name
        if not exists(p):
            return p
    raise RuntimeError('Too many existing destination folders. Select another location.')


def validate_volume(path):
    if os.name != 'nt':
        return
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    volume = ctypes.create_unicode_buffer(32768)
    k.GetVolumePathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
    k.GetDriveTypeW.argtypes = [w.LPCWSTR]
    k.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, w.LPWSTR, w.DWORD]
    if not k.GetVolumePathNameW(str(Path(path).resolve()), volume, len(volume)):
        raise ctypes.WinError(ctypes.get_last_error())
    fs = ctypes.create_unicode_buffer(256)
    if not k.GetVolumeInformationW(volume.value, None, 0, None, None, None, fs, len(fs)):
        raise ctypes.WinError(ctypes.get_last_error())
    if k.GetDriveTypeW(volume.value) != 3 or fs.value.upper() != 'NTFS':
        raise RuntimeError('Choose a local fixed NTFS drive (for example D:). USB, network and non-NTFS destinations are not supported.')


def make_junction(link, target):
    """Create an NTFS mount-point reparse buffer without shell quoting or UAC."""
    link, target = Path(link), Path(target).resolve(strict=True)
    if os.name != 'nt':  # Portable transaction tests only.
        link.symlink_to(target, target_is_directory=True)
        return
    from ctypes import wintypes as w
    if str(target).startswith('\\\\'):
        raise RuntimeError('Network junction targets are not supported.')
    sub = ('\\??\\' + str(target)).encode('utf-16-le')
    display = str(target).encode('utf-16-le')
    pathbuf = sub + b'\0\0' + display + b'\0\0'
    data = struct.pack('<IHHHHHH', 0xA0000003, 8 + len(pathbuf), 0, 0, len(sub), len(sub)+2, len(display)) + pathbuf
    if len(data) > 16384:
        raise RuntimeError('Destination path is too long for a Windows junction.')
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    k.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    k.CloseHandle.argtypes = [w.HANDLE]
    link.mkdir()
    try:
        h = k.CreateFileW(str(link), 0x40000000, 7, None, 3, 0x02200000, None)
        if h == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buf = ctypes.create_string_buffer(data)
            returned = w.DWORD()
            if not k.DeviceIoControl(h, 0x000900A4, buf, len(data), None, 0, ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k.CloseHandle(h)
    except BaseException:
        link.rmdir()
        raise


def remove_link(link):
    if not is_link(link):
        raise RuntimeError(f'Expected a link, not a real folder: {link}')
    if os.name == 'nt' and getattr(Path(link).lstat(), 'st_reparse_tag', 0) == 0xA0000003:
        Path(link).rmdir()
    else:
        Path(link).unlink()


def safe_remove_tree(path):
    """Never follow directory junctions or symlinks while cleaning old trees."""
    path = Path(path)
    if not exists(path):
        return
    if is_link(path):
        remove_link(path)
        return
    if path.is_dir():
        for p in path.iterdir():
            safe_remove_tree(p)
        path.rmdir()
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
            path.unlink()


def make_plan(app, data, destination, token):
    """Record logical + physical paths before the GUI closes."""
    destination = Path(destination)
    if exists(destination):
        raise RuntimeError('Destination already exists. Choose another folder.')
    rows = []
    for label, logical in [('Program', app), ('Data', data)]:
        logical = Path(logical).absolute()
        physical = logical.resolve(strict=True)
        if not physical.is_dir():
            raise RuntimeError(f'Missing source folder: {logical}')
        linked = is_link(logical)
        backup = physical if linked else logical.with_name(logical.name + '.avs-backup-' + token)
        if not linked and exists(backup):
            raise RuntimeError(f'Backup already exists: {backup}')
        rows.append(dict(label=label, logical=str(logical), physical=str(physical), linked=linked, backup=str(backup), dst=str(destination/label)))
    all_roots = [Path(r[k]) for r in rows for k in ('logical', 'physical')]
    if any(overlap(Path(rows[0][k]), Path(rows[1][l])) for k in ('logical', 'physical') for l in ('logical', 'physical')):
        raise RuntimeError('Program and data folders overlap. Move cancelled to avoid data loss.')
    if any(overlap(destination, p) for p in all_roots):
        raise RuntimeError('Choose a destination outside the current program and data folders.')
    for p in all_roots:
        validate_volume(p.parent)
    validate_volume(destination.parent)
    return dict(rows=rows, destination=str(destination), staging=str(destination.with_name(destination.name + '.moving-' + token)), phase='planned')


def inventory(root, allowed, progress=lambda *_:None):
    """Expand safe local links into ordinary files/dirs without following cycles.

    Hugging Face snapshots commonly contain links to local blobs. Copying their
    content needs no symbolic-link privilege and survives a drive move. External
    links and non-link reparse points fail safely instead of copying unrelated data.
    """
    dirs, files = [], []
    root = Path(root)
    count = 0
    def walk(p, rel, ancestry):
        nonlocal count
        if is_link(p):
            actual = p.resolve(strict=True)
            if not any(inside(actual, a) for a in allowed):
                raise RuntimeError(f'Link points outside managed folders: {p} -> {actual}. Original files remain unchanged.')
        else:
            actual = p
        st = actual.stat()
        if stat.S_ISDIR(st.st_mode):
            key = actual.resolve(strict=True)
            if key in ancestry:
                raise RuntimeError(f'Circular folder link: {p}')
            dirs.append(rel)
            for child in actual.iterdir():
                walk(child, rel/child.name, ancestry | {key})
        elif stat.S_ISREG(st.st_mode):
            files.append((rel, actual, st.st_size, st.st_mtime_ns))
        else:
            raise RuntimeError(f'Unsupported file type: {p}')
        count += 1
        if count % 300 == 0:
            progress('Checking files', 0, 0, str(rel))
    walk(root, Path('.'), set())
    return dirs, files


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for buf in iter(lambda:f.read(CHUNK), b''):
            h.update(buf)
    return h.digest()


def copy_verified(src, dst, size, mtime, advance, partial=None):
    """A finalized staging file is a checkpoint; an unfinished file can resume.
    On recovery rehash retained bytes, rather than trusting timestamps alone.
    """
    src, dst = Path(src), Path(dst)
    partial = Path(partial) if partial is not None else dst.with_name(dst.name+'.avs-part')
    def source_unchanged():
        st=src.stat()
        if st.st_size!=size or st.st_mtime_ns!=mtime:
            raise RuntimeError(f'Source changed during copy: {src}')
    source_unchanged()
    if exists(dst):
        if is_link(dst) or not dst.is_file():
            raise RuntimeError(f'Unexpected staged file: {dst}')
        if dst.stat().st_size==size and file_digest(src)==file_digest(dst):
            source_unchanged();advance(2*size);return
        try:dst.unlink()
        except PermissionError:
            dst.chmod(stat.S_IWRITE | stat.S_IREAD);dst.unlink()
    if exists(partial) and (is_link(partial) or not partial.is_file()):
        raise RuntimeError(f'Unexpected partial file: {partial}')
    offset=partial.stat().st_size if partial.exists() else 0
    digest=hashlib.sha256()
    if offset>size:
        partial.unlink();offset=0
    if offset:
        with src.open('rb') as reader,partial.open('rb') as old:
            remaining=offset
            while remaining:
                buf=old.read(min(CHUNK,remaining))
                if not buf or reader.read(len(buf))!=buf:
                    offset=0;break
                digest.update(buf);remaining-=len(buf)
        if not offset:
            partial.unlink();digest=hashlib.sha256()
    count=offset
    advance(offset)
    with src.open('rb') as reader, partial.open('ab' if offset else 'wb') as writer:
        reader.seek(offset)
        for chunk in iter(lambda:reader.read(CHUNK), b''):
            writer.write(chunk);digest.update(chunk);count+=len(chunk);advance(len(chunk))
        writer.flush();os.fsync(writer.fileno())
    source_unchanged()
    if count!=size:
        raise RuntimeError(f'Source size changed: {src}')
    check=hashlib.sha256()
    with partial.open('rb') as f:
        for chunk in iter(lambda:f.read(CHUNK),b''):
            check.update(chunk);advance(len(chunk))
    if partial.stat().st_size!=size or check.digest()!=digest.digest():
        raise RuntimeError(f'File verification failed: {src}')
    shutil.copystat(src,partial)
    os.replace(partial,dst)


def write_marker(stage,plan):
    marker=stage/'.avs-move-owner'
    with marker.open('w',encoding='utf-8') as f:
        f.write(plan['staging']);f.flush();os.fsync(f.fileno())


def owned_copy(path,plan):
    path=Path(path); marker=path/'.avs-move-owner'
    return (path.is_dir() and not is_link(path) and marker.is_file() and
            not is_link(marker) and marker.read_text(encoding='utf-8')==plan['staging'])


def prepare_resume(plan,journal):
    """Undo an interrupted switch without discarding the staged snapshot."""
    phase=plan['phase'];stage=Path(plan['staging']);dest=Path(plan['destination'])
    if phase not in ('publishing','switching'):return
    if phase=='switching':
        for row in reversed(plan['rows']):restore(row)
    # Restore first, then move the owned copy back to its staging name.
    # If power fails here the same operation is safe to repeat.
    if exists(dest):
        if not owned_copy(dest,plan) or exists(stage):
            raise RuntimeError('Cannot safely recover destination; original data retained.')
        dest.rename(stage)
    if not owned_copy(stage,plan):
        raise RuntimeError('Staged copy unavailable; original data retained.')
    plan['phase']='copying';atomic_json(journal,plan)


def link_matches(path, target):
    return exists(path) and is_link(path) and Path(path).resolve(strict=True) == Path(target).resolve(strict=True)


def switch(row):
    logical, old, dst, backup = (Path(row[k]) for k in ('logical','physical','dst','backup'))
    if row['linked']:
        if not link_matches(logical, old):
            raise RuntimeError(f'Original link changed: {logical}')
        remove_link(logical)
    else:
        if exists(backup) or is_link(logical) or logical.resolve(strict=True) != old:
            raise RuntimeError(f'Original folder changed: {logical}')
        logical.rename(backup)
    make_junction(logical, dst)
    if not link_matches(logical, dst):
        raise RuntimeError(f'New link verification failed: {logical}')


def link_points_to(path,target):
    # A destination volume may be offline; compare the stored junction target
    # without following it. Final commit still requires strict link_matches.
    if not exists(path) or not is_link(path):return False
    raw=os.readlink(path)
    if os.name=='nt' and raw.startswith('\\\\?\\'):
        raw=raw[4:]
    p=Path(raw)
    if not p.is_absolute():p=Path(path).parent/p
    return os.path.normcase(os.path.abspath(p))==os.path.normcase(os.path.abspath(target))


def restore(row):
    """Idempotent rollback, including a crash between rename and junction creation."""
    logical, old, dst, backup = (Path(row[k]) for k in ('logical','physical','dst','backup'))
    if exists(logical) and is_link(logical):
        if row['linked'] and link_points_to(logical, old):
            if not old.is_dir():raise RuntimeError(f'Original location is unavailable: {old}')
            return
        if not link_points_to(logical, dst):
            raise RuntimeError(f'Unexpected link at {logical}; original backup: {backup}')
        remove_link(logical)
    # An abrupt power loss between creating a junction directory and setting
    # its reparse point can leave an empty directory. Removing only an empty
    # placeholder is safe; never remove a folder containing unexpected files.
    if exists(logical) and (row['linked'] or exists(backup)):
        if logical.is_dir() and not is_link(logical) and not any(logical.iterdir()):
            logical.rmdir()
    if row['linked']:
        if exists(logical):
            raise RuntimeError(f'Unexpected folder at {logical}; original files: {old}')
        make_junction(logical, old)
        if not link_matches(logical, old):
            raise RuntimeError(f'Could not restore original link: {logical}')
    elif exists(backup):
        if exists(logical):
            raise RuntimeError(f'Unexpected folder at {logical}; original backup: {backup}')
        backup.rename(logical)
    elif not logical.is_dir():
        raise RuntimeError(f'Original folder not found: {logical}; backup: {backup}')


def rollback(plan, log):
    # During pre-copy phases no source paths could have been switched.
    if plan['phase'] == 'switching':
        for row in reversed(plan['rows']):
            restore(row)
    # Never discard the staged copy when the original roots are unavailable.
    for row in plan['rows']:
        original=Path(row['logical']);physical=Path(row['physical'])
        if not original.is_dir() or original.resolve(strict=True)!=physical.resolve(strict=True):
            raise RuntimeError(f'Original location unavailable: {original}. Staged data retained.')
    for key in ('staging', 'destination'):
        # Only remove the final destination if the journal says we owned it.
        if key == 'destination' and plan['phase'] not in ('switching','publishing'):
            continue
        p = Path(plan[key])
        # A unique ownership marker is written before publishing the copy.
        # The marker is checked even when a concurrent folder creation occurred.
        try:
            marker = p/'.avs-move-owner'
            if p.is_dir() and not is_link(p) and marker.is_file() and marker.read_text(encoding='utf-8') == plan['staging']:
                safe_remove_tree(p)
        except (OSError,RuntimeError) as exc:
            log(f'Could not remove unused copy {p}: {exc}')


def cleanup(plan, log):
    """Only after durable commit; never remove a path that is now active."""
    if not all(link_matches(Path(r['logical']),Path(r['dst'])) for r in plan['rows']):
        raise RuntimeError('At least one committed junction no longer points to the verified copy. Old backups retained.')
    warnings = []
    roots = [Path(r['dst']).resolve(strict=True) for r in plan['rows']]
    for row in plan['rows']:
        backup = Path(row['backup'])
        if not exists(backup):
            continue
        try:
            if is_link(backup) or any(overlap(backup.resolve(strict=True), p) for p in roots):
                raise RuntimeError(f'Unsafe cleanup path: {backup}')
            safe_remove_tree(backup)
        except Exception as exc:
            warnings.append(f'Original backup partly or fully retained: {backup}: {exc}')
            log(warnings[-1])
    return warnings


def relocate(plan, journal, smoke, progress, log, privacy=lambda _:None, before_switch=lambda:None):
    """Execute the transaction. The worker handles rollback on exceptions."""
    def save(phase):
        plan['phase'] = phase
        atomic_json(journal, plan)
    stage, dest = Path(plan['staging']), Path(plan['destination'])
    if plan['phase'] not in ('planned','copying') or exists(dest):
        raise RuntimeError('Move is not ready for copying or destination already exists.')
    allowed = [Path(r['physical']).resolve(strict=True) for r in plan['rows']]
    inventories=[];total=0
    for row in plan['rows']:
        d,f=inventory(row['physical'],allowed,progress)
        inventories.append((d,f));total+=sum(x[2] for x in f)
    # The journal is persisted before any staged files are copied.
    if exists(stage):
        if not owned_copy(stage,plan):
            # A power loss can occur between creating the directory and marker.
            if is_link(stage) or not stage.is_dir():
                raise RuntimeError('Unrecognized staging folder. Original files unchanged.')
            entries=list(stage.iterdir());marker=stage/'.avs-move-owner'
            # Only an empty directory or a torn initial marker can be repaired.
            if entries and not (entries==[marker] and marker.is_file() and not is_link(marker)
                                and plan['staging'].startswith(marker.read_text(encoding='utf-8'))):
                raise RuntimeError('Unrecognized staging folder. Original files unchanged.')
            write_marker(stage,plan)
    else:
        save('copying');stage.mkdir()
        write_marker(stage,plan)
    privacy(stage)
    parts=stage/'.avs-parts';parts.mkdir(exist_ok=True)
    if is_link(parts):raise RuntimeError('Unexpected staging checkpoint link.')
    def part_for(label,rel):
        return parts/(hashlib.sha256((label+'/'+rel.as_posix()).encode()).hexdigest()+'.part')
    # Only existing checkpoint bytes for expected files count toward the estimate.
    retained=0;tasks=[]
    for row,(dirs,files) in zip(plan['rows'],inventories):
        base=stage/row['label']
        for rel in dirs:
            d=base/rel;d.mkdir(parents=True,exist_ok=True)
            if is_link(d):raise RuntimeError(f'Unexpected staging link: {d}')
        for rel,actual,size,mtime in files:
            dst=base/rel;part=part_for(row['label'],rel)
            for candidate in (dst,part):
                if exists(candidate) and (is_link(candidate) or not candidate.is_file()):raise RuntimeError(f'Unexpected checkpoint: {candidate}')
            retained+=min(size,max(dst.stat().st_size if dst.exists() else 0,part.stat().st_size if part.exists() else 0))
            tasks.append((row['label'],rel,actual,dst,size,mtime,part))
    free=shutil.disk_usage(dest.parent).free
    if free < total-retained+RESERVE:
        raise RuntimeError(f'Insufficient destination storage. Need {(total-retained+RESERVE)/1e9:.2f} GB more, available {free/1e9:.2f} GB. Source files unchanged.')
    save('copying')
    log(f'Copying and verifying {total/1e9:.2f} GB across {len(tasks)} files; up to {retained/1e9:.2f} GB checkpointed.')
    # Robocopy only stages normal trees. Existing Python partials and links are
    # handled by the verified copy engine below; source paths are never moved
    # by Robocopy. The same post-copy verification, rollback and commit apply.
    for row,(dirs,files) in zip(plan['rows'],inventories):
        label=row['label'];base=stage/label
        checkpoints={rel for rel,_,_,_ in files if part_for(label,rel).exists()}
        robocopy_engine.accelerate(Path(row['physical']),base,dirs,files,checkpoints,
                                   journal,progress,log,is_link)
    done=0;last=0.;started=time.monotonic();guard=threading.Lock();stop=threading.Event()
    def copy_one(task):
        label,rel,actual,dst,size,mtime,part=task
        if stop.is_set():return
        def advance(n):
            nonlocal done,last
            if stop.is_set():raise RuntimeError('Copy worker stopped; checkpoints retained.')
            with guard:
                done+=n;now=time.monotonic()
                if now-last>=.20:
                    speed=done/2/max(.1,now-started)/1e6
                    progress('Copying and verifying files',done,total*2,f'{speed:.1f} MB/s effective | {label} / {rel}')
                    last=now
        try:copy_verified(actual,dst,size,mtime,advance,part)
        except BaseException:
            stop.set();raise
    # Four bounded workers help with many tiny files without opening thousands
    # of handles or bypassing link materialization and checksum verification.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0,len(tasks),128):
            list(pool.map(copy_one,tasks[start:start+128]))
    for row,(dirs,files) in zip(plan['rows'],inventories):
        d2,f2=inventory(row['physical'],allowed)
        if set(d2)!=set(dirs) or set(f2)!=set(files):
            raise RuntimeError(f"Source changed while moving {row['label']}. Retry after closing other processes.")
        # Refuse unexpected stale files rather than publish a stale snapshot.
        staged_dirs,staged_files=inventory(stage/row['label'],[stage/row['label']])
        if set(staged_dirs)!=set(dirs) or {x[0] for x in staged_files}!={x[0] for x in files}:
            raise RuntimeError('Staging contents differ from source. Choose original location and start a new move.')
    safe_remove_tree(parts)
    before_switch()
    progress('Switching to new folder',total*2,total*2,'Do not turn off the computer.')
    save('publishing')
    if exists(dest):
        raise RuntimeError('Destination was created by another process; no source files changed.')
    stage.rename(dest)
    save('switching')
    for row in plan['rows']:
        switch(row)
    if not all(link_matches(Path(r['logical']),Path(r['dst'])) for r in plan['rows']):
        raise RuntimeError('Final junction verification failed.')
    progress('Checking installation',total*2,total*2,'Testing application and database')
    smoke()
    save('committed')
    log('Verified program and data at destination; committed.')
    progress('Cleaning up old files',total*2,total*2,'Original data is now safely relocated.')
    warnings = cleanup(plan,log)
    try:
        (dest/'.avs-move-owner').unlink()
    except OSError:
        pass
    save('complete')
    return warnings
