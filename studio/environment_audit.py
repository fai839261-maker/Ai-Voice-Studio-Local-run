"""Read-only startup and installer audit of existing managed Python profiles."""
from __future__ import annotations
import json, time, os
from .core import models, subdir, bundled
from . import managed, external

def write_license_inventory():
    target=subdir('licenses')/'model-sources.json'
    from .model_sources import source_details
    items=[{k:m[k] for k in ('id','name','repo','license_note')} | {'hf_repo':m.get('hf_repo',''), 'setup':source_details(m['id'])} for m in models().values()]
    target.write_text(json.dumps({'note':'Model-specific terms apply. These are catalog notes, not full license texts or legal clearance.','models':items},ensure_ascii=False,indent=2),encoding='utf-8')
    return target

def audit_existing(log=lambda _:None, cancel=None):
    rows=[];profiles={}
    for mid,m in models().items():profiles.setdefault(m['profile'],[]).append((mid,m))
    for profile,entries in profiles.items():
        managed.check_cancel(cancel);mid,entry=entries[0];py=managed.engine_python(mid)
        if not py.is_file():continue
        row={'profile':profile,'models':[x[0] for x in entries],'issues':[],'python':str(py)}
        try:
            if not managed.probe(py):raise RuntimeError('Incompatible or broken Python environment')
            r=external.run([py,'-m','pip','check'],capture_output=True,text=True,errors='replace',timeout=90,env=managed.env(),priority='background')
            if r.returncode:row['issues'].append((str(r.stdout or '')+'\n'+str(r.stderr or '')).strip() or 'pip check failed')
            # Metadata checks avoid loading multiple GB of model libraries at startup.
            requirements={m['package'] for _,m in entries if '==' in m['package'] and not m['package'].startswith('git+') and (managed.profile_dir(m['id'])/(m['id']+'.installed.json')).is_file()}
            if entry['torch']:
                v=managed.torch_version(entry);requirements.update({f'torch=={v}',f'torchaudio=={v}'})
            for spec in sorted(requirements):
                name,wanted=spec.split('==',1)
                ok,detail=managed.probe_import(py,f'from importlib.metadata import version; assert version({name!r}).split("+")[0]=={wanted!r}, {spec!r}+" required, installed "+version({name!r})',timeout=20)
                if not ok:row['issues'].append(detail)
        except Exception as exc:row['issues'].append(str(exc))
        if row['issues']:
            for key,_ in entries:(managed.profile_dir(key)/(key+'.installed.json')).unlink(missing_ok=True)
            log('Compatibility check requires repair for '+profile+': '+'; '.join(row['issues']))
        rows.append(row)
    # A previous Kokoro marker predates multilingual setup. The engine repair
    # is deferred to Check or generation; no weights are downloaded by the audit.
    report={'checked_at':time.time(),'profiles':rows,'repair_required':sum(bool(r['issues']) for r in rows)}
    path=subdir('logs')/'environment-audit.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(report,indent=2),encoding='utf-8');os.replace(temp,path)
    write_license_inventory();return report
