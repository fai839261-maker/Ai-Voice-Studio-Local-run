"""Bounded accelerated HTTPS transfer. Only complete contiguous windows are appended.

The existing .part file remains the recovery checkpoint; no sparse preallocation,
extra on-disk copy, or unverified mirror. A full upstream digest is mandatory.
"""
import re, threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BLOCK=8*1024**2
MINIMUM=16*1024**2
# At most two additional connections (normally four file slots + these two).
EXTRA=threading.BoundedSemaphore(2)

class RangeUnavailable(Exception): pass

def eligible(item,offset):
    return (isinstance(item.get('size'),int) and item['size']-offset>=MINIMUM
            and bool(re.fullmatch(r'[a-fA-F0-9]{64}',item.get('sha256') or '')))

def accelerate(part,item,check,progress,committed,space_check):
    """Try parallel exact ranges. Return False to fall back to ordinary resume.
    Network/protocol failures do not delete the contiguous partial file.
    Cancellation and disk errors propagate, rather than masquerading as fallback.
    """
    from . import downloads as d
    part=Path(part);size=item.get('size');offset=part.stat().st_size if part.is_file() else 0
    if not eligible(item,offset) or not EXTRA.acquire(blocking=False):return False
    stop=threading.Event();report_lock=threading.Lock();counts={};last_report=[0.0]
    def guard():
        check()
        if stop.is_set():raise RangeUnavailable('Sibling connection stopped')
    def fetch(start,end):
        guard()
        try:
            with d.open_secure(d.request(item['url'],{'Range':f'bytes={start}-{end}'})) as r:
                if r.status!=206 or r.headers.get('Content-Range','')!=f'bytes {start}-{end}/{size}' or r.headers.get('Content-Encoding','identity').lower() not in ('identity',''):
                    raise RangeUnavailable('Server does not provide exact byte ranges')
                data=bytearray();needed=end-start+1
                while len(data)<needed:
                    guard();buf=r.read(min(256*1024,needed-len(data)))
                    if not buf:raise d.Incomplete('Short range response')
                    data.extend(buf)
                    with report_lock:
                        counts[start]=len(data);now=time.monotonic()
                        if now-last_report[0]>=.35:
                            progress(offset+sum(counts.values()));last_report[0]=now
                    if len(data)>needed:raise RangeUnavailable('Oversized range')
                if r.read(1):raise RangeUnavailable('Oversized range')
                return data
        except RangeUnavailable:raise
        except Exception as exc:
            if d.retryable(exc) or isinstance(exc,d.urllib.error.HTTPError):raise RangeUnavailable(str(exc)) from exc
            raise
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            while offset<size:
                check();end1=min(size-1,offset+BLOCK-1);end2=min(size-1,end1+BLOCK)
                spans=[(offset,end1)]+([(end1+1,end2)] if end1<end2 else [])
                counts.clear();futures=[pool.submit(fetch,*span) for span in spans]
                try:buffers=[f.result() for f in futures]
                except BaseException:
                    stop.set()
                    for f in futures:f.cancel()
                    raise
                check();space_check(sum(map(len,buffers)))
                with part.open('ab' if offset else 'wb') as out:
                    for data in buffers:out.write(data)
                offset=part.stat().st_size;committed(offset);progress(offset)
        return True
    except RangeUnavailable:
        check();return False
    finally:EXTRA.release()
