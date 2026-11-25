"""Saved reference-based clone profiles. Audio is copied to private app storage."""
from pathlib import Path
import shutil,uuid,math,os
from .core import subdir,models,is_audio

# Two plain lines used as the "words spoken" when nobody typed them, so a voice is never refused and nobody is asked for text.
DEFAULT_TRANSCRIPT=("Hello, this is my natural voice speaking clearly and calmly. "
                    "I am recording a short sample so that every voice model can learn how I sound.")

def profiles(store):return store.setting('clone_profiles',[])

def _taken_names(store):
    names={p['name'].strip().lower() for p in profiles(store)}
    try:
        for m in models().values():
            for v in m.get('voices') or []:
                if isinstance(v,dict) and v.get('name'):names.add(str(v['name']).strip().lower())
    except Exception:pass
    return names

def new_name(store,prefix='My Voice'):
    """A name that no voice in the software uses yet: My Voice 001, My Voice 002 ..."""
    taken=_taken_names(store);n=1
    while f'{prefix} {n:03d}'.lower() in taken:n+=1
    return f'{prefix} {n:03d}'

def unique_name(store,name,existing=None):
    """The wanted name, or the same name with a number when another voice already has it."""
    name=' '.join(str(name).split()) or new_name(store)
    others={p['name'].strip().lower() for p in profiles(store) if p['id']!=existing}
    if name.lower() not in others:return name
    n=2
    while f'{name} ({n})'.lower() in others:n+=1
    return f'{name} ({n})'

def rename_profile(store,ident,name):
    name=' '.join(str(name).split())
    if not name:raise ValueError('Type a name for the voice.')
    items=profiles(store)
    if not any(p['id']==ident for p in items):raise ValueError('That voice no longer exists.')
    name=unique_name(store,name,existing=ident)
    store.set_setting('clone_profiles',[{**p,'name':name} if p['id']==ident else p for p in items]);return name

def transcript_of(profile):
    """The words spoken for a voice: what was typed, otherwise the two default lines."""
    return (profile.get('transcript') or '').strip() or DEFAULT_TRANSCRIPT
def save_profile(store,name,model,reference,transcript,consent,options=None,existing=None,style_notes=""):
    reference=Path(reference)
    if not consent:raise ValueError('Speaker permission is required.')
    name=unique_name(store,name,existing=existing) if (name or '').strip() else new_name(store)
    if models().get(model,{}).get('kind')!='clone':raise ValueError('Select a cloning engine.')
    if not is_audio(reference):raise ValueError('Choose a valid PCM WAV reference.')
    transcript=' '.join(str(transcript or '').split()) or DEFAULT_TRANSCRIPT       # never ask: two default lines are used
    items=profiles(store);old=next((p for p in items if p['id']==existing),None)
    ident=existing or uuid.uuid4().hex;dest=subdir('clones')/(ident+'.wav')
    # New reference file gets a new ID, keeping old job references intact.
    if old and reference.resolve()!=dest.resolve():ident=uuid.uuid4().hex;dest=subdir('clones')/(ident+'.wav')
    if reference.resolve()!=dest.resolve():
        temp=dest.with_suffix(".copying.wav")
        try:shutil.copy2(reference,temp);os.replace(temp,dest)
        finally:temp.unlink(missing_ok=True)
    from .reference_audio import describe
    analysis=describe(dest)
    rec=dict(style_notes=str(style_notes),reference_analysis=analysis,id=ident,name=name,model=model,reference=str(dest),transcript=transcript,options=options or {},consent=True)
    store.set_setting('clone_profiles',[p for p in items if p['id']!=existing]+[rec]);return rec

def set_transcript(store,ident,text):
    """Save the words spoken in a sample once; every cloning model then uses them."""
    text=" ".join(str(text).split())
    if not text:raise ValueError("Type the words spoken in the sample.")
    items=profiles(store)
    if not any(p["id"]==ident for p in items):raise ValueError("That voice no longer exists.")
    store.set_setting("clone_profiles",[{**p,"transcript":text} if p["id"]==ident else p for p in items]);return text

def remove_profile(store,ident):
    # Existing queued jobs may refer to this audio. Retain the underlying WAV.
    store.set_setting('clone_profiles',[p for p in profiles(store) if p['id']!=ident])
