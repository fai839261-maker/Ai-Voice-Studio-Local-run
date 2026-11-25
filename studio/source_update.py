"""Validate source ZIPs without executing them; build only after explicit trust.
A hash records integrity, not publisher identity. Source/build tools execute code.
"""
from pathlib import Path
from dataclasses import dataclass
from contextlib import closing
import json, os, re, shutil, sqlite3, stat, tempfile, zipfile
from .core import subdir, sha256, APP_VERSION
from .update_local import stage_update, version_tuple
from . import external

MANIFEST='AVS_SOURCE_UPDATE.json'
MAX_SOURCE=128*1024**2
MAX_FILES=4000
MAX_MEMBER=64*1024**2
ROOT_FILES={MANIFEST,'main.py','avs_boot.py','Build-Windows.ps1','requirements-gui.txt','README.md',
 'WINDOWS_RELEASE_CHECKLIST.md','VALIDATION.txt','OFFLINE_UPDATE_README.txt','BUILD_INSTALLER.bat',
 'BUILD_OFFLINE_UPDATE.bat','APPLY_OFFLINE_UPDATE.bat','Apply-Offline-Update.ps1','UPDATE_MY_SOFTWARE.vbs',
 'Upgrade-Installed.ps1','SOURCE_UPDATE_README.txt','.gitignore','Build-Mac.sh','BUILD_MAC.command','AI_Voice_Studio_mac.spec','README_MAC.md'}
DIRS={'studio','runtime','resources','installer','tests','.github'}
REQUIRED={MANIFEST,'main.py','Build-Windows.ps1','requirements-gui.txt','studio/core.py',
 'studio/ui_v3.py','installer/AI_Voice_Studio.iss','resources/catalog.json','runtime/runner.py'}
BAD_WINDOWS=re.compile(r'[<>:"|?*\x00-\x1f]')
DEVICES={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in '123456789¹²³'),*(f'LPT{i}' for i in '123456789¹²³')}

@dataclass(frozen=True)
class SourcePackage:
    project: Path
    version: str
    digest: str


def safe_parts(name):
    if '\\' in name or name.startswith('/'):
        raise ValueError('Unsafe source archive path.')
    parts=name.rstrip('/').split('/')
    if any(not p or p in ('.','..') or p.endswith((' ','.')) or BAD_WINDOWS.search(p) or p.split('.')[0].upper() in DEVICES for p in parts):
        raise ValueError('Unsafe source archive path.')
    return parts


def is_source_zip(path):
    if Path(path).suffix.lower()!='.zip':return False
    with zipfile.ZipFile(path) as z:
        return any(not i.is_dir() and i.filename.split('/')[-1]==MANIFEST for i in z.infolist())


