"""Optional official CPU C++ SDK. Not used for Python inference."""
import os,shutil,stat,zipfile
from pathlib import Path
from . import downloads,managed
from .core import subdir,sha256
VERSION='2.8.0'
NAME='libtorch-win-shared-with-deps-2.8.0+cpu.zip'
URL='https://download.pytorch.org/libtorch/cpu/libtorch-win-shared-with-deps-2.8.0%2Bcpu.zip'

def install(cancel,progress,log):
    if os.name!='nt':raise RuntimeError('Windows x64 LibTorch SDK only.')
    base=subdir('native');dest=base/'libtorch-2.8.0-cpu';archive=base/NAME
    if (dest/'libtorch/lib/torch.dll').is_file() and (dest/'libtorch/include/torch').is_dir():return 'LibTorch SDK installed'
    check=lambda:managed.check_cancel(cancel)
    item={'name':NAME,'url':URL,'size':downloads.head_size(URL)}
    downloads.download_set(base,[item],progress,check,managed.valid_file,sha256,manifest=False)
    stage=base/'libtorch-2.8.0-cpu.extracting';shutil.rmtree(stage,ignore_errors=True);stage.mkdir()
    try:
        with zipfile.ZipFile(archive) as z:
            from .source_update import safe_parts
            entries=z.infolist();total=sum(i.file_size for i in entries)
            if total+downloads.RESERVE>shutil.disk_usage(base).free:raise RuntimeError('Insufficient space to extract LibTorch.')
            done=0;seen=set()
            for i in entries:
                check();parts=safe_parts(i.filename);key='/'.join(parts).casefold()
                if parts[0]!='libtorch' or key in seen or stat.S_IFMT(i.external_attr>>16) not in (0,stat.S_IFREG,stat.S_IFDIR):raise ValueError('Unsafe SDK archive entry.')
                seen.add(key);p=stage.joinpath(*parts)
                if i.is_dir():p.mkdir(parents=True,exist_ok=True);continue
                p.parent.mkdir(parents=True,exist_ok=True)
                with z.open(i) as src,p.open('xb') as out:
                    while True:
                        check();buf=src.read(1024*1024)
                        if not buf:break
                        out.write(buf);done+=len(buf)
                progress(done,total,'Extracting LibTorch')
        if not (stage/'libtorch/lib/torch.dll').is_file() or not (stage/'libtorch/include/torch').is_dir():raise ValueError('Incomplete LibTorch SDK.')
        if dest.exists():raise RuntimeError('Existing incomplete SDK folder; remove only that folder and retry.')
        stage.rename(dest);archive.unlink(missing_ok=True);log('LibTorch SDK installed at '+str(dest));return 'LibTorch SDK installed'
    finally:shutil.rmtree(stage,ignore_errors=True)
