"""Register only vetted adapter families; never execute repository code."""
import hashlib,json,re,os
from urllib.parse import urlsplit,quote
from .core import bundled,subdir

REPO_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')

def parse_repo(value):
    value=value.strip().rstrip('/')
    if '://' in value:
        u=urlsplit(value)
        if u.scheme!='https' or u.netloc!='huggingface.co' or u.query or u.fragment:raise ValueError('Use a public huggingface.co model URL or owner/model.')
        value=u.path.strip('/')
    if not REPO_RE.fullmatch(value) or '..' in value:raise ValueError('Use a Hugging Face owner/model repository ID.')
    return value

def make_entry(repo, info, config):
    repo=parse_repo(repo)
    rev=info.get('sha','')
    if not re.fullmatch('[0-9a-f]{40}',rev):raise ValueError('Missing immutable repository revision.')
    if info.get('private') or info.get('gated'):raise ValueError('Only public, ungated models are supported.')
    if config.get('model_type')!='vits' or config.get('auto_map') or config.get('architectures',[]) not in ([],['VitsModel']):raise ValueError('No supported adapter for this architecture. Currently custom imports support standard Transformers VITS/MMS TTS only.')
    names=[x.get('rfilename','') for x in info.get('siblings',[])]
    if not any(x.endswith('.safetensors') for x in names):raise ValueError('This importer requires safetensors weights; pickle checkpoints are not accepted.')
    license=(info.get('cardData') or {}).get('license','unknown')
    return {'id':'hf_vits_'+hashlib.sha256(repo.encode()).hexdigest()[:16], 'name':repo, 'kind':'tts','package':'transformers==4.55.4','module':'transformers','profile':'hf_vits','torch':True,'device':'CPU / CUDA','supports':[], 'voices':[{'id':'default','name':'Default speaker','language':'Model language'}], 'repo':'https://huggingface.co/'+repo,'hf_repo':repo,'hf_revision':rev,'snapshot':True,'description':'Imported VITS voice · synthesis test required','license_note':'Repository license: '+str(license)+'; review original terms.'}

def import_repo(value):
    from .managed import fetch_json
    repo=parse_repo(value)
    for m in json.loads(bundled('resources/catalog.json').read_text())['models']:
        if m.get('hf_repo','').lower()==repo.lower():return m,False
    for m in read_entries():
        if m['hf_repo'].lower()==repo.lower():return m,False
    info=fetch_json('https://huggingface.co/api/models/'+repo+'?blobs=true')
    rev=info.get('sha','')
    if not re.fullmatch('[0-9a-f]{40}',rev):raise ValueError('Invalid revision.')
    config=fetch_json('https://huggingface.co/'+repo+'/resolve/'+rev+'/config.json')
    entry=make_entry(repo,info,config)
    p=subdir('state')/'imported-models.json';entries=read_entries();entries=[m for m in entries if m['id']!=entry['id']]+[entry];tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(entries,indent=2),encoding='utf-8');os.replace(tmp,p)
    return entry,True

def read_entries():
    p=subdir('state')/'imported-models.json'
    try: data=json.loads(p.read_text(encoding='utf-8'))
    except (OSError,ValueError):return []
    return [m for m in data if isinstance(m,dict) and str(m.get('id','')).startswith('hf_vits_')] if isinstance(data,list) else []
