"""Process-local engine and reference cache; only one engine per worker."""
from pathlib import Path
from collections import OrderedDict
import hashlib
_engine_key=None
_engine=None
_prompts=OrderedDict()

def engine(key,factory):
    global _engine_key,_engine
    if _engine_key != key or _engine is None:
        _engine=None;_engine_key=None;_prompts.clear()
        try:
            import gc;gc.collect()
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()
        except Exception:pass
        _engine=factory();_engine_key=key
    return _engine

def reference_key(path,transcript):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest(),transcript

def prompt(key,factory):
    if key not in _prompts:
        _prompts[key]=factory()
        if len(_prompts)>4:_prompts.popitem(last=False)
    _prompts.move_to_end(key)
    return _prompts[key]