def stage_source(path):
    path=Path(path)
    if not 0<path.stat().st_size<=MAX_SOURCE:raise ValueError('Invalid source archive size.')
    cache=subdir('updates'); stage=Path(tempfile.mkdtemp(prefix='source-',dir=cache))
    try:
        if shutil.disk_usage(stage).free<path.stat().st_size:raise ValueError('Insufficient storage for source archive.')
        local=stage/'source.zip';shutil.copyfile(path,local);digest=sha256(local)
        with zipfile.ZipFile(local) as z:
            entries=z.infolist()
            if len(entries)>MAX_FILES or sum(i.file_size for i in entries)>MAX_SOURCE:raise ValueError('Source archive exceeds limits.')
            for i in entries:
                safe_parts(i.filename)
                if stat.S_IFMT(i.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR) or i.flag_bits&1:
                    raise ValueError('Links, special files or encrypted entries are not allowed.')
                if i.file_size>MAX_MEMBER:raise ValueError('Source member exceeds size limit.')
            files=[i for i in entries if not i.is_dir()]
            manifests=[i for i in files if i.filename.split('/')[-1]==MANIFEST]
            if len(manifests)!=1:raise ValueError('Expected exactly one source update manifest.')
            prefix=safe_parts(manifests[0].filename)[:-1]
            if len(prefix)>1:raise ValueError('Unexpected nested source project.')
            rels={};seen=set();directories=set()
            for i in entries:
                parts=safe_parts(i.filename)
                if prefix and parts==prefix and i.is_dir():continue
                if parts[:len(prefix)]!=prefix:raise ValueError('Unexpected files outside source project.')
                rel=parts[len(prefix):];name='/'.join(rel);fold=name.casefold()
                if not rel or (len(rel)==1 and not i.is_dir() and rel[0] not in ROOT_FILES) or (len(rel)>1 and rel[0] not in DIRS) or (i.is_dir() and rel[0] not in DIRS):
                    raise ValueError('Unexpected source project entry: '+name)
                if fold in seen:raise ValueError('Duplicate source archive entry.')
                seen.add(fold)
                if i.is_dir():directories.add(fold);continue
                rels[name]=i
            names={n.casefold() for n in rels}
            if any('/'.join(n.split('/')[:j]) in names for n in names|directories for j in range(1,len(n.split('/')))) or names&directories:
                raise ValueError('Conflicting archive paths.')
            if not REQUIRED<=rels.keys():raise ValueError('Source project is missing required files.')
            if manifests[0].file_size>4096:raise ValueError('Invalid source manifest size.')
            m=json.loads(z.read(manifests[0]));v=m.get('version','')
            if m.get('format')!='avs-source-update-v1' or m.get('app')!='AI Voice Studio' or not isinstance(v,str) or not re.fullmatch(r'\d{1,5}\.\d{1,5}\.\d{1,5}',v):raise ValueError('Invalid source update manifest.')
            if version_tuple(v)<version_tuple(APP_VERSION):raise ValueError('Older source update rejected.')
            if shutil.disk_usage(stage).free<sum(i.file_size for i in files):raise ValueError('Insufficient storage to extract source update.')
            project=stage/'project';project.mkdir()
            for rel,i in rels.items():
                dest=project.joinpath(*rel.split('/'));dest.parent.mkdir(parents=True,exist_ok=True)
                with z.open(i) as src,dest.open('xb') as out:shutil.copyfileobj(src,out,1024*1024)
        return SourcePackage(project,v,digest)
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True);raise


def stage_any(path):
    return stage_source(path) if is_source_zip(path) else stage_update(path)


def powershell_path():
    return Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'


def build_source(package,offline=False,build_environment=None):
    if os.name!='nt':raise RuntimeError('Source update compilation requires Windows.')
    project=package.project;log=project.parent/'source-build.log'
    args=[powershell_path(),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',project/'Build-Windows.ps1']
    if offline:args.append('-Offline')
    if build_environment:args.extend(['-BuildEnvironment',str(build_environment)])
    with log.open('wb') as out:
        result=external.run(args,cwd=project,stdout=out,stderr=out)
    if result.returncode!=0:raise RuntimeError(f'Source build failed (exit {result.returncode}). Log: {log}')
    installer=project/'BuildOut'/f'AI_Voice_Studio_Setup_{package.version}.exe'
    report=project/'BuildOut'/'packaged-self-check.json'
    if not report.is_file():raise RuntimeError(f'Missing packaged self-check. Log: {log}')
    check=json.loads(report.read_text(encoding='utf-8-sig'))
    if check.get('result')!='PASS' or check.get('missing') or check.get('version')!=package.version:raise RuntimeError(f'Packaged self-check did not pass. Log: {log}')
    return stage_update(installer)


def backup_state(db):
    db=Path(db)
    if not db.is_file():raise RuntimeError('Existing application database not found; update not started.')
    dest=Path(tempfile.mkdtemp(prefix='before-update-',dir=subdir('updates')))/db.name
    with closing(sqlite3.connect(db)) as src,closing(sqlite3.connect(dest)) as out:src.backup(out)
    return dest
