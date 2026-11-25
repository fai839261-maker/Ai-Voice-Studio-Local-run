"""Reuse identical verified weights across model folders without shared mutable links."""
from pathlib import Path
import json, os, shutil, tempfile, hashlib
from .core import subdir
from . import downloads


def reuse_weights(folder,items,progress,check):
    folder=Path(folder); candidates={}
    targets={x.get('sha256') for x in items if x.get('sha256')}
    if not targets:return 0
    for mf in subdir('models').rglob('.avs-manifest.json'):
        check()
        if mf.parent==folder:continue
        try:plan=json.loads(mf.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        for item in plan:
            digest=item.get('sha256')
            if digest not in targets:continue
            p=mf.parent/item['name']
            if p.resolve().is_relative_to(mf.parent.resolve()) and p.is_file():candidates.setdefault(digest,[]).append(p)
    count=0
    for item in items:
        check();dest=folder/item['name'];digest=item.get('sha256');size=item.get('size')
        if not dest.resolve().is_relative_to(folder.resolve()):raise ValueError('Unsafe model file path')
        if dest.exists() or not digest:continue
        for path in candidates.get(digest,[]):
            check();n=path.stat().st_size
            if size is not None and size!=n:continue
            if shutil.disk_usage(folder).free<n+downloads.RESERVE:break
            dest.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as f:tmp=Path(f.name)
            try:
                h=hashlib.sha256()
                with path.open('rb') as src,tmp.open('wb') as out:
                    for b in iter(lambda:src.read(2**20),b''):check();h.update(b);out.write(b)
                if h.hexdigest()!=digest.lower() or tmp.stat().st_size!=n:continue
                os.replace(tmp,dest);item['size']=n;count+=1;progress(0,None,'Checking reused model files');break
            finally:tmp.unlink(missing_ok=True)
    return count
