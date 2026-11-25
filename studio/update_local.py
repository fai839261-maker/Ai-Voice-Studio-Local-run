"""Local update ingestion. Only an installer and its optional checksum are accepted.
Never extract or execute arbitrary source archives. An independent trusted checksum
(or a publisher signature) is required to establish authenticity.
"""
from pathlib import Path
import re, zipfile, shutil, tempfile, os
from .core import subdir, sha256, APP_VERSION

PATTERN=re.compile(r'AI_Voice_Studio_Setup_(\d+\.\d+\.\d+)\.exe',re.I)
MAX_INSTALLER=2*1024**3

def version_tuple(text):return tuple(map(int,text.split('.')))

def check_name(name):
    match=PATTERN.fullmatch(name)
    if not match:raise ValueError('Select an AI Voice Studio setup EXE or a compiled update ZIP.')
    if version_tuple(match.group(1))<version_tuple(APP_VERSION):raise ValueError('Older installers cannot be used as updates.')
    return match.group(1)

def valid_checksum(text):
    text=text.strip()
    if not re.fullmatch(r'[0-9a-fA-F]{64}',text):raise ValueError('Expected SHA-256 must contain 64 hexadecimal characters.')
    return text.lower()

def stage_update(source):
    """Return (staged executable, optional declared hash, actual hash).
    Archive member names are never used as filesystem paths.
    """
    path=Path(source);declared='';cache=subdir('updates');stage=Path(tempfile.mkdtemp(prefix='local-',dir=cache))
    try:
        if path.suffix.lower()=='.exe':
            check_name(path.name);target=stage/path.name
            if not 0<path.stat().st_size<=MAX_INSTALLER:raise ValueError('Invalid installer size.')
            shutil.copy2(path,target);side=path.with_name(path.name+'.sha256.txt')
            if side.is_file() and side.stat().st_size<=1024:declared=valid_checksum(side.read_text(encoding='ascii'))
        elif path.suffix.lower()=='.zip':
            with zipfile.ZipFile(path) as z:
                files=[i for i in z.infolist() if not i.is_dir()]
                candidates=[i for i in files if PATTERN.fullmatch(i.filename)]
                if len(candidates)!=1:raise ValueError('ZIP must contain exactly one compiled setup EXE at its root. A source ZIP must be compiled first.')
                exe=candidates[0];check_name(exe.filename);target=stage/exe.filename
                sides=[i for i in files if i.filename==exe.filename+'.sha256.txt']
                if len(sides)>1 or len(files)!=1+len(sides):raise ValueError('Unexpected entries in update ZIP.')
                if not 0<exe.file_size<=MAX_INSTALLER:raise ValueError('Invalid installer size.')
                if shutil.disk_usage(stage).free<exe.file_size:raise ValueError('Insufficient storage for update.')
                with z.open(exe) as inp,target.open('wb') as out:shutil.copyfileobj(inp,out,1024*1024)
                if sides:
                    if sides[0].file_size>1024:raise ValueError('Invalid checksum file.')
                    declared=valid_checksum(z.read(sides[0]).decode('ascii'))
        else:raise ValueError('Use a compiled EXE or ZIP update package.')
        with target.open('rb') as f:
            if f.read(2)!=b'MZ':raise ValueError('Installer is not a Windows executable.')
        actual=sha256(target)
        if declared and declared!=actual:raise ValueError('Update package checksum mismatch.')
        return target,declared,actual
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise

def verify_update(target,expected):
    if sha256(Path(target))!=valid_checksum(expected):raise ValueError('Installer checksum does not match the trusted value.')


def upgrade_arguments(target, exe=None):
    """Use the running installed application, including its stable migrated path.

    Do not guess a fresh install folder when the installed copy cannot be verified.
    Inno uses the same AppId and receives the logical path, preserving junctions.
    """
    import sys
    from .migration import stable_app
    if exe is None:
        if not getattr(sys, 'frozen', False):
            raise RuntimeError('Run the update from the installed Windows app, not a source project.')
        exe = sys.executable
    exe = Path(exe)
    app = stable_app(exe)
    if not any((app / n).is_file() for n in ('Voice Studio V0.1.exe', 'AI_Voice_Studio.exe')) or not any(app.glob('unins*.exe')):
        raise RuntimeError('Could not verify the installed application folder. Use the extracted update launcher.')
    return [str(target), '/AVSOFFLINE=1', '/NORESTART', '/SILENT', '/DIR='+str(app)]
