"""Display and resolve the same trusted catalog entries used by the installer."""
from urllib.parse import urlsplit
from .core import models


def extra_packages(model, reduced=False):
    if model.startswith('hf_vits_'):return ['soundfile','uroman']
    if model=='dia':return ['soundfile','librosa']
    if model in ('xtts2','styletts2'):return ['soundfile']
    if model=='wordcheck':return ['requests']      # faster-whisper imports it but does not list it
    if model=='kokoro':return ['soundfile','misaki-fork[en,zh]==0.9.6' if reduced else 'misaki-fork[en,ja,zh]==0.9.6','espeakng-loader']
    return []


def adapter_name(model):
    if model.startswith('hf_vits_'):return 'vits_adapter.py'
    if model.startswith('qwen'):return 'qwen_adapter.py'
    if model=='indextts2':return 'indextts_adapter.py'
    return model+'_adapter.py'


def source_details(model):
    m=models()[model]
    links=[]
    def add(label,url):
        p=urlsplit(url)
        if p.scheme=='https' and p.hostname and not p.username and not p.password and url not in [v['url'] for v in links]:
            links.append(dict(label=label,url=url))
    add('Project repository',m['repo'])
    if m.get('hf_repo'):add('Model weights','https://huggingface.co/'+m['hf_repo'])
    for voice in m.get('voices',[]):
        if voice.get('repo'):add('Voice weights','https://huggingface.co/'+voice['repo'])
    for f in m.get('files',[])+m.get('extra_files',[]):
        add('File: '+f['name'],f['url'])
    package=m['package']
    if package.startswith('git+'):add('Pinned package source',package[4:].split('@',1)[0])
    else:add('Engine package','https://pypi.org/project/'+package.split('==',1)[0]+'/')
    for dep in extra_packages(model):add('Dependency: '+dep,'https://pypi.org/project/'+dep+'/')
    requirements=[package,*extra_packages(model)]
    if m['torch']:
        from .managed import torch_version
        v=torch_version(m);requirements.extend(['torch=='+v,'torchaudio=='+v]);add('PyTorch wheels','https://download.pytorch.org/whl/')
    add('Python dependency index','https://pypi.org/')
    return dict(links=links,requirements=requirements,profile=m['profile'],adapter='runtime/adapters/'+adapter_name(model),license=m['license_note'],revision=m.get('hf_revision') or ('Resolved to a commit during download' if m.get('snapshot') else 'Catalog file URLs'),auxiliary=model in ('indextts2','dia'))
