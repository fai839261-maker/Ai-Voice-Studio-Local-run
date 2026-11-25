"""Resumable HTTPS transfers shared by all catalog and imported models."""
import errno, hashlib, http.client, json, os, re, shutil, threading, time
from . import ranged_transfer
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from . import network_trust
from pathlib import Path
from urllib.parse import urlsplit

TIMEOUT=90
ATTEMPTS=8
RESERVE=128*1024**2
LOCK=threading.Lock()
CLAIMS={}

class StorageError(RuntimeError): pass
class Incomplete(RuntimeError): pass

def part_path(dest,item):
    tag=hashlib.sha256(item['url'].encode()).hexdigest()[:12]
    return dest.with_name(dest.name+'.'+tag+'.part')

def offset_for(dest,item):
    part=part_path(dest,item)
    n=part.stat().st_size if part.is_file() else 0
    size=item.get('size')
    return n if size is None or n<=size else 0

def retryable(exc):
    if network_trust.certificate_error(exc):return False
    if isinstance(exc,urllib.error.HTTPError):return exc.code in (408,425,429,500,502,503,504,520,522,524)
    return isinstance(exc,(urllib.error.URLError,TimeoutError,ConnectionError,http.client.HTTPException,Incomplete)) or isinstance(exc,OSError) and (getattr(exc,"winerror",None) in (10053,10054,10060,10061,11001) or exc.errno in (errno.ECONNRESET,errno.ECONNABORTED,errno.ETIMEDOUT,errno.EPIPE))

_FAST=threading.local()

class fast_mode:
    """Inside this block a lookup gives up quickly (2 tries, short waits, 10 s timeout). Used for sizes and update checks only."""
    def __enter__(self):_FAST.on=getattr(_FAST,'on',0)+1;return self
    def __exit__(self,*_):_FAST.on-=1

def is_fast():return getattr(_FAST,'on',0)>0

def retry(fn,check=lambda:None,notice=lambda *_:None,attempts=ATTEMPTS):
    if is_fast():attempts=min(attempts,2)
    for i in range(attempts):
        check()
        try:return fn()
        except Exception as exc:
            if not retryable(exc) or i+1==attempts:raise
            delay=min(1 if is_fast() else 30,2**i)
            if isinstance(exc,urllib.error.HTTPError):
                try:delay=min(60,max(delay,int(exc.headers.get('Retry-After','0'))))
                except (ValueError,TypeError,AttributeError):pass
            notice(f'Connection interrupted: {str(exc)[:150]}; retry {i+2}/{attempts} in {delay}s')
            end=time.monotonic()+delay
            while time.monotonic()<end:check();time.sleep(min(.25,max(0,end-time.monotonic())))

def request(url,headers=None,method='GET'):
    if not url.lower().startswith('https://'):raise ValueError('Only HTTPS model sources are supported.')
    return urllib.request.Request(url,headers={'User-Agent':'AI-Voice-Studio/0.22.0','Accept-Encoding':'identity',**(headers or {})},method=method)

class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not newurl.lower().startswith('https://'):raise ValueError('Insecure download redirect refused.')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

OPENER=urllib.request.build_opener(SecureRedirect(), urllib.request.HTTPSHandler(context=network_trust.context()))
def open_secure(req,timeout=TIMEOUT):
    try:response=OPENER.open(req,timeout=timeout)
    except (OSError,urllib.error.URLError) as exc:
        if not network_trust.certificate_error(exc):raise
        # Try a second independently verified trust configuration, not an
        # unverified connection. Do not silently accept a bad certificate.
        fallback=urllib.request.build_opener(SecureRedirect(),urllib.request.HTTPSHandler(context=network_trust.public_context()))
        try:response=fallback.open(req,timeout=timeout)
        except (OSError,urllib.error.URLError) as second:
            if not network_trust.certificate_error(second):raise
            raise RuntimeError("HTTPS certificate verification failed. Check Windows date/time, Windows trusted root updates and antivirus/proxy certificates; retain TLS verification. Host: "+str(urlsplit(req.full_url).hostname)) from second
    if not response.geturl().lower().startswith('https://'):
        response.close();raise ValueError('Insecure download redirect refused.')
    return response

