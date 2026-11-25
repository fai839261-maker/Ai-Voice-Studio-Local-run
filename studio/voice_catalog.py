"""Read-only local voice discovery and opt-in official Piper index refresh."""
import json, os, re, zipfile
from pathlib import Path
from functools import lru_cache

QWEN_LANG=('Auto','Chinese','English','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian')
PIPER_INDEX='https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json'
PIPER_DEFAULTS=('en_GB-alan-medium','en_US-lessac-medium')

def read_json(path):
    try:
        if path.stat().st_size>16*1024*1024:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def piper_rows(index):
    result=[]
    for key, row in index.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',key) or not isinstance(row,dict):continue
        files=row.get('files',{})
        if not isinstance(files,dict):continue
        matches=[p for p in files if isinstance(p,str) and p.endswith('/'+key+'.onnx') and p+'.json' in files and re.fullmatch(r'[A-Za-z0-9_/-]+\.onnx',p) and '..' not in p.split('/') and not p.startswith('/')]
        if len(matches)!=1:continue
        lang=row.get('language',{});lang=lang if isinstance(lang,dict) else {}
        name=str(row.get('name',key));language=str(lang.get('name_english') or lang.get('code') or 'Unknown')
        result.append(dict(id=key,name=f'{name.title()} · {lang.get("code",language)} · {row.get("quality", "")}',language=language,locale=str(lang.get('code','')),repo='rhasspy/piper-voices',prefix=matches[0].rsplit('/',1)[0]+'/',speakers=row.get('num_speakers',1)))
    return sorted(result,key=lambda r:(r['language'],r['name']))

def refresh_piper(root,fetch):
    rows=piper_rows(fetch(PIPER_INDEX))
    if not rows:raise ValueError('Official Piper index did not contain valid voice entries. Previous list kept.')
    file=Path(root)/'piper-voice-index.json';tmp=file.with_suffix('.tmp');tmp.write_text(json.dumps({r['id']:r for r in rows},ensure_ascii=False),encoding='utf-8');os.replace(tmp,file)
    return len(rows)

@lru_cache(maxsize=8)
def _voice_keys(path,mtime,size):
    try:
        with zipfile.ZipFile(path) as z:
            return frozenset(n[:-4] for n in z.namelist() if re.fullmatch(r'[abjzefhip][fm]_[a-z0-9_]+\.npy',n))
    except (OSError,zipfile.BadZipFile):return frozenset()

def available_kokoro(path,voices):
    try:
        s=path.stat();keys=_voice_keys(str(path),s.st_mtime_ns,s.st_size)
    except OSError:keys=set()
    # A missing/unrecognized bank must not be confused with a valid empty list.
    return [v for v in voices if v['id'] in keys] if keys else voices

def merge(model,home):
    model=dict(model);mid=model['id'];folder=home/'models'/mid
    if mid=='kokoro':model['voices']=available_kokoro(folder/'voices-v1.0.bin',model['voices'])
    elif mid=='piper':
        saved=read_json(home/'piper-voice-index.json');old={v['id']:v for v in model['voices']}
        # Revalidate cached entries before using them as download paths.
        for k,v in saved.items():
            if not isinstance(v,dict) or v.get('id')!=k or v.get('repo')!='rhasspy/piper-voices' or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',k):continue
            if not isinstance(v.get('prefix'),str) or not re.fullmatch(r'(?:[A-Za-z0-9_-]+/)+',v['prefix']):continue
            if not isinstance(v.get('name'),str) or not isinstance(v.get('language'),str):continue
            old[k]=v
        model['voices']=list(old.values())
    elif mid.startswith('qwen') and model['kind']=='tts':
        cfg=read_json(folder/'config.json');talker=cfg.get('talker_config',{});speakers=talker.get('spk_id',{}) if isinstance(talker,dict) else {}
        if isinstance(speakers,dict) and speakers:
            known={v['id'].lower():v for v in model['voices']}
            actual=[known.get(k.lower(),dict(id=k,name=k.replace('_',' ').title(),language='Multilingual')) for k in speakers if isinstance(k,str) and re.fullmatch(r'[A-Za-z0-9_]{1,60}',k)]
            if actual:model['voices']=actual
    return model

def piper_targets(home,model,selected=None):
    known={v['id'] for v in model['voices']}
    defaults=[x for x in PIPER_DEFAULTS if x in known]
    installed=[x for x in known if (home/'models'/'piper'/x/(x+'.onnx')).is_file()]
    return list(dict.fromkeys([*defaults,*sorted(installed),*([selected] if selected in known else [])]))
