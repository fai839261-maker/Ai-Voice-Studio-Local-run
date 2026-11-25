"""Read official Hugging Face metadata, retaining direct URL compatibility."""
import threading,time
from urllib.parse import urlsplit,unquote,quote
import re
CACHE={};LOCK=threading.Lock()

def info_for(repo,revision,fetch):
    key=repo,revision
    with LOCK:
        old=CACHE.get(key)
        if old and time.monotonic()-old[0]<600:return old[1]
    info=fetch('https://huggingface.co/api/models/'+repo+'/revision/'+revision+'?blobs=true')
    with LOCK:CACHE[key]=(time.monotonic(),info)
    return info

def hydrate(files,fetch):
    result=[];failed=set()
    for src in files:
        item=dict(src);u=urlsplit(item['url']);bits=u.path.strip('/').split('/')
        if u.hostname=='huggingface.co' and len(bits)>=5 and bits[2]=='resolve':
            try:
                repo='/'.join(bits[:2]);revision=bits[3];name=unquote('/'.join(bits[4:]));
                if (repo,revision) in failed:raise ValueError('Metadata unavailable')
                try:data=info_for(repo,revision,fetch)
                except (OSError,RuntimeError,ValueError):failed.add((repo,revision));raise
                match=next(x for x in data['siblings'] if x['rfilename']==name)
                if match.get('size') is not None:item['size']=match['size']
                if (match.get('lfs') or {}).get('sha256'):item['sha256']=match['lfs']['sha256']
                rev=data.get('sha','')
                if re.fullmatch('[0-9a-f]{40}',rev):item['url']=f'https://huggingface.co/{repo}/resolve/{rev}/'+quote(name)
            except (OSError,RuntimeError,KeyError,ValueError,StopIteration):pass
        result.append(item)
    return result