def fetch_json(url,check=lambda:None):
    def action():
        with open_secure(request(url),timeout=10 if is_fast() else 25) as response:return json.load(response)
    return retry(action,check,attempts=4)

def response_size(headers):
    span=headers.get('Content-Range','')
    match=re.fullmatch(r'bytes \d+-\d+/(\d+)',span)
    if match:return int(match[1])
    if span:return None
    for k in ('X-Linked-Size','Content-Length'):
        val=headers.get(k)
        if val and str(val).isdigit() and int(val)>0:return int(val)
    return None

def head_size(url):
    def action():
        try:
            with open_secure(request(url,method='HEAD'),timeout=10 if is_fast() else 20) as r:
                n=response_size(r.headers)
                if n:return n
        except urllib.error.HTTPError as exc:
            if exc.code not in (400,403,405,501):raise
        with open_secure(request(url,{'Range':'bytes=0-0'}),timeout=10 if is_fast() else 20) as r:
            return response_size(r.headers) if r.status in (200,206) and (r.status==200 or r.headers.get('Content-Range')) else None
    try:return retry(action,attempts=2)
    except (OSError,urllib.error.URLError,http.client.HTTPException,RuntimeError):return None

def volume_key(folder):
    if os.name=='nt':return os.path.splitdrive(str(folder.resolve()))[0].lower()
    return folder.stat().st_dev

def disk_error(folder,required,free):
    return StorageError(f'Insufficient storage on {folder}: need {required/1e9:.2f} GB, available {free/1e9:.2f} GB. Partial files retained. Free space or use Settings → Move folder.')

