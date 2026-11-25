"""Attach trusted local assets, never execute ZIP source or modify adapter code."""
import os, stat, zipfile, tempfile, shutil
from pathlib import Path
from contextlib import contextmanager
from .source_update import safe_parts
from .core import sha256, subdir
from . import downloads

@contextmanager
def candidates(source):
    source=Path(source)
    if source.is_dir():
        files={}
        for root,dirs,names in os.walk(source,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(root)/d).is_symlink() and not (getattr((Path(root)/d).lstat(),'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024))]
            for n in names:
                p=Path(root)/n
                if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()) or not p.is_file():continue
                files[p.relative_to(source).as_posix()]=(p.stat().st_size,lambda p=p:p.open('rb'))
        yield files;return
    with zipfile.ZipFile(source) as z:
        files={};seen=set()
        if len(z.infolist())>100000:raise ValueError('Archive contains too many entries')
        for i in z.infolist():
            name='/'.join(safe_parts(i.filename))
            if name.casefold() in seen:raise ValueError('Duplicate archive paths')
            seen.add(name.casefold())
            if stat.S_IFMT(i.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR) or i.flag_bits&1:raise ValueError('Archive links or encrypted files are not supported')
            if not i.is_dir():files[name]=(i.file_size,lambda i=i:z.open(i))
        yield files


def import_assets(source,folder,items,progress,check):
    folder=Path(folder);count=0
    with candidates(source) as files:
        for item in items:
            check();name=item['name'];dest=folder/name;size=item.get('size');digest=item.get('sha256')
            if not dest.resolve().is_relative_to(folder.resolve()):raise ValueError('Invalid model path')
            if dest.is_file() and size==dest.stat().st_size and (not digest or sha256(dest)==digest):continue
            # Match exact relative suffix, retaining nested tokenizer/vocoder paths.
            matches=[v for k,v in files.items() if k==name or k.endswith('/'+name)]
            if len(matches)!=1:continue
            n,opener=matches[0]
            if not n or (size is not None and n!=size) or (size is None and not digest):continue
            # Binary formats without official SHA256 are allowed only after explicit
            # user trust confirmation in the UI; size is not authentication.
            if shutil.disk_usage(folder).free<n+downloads.RESERVE:raise RuntimeError('Not enough space to attach model files')
            dest.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as out:
                tmp=Path(out.name)
            try:
                with opener() as src,tmp.open('wb') as out:
                    copied=0
                    for buf in iter(lambda:src.read(2**20),b''):
                        check();copied+=len(buf)
                        if copied>n:raise ValueError('Archive file length changed')
                        out.write(buf);progress(copied,n,'Checking attached '+name)
                if tmp.stat().st_size!=n or (digest and sha256(tmp)!=digest):continue
                if name.endswith('.json'):
                    import json
                    json.loads(tmp.read_text(encoding='utf-8'))
                os.replace(tmp,dest);item['size']=n;count+=1
            finally:tmp.unlink(missing_ok=True)
    return count
