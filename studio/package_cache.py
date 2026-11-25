"""Verified reuse of pip HTTP bodies and exact official Windows PyTorch wheels."""
import hashlib, os, shutil, tempfile
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, unquote, parse_qs
from pathlib import Path
from . import downloads

class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href:self.links.append(href)


def reuse_http_body(cache,url,dest,digest,check=lambda:None):
    # pip's CacheControl HTTP cache uses SHA224(URL), and a separate .body.
    # Never trust the cache key alone: verify the resolved archive SHA256.
    key=hashlib.sha224(url.encode()).hexdigest()
    src=Path(cache)/'http-v2'/Path(*key[:5])/ (key+'.body')
    if not src.is_file():return False
    check();h=hashlib.sha256()
    with src.open('rb') as f:
        for buf in iter(lambda:f.read(2**20),b''):check();h.update(buf)
    if h.hexdigest()!=digest.lower():return False
    with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as f:tmp=Path(f.name)
    try:
        with src.open('rb') as a,tmp.open('wb') as b:
            for buf in iter(lambda:a.read(2**20),b''):check();b.write(buf)
        if downloads_hash(tmp)!=digest.lower():return False
        os.replace(tmp,dest);return True
    finally:tmp.unlink(missing_ok=True)


def downloads_hash(path):
    from .core import sha256
    return sha256(path)


def torch_report(name,version,tag):
    index=f'https://download.pytorch.org/whl/{tag}/{name}/'
    expected=f'{name}-{version}+{tag}-cp311-cp311-win_amd64.whl'
    def action():
        with downloads.open_secure(downloads.request(index)) as r:
            parser=Links();parser.feed(r.read(8*1024**2).decode('utf-8'));return parser.links
    for link in downloads.retry(action,attempts=3):
        url=urljoin(index,link);u=urlsplit(url)
        if u.scheme!='https' or u.hostname not in ('download.pytorch.org','download-r2.pytorch.org'):continue
        if unquote(u.path.rsplit('/',1)[-1])!=expected:continue
        digest=parse_qs(u.fragment).get('sha256',[''])[0]
        import re
        if re.fullmatch('[0-9a-fA-F]{64}',digest):return {'download_info':{'url':url.split('#')[0],'archive_info':{'hashes':{'sha256':digest}}}}
    return None