def download_set(folder,items,progress,check,valid,sha256,manifest=True):
    folder=Path(folder);total=sum(x['size'] for x in items) if all(x.get('size') is not None for x in items) else None
    # Only unfinished bytes require extra space. A failed hash requires the full file.
    verified={}
    def remaining(item):
        dest=folder/item['name'];size=item.get('size') or 0
        if valid(dest,item) and (not item.get('sha256') or sha256(dest)==item['sha256']):
            st=dest.stat();verified[item['name']]=(st.st_size,st.st_mtime_ns);return 0
        return max(0,size-offset_for(dest,item))
    for item in items:
        if not (folder/item['name']).resolve().is_relative_to(folder.resolve()):raise ValueError('Unsafe model file path')
    need=0
    for x in items:
        check();need+=remaining(x)
    physical=folder.resolve();key=volume_key(folder);ticket=object()
    with LOCK:
        claimed=sum(v for k,v in CLAIMS.values() if k==key);free=shutil.disk_usage(folder.resolve()).free
        if need and need+claimed+RESERVE>free:raise disk_error(folder,need+claimed+RESERVE,free)
        CLAIMS[ticket]=(key,need)
    done=0
    try:
        for item in items:
            check();dest=folder/item['name']
            if not dest.resolve().is_relative_to(folder.resolve()):raise ValueError('Unsafe model file path')
            dest.parent.mkdir(parents=True,exist_ok=True)
            if item['name'] in verified and valid(dest,item) and (dest.stat().st_size,dest.stat().st_mtime_ns)==verified[item['name']]:
                done+=dest.stat().st_size;progress(done,total,'Checking existing files');continue
            part=part_path(dest,item);size=item.get('size');last=offset_for(dest,item);fast_allowed=True
            def transfer():
                nonlocal last,fast_allowed,size,total
                check();offset=offset_for(dest,item)
                if size is not None and offset==size and part.exists():
                    if not item.get('sha256') or sha256(part)==item['sha256']:return
                    part.unlink();offset=0
                if part.exists() and part.stat().st_size!=offset:part.unlink()
                def commit(received):
                    nonlocal last
                    with LOCK:
                        k,v=CLAIMS[ticket];CLAIMS[ticket]=(k,max(0,v-max(0,received-last)));last=max(last,received)
                def space_check(n):
                    free=shutil.disk_usage(physical).free
                    if free<n+RESERVE:raise disk_error(folder,n+RESERVE,free)
                if fast_allowed and ranged_transfer.eligible(item,offset):
                    progress(done+offset,total,'Connecting: parallel transfer')
                    try:
                        fast=ranged_transfer.accelerate(part,item,check,lambda n:progress(done+n,total,item['name']),commit,space_check)
                    except OSError as exc:
                        if exc.errno==errno.ENOSPC or getattr(exc,'winerror',None)==112:raise disk_error(folder,1,shutil.disk_usage(physical).free) from exc
                        raise
                    fast_allowed=False
                    if fast:
                        check()
                        progress(done+part.stat().st_size,total,'Verifying '+item['name'])
                        if part.stat().st_size==size and sha256(part)==item['sha256'].lower():return
                        part.unlink(missing_ok=True);last=0
                        raise Incomplete('Parallel transfer checksum mismatch; retrying ordinary download')
                    offset=offset_for(dest,item)
                    progress(done+offset,total,'Server cannot resume parallel ranges; using ordinary transfer')
                headers={'Range':f'bytes={offset}-'} if offset else {}
                progress(done+offset,total,('Resuming ' if offset else 'Connecting: ')+item['name']+' · '+str(urlsplit(item['url']).hostname))
                try:
                    response=open_secure(request(item['url'],headers))
                except urllib.error.HTTPError as exc:
                    if exc.code==416 and offset:
                        # Server no longer accepts the saved range (changed or unknown length).
                        # Never append bytes to a rejected offset.
                        part.unlink(missing_ok=True);last=0
                        raise Incomplete('Server rejected resume range; restarting file') from exc
                    raise
                with response as r:
                    if r.status==206:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)',r.headers.get('Content-Range',''))
                        if not match or int(match[1])!=offset or int(match[2])<offset:raise Incomplete('Invalid range response')
                        remote_total=int(match[3]) if match[3]!='*' else None
                        if size is not None and remote_total is not None and size!=remote_total:raise Incomplete('Remote file size changed')
                    elif r.status==200:
                        if offset:progress(done,total,'Server cannot resume this file; restarting '+item['name'])
                        offset=0;last=0;remote_total=None
                    else:raise Incomplete(f'Unexpected HTTP status {r.status}')
                    if r.headers.get('Content-Encoding','identity').lower() not in ('identity',''):
                        raise Incomplete('Unexpected compressed response; cannot safely resume')
                    length=r.headers.get('Content-Length');expected=remote_total if remote_total is not None else offset+int(length) if length and length.isdigit() else None
                    if size is not None:expected=size
                    if item.get('size') is None and expected is not None:
                        item['size']=size=expected
                        if all(x.get('size') is not None for x in items):total=sum(x['size'] for x in items)
                        if fast_allowed and ranged_transfer.eligible(item,offset) and (r.status==206 or not part.exists()):
                            r.close();return transfer()
                    with part.open('ab' if offset else 'wb') as f:
                        received=offset;next_storage_check=offset;last_report=time.monotonic();reported=offset
                        while True:
                            check();buf=getattr(r,'read1',r.read)(256*1024)
                            if not buf:break
                            # OS writes report disk-full too. Avoid a filesystem
                            # space query and shared lock on every HTTPS packet.
                            if received>=next_storage_check:
                                free=shutil.disk_usage(physical).free
                                if free<len(buf)+RESERVE:raise disk_error(folder,len(buf)+RESERVE,free)
                                next_storage_check=received+8*1024*1024
                            try:f.write(buf)
                            except OSError as exc:
                                if exc.errno==errno.ENOSPC or getattr(exc,'winerror',None)==112:raise disk_error(folder,len(buf)+RESERVE,shutil.disk_usage(physical).free) from exc
                                raise
                            received+=len(buf)
                            if received-last>=1024**2:commit(received)
                            if expected is not None and received>expected:
                                f.close();part.unlink(missing_ok=True);last=0;raise Incomplete('Server sent more bytes than expected')
                            now=time.monotonic()
                            if now-last_report>=.35 or received-reported>=256*1024:
                                progress(done+received,sum(x['size'] for x in items) if all(x.get('size') is not None for x in items) else total,item['name']);last_report=now;reported=received
                        commit(received)
                        progress(done+received,total,item['name']);check()
                    if expected is None and not item.get('sha256'):raise Incomplete('No reliable remote length or checksum: '+item['name'])
                    if expected is not None and part.stat().st_size!=expected:raise Incomplete('Incomplete response: '+item['name'])
                if not valid(part,item):raise Incomplete('Incomplete download: '+item['name'])
                progress(done+part.stat().st_size,total,'Verifying '+item['name'])
                if item.get('sha256') and sha256(part)!=item['sha256']:
                    part.unlink(missing_ok=True);raise Incomplete('Checkpoint hash mismatch: '+item['name'])
            retry(transfer,check,lambda s:progress(done+offset_for(dest,item),total,s))
            check()
            if dest.suffix=='.json':json.loads(part.read_text(encoding='utf-8'))
            os.replace(part,dest);item['size']=dest.stat().st_size;done+=dest.stat().st_size;progress(done,total,item['name'])
        if manifest:
            tmp=folder/'.avs-manifest.tmp';tmp.write_text(json.dumps(items),encoding='utf-8');os.replace(tmp,folder/'.avs-manifest.json')
        progress(done,total,'Weights downloaded')
    finally:
        with LOCK:CLAIMS.pop(ticket,None)


# Limit the sum of parallel model-file transfers across all active models.
FILE_SLOTS=threading.BoundedSemaphore(4)

def parallel_set(folder,items,progress,check,valid,sha256):
    if len(items)<=1:
        while not FILE_SLOTS.acquire(timeout=.2):check()
        try:return download_set(folder,items,progress,check,valid,sha256)
        finally:FILE_SLOTS.release()
    names=[x['name'].casefold() for x in items]
    if len(names)!=len(set(names)):raise ValueError('Duplicate model filenames')
    lock=threading.Lock();stop=threading.Event();amounts=[0]*len(items);failures=[]
    def guard():
        check()
        if stop.is_set():raise InterruptedError('Sibling transfer ended')
    def total():return sum(x['size'] for x in items) if all(x.get('size') is not None for x in items) else None
    def work(i,item):
        def update(done,_,stage):
            with lock:amounts[i]=done;progress(sum(amounts),total(),stage)
        while not FILE_SLOTS.acquire(timeout=.2):guard()
        try:download_set(folder,[item],update,guard,valid,sha256,manifest=False)
        except BaseException as exc:
            with lock:
                if not stop.is_set():failures.append(exc)
                stop.set()
            raise
        finally:FILE_SLOTS.release()
    # Two files per model; large files get started early. Never concatenate ranges
    # from different mirrors or bypass the existing per-file integrity checks.
    ordered=sorted(enumerate(items),key=lambda x:-(x[1].get('size') or 0))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(work,i,item) for i,item in ordered]
        try:
            for f in as_completed(futures):f.result()
        except BaseException:
            stop.set()
            for f in futures:f.cancel()
            if failures:raise failures[0]
            raise
    check();tmp=Path(folder)/'.avs-manifest.tmp';tmp.write_text(json.dumps(items),encoding='utf-8');os.replace(tmp,Path(folder)/'.avs-manifest.json')
    progress(sum(amounts),total(),'Weights downloaded')
