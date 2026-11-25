from pathlib import Path

def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    opts=task['options']
    from adapters.language import qwen_language
    language=qwen_language(opts.get('language'))
    from adapters.gpu_choice import pick_device,gpu_dtype
    dev=pick_device(torch,opts);cuda=dev=='cuda'
    engine=warm_engine((task['models_dir'],dev,opts.get('gpu_precision','')),lambda:Qwen3TTSModel.from_pretrained(task['models_dir'],device_map={'cuda':'cuda:0','mps':'mps','cpu':'cpu'}[dev],dtype=gpu_dtype(torch,opts) if cuda else torch.float32,attn_implementation='sdpa',local_files_only=True))
    chars=len(task['text']);limit={'max_new_tokens':int(chars/15*12.5*2.2)+64}      # 12.5 audio frames a second; about 15 letters spoken a second; twice the expected length is already generous
    def attempt(call,**kw):
        try:return call(**kw,**limit)
        except TypeError:return call(**kw)
    if task['model']=='qwen17bdesign':
        description=str(opts.get('voice_description','')).strip()
        if not description:raise ValueError('Describe the voice you want first.')
        feeling=str(opts.get('emotion_text','')).strip()
        wavs,sr=attempt(engine.generate_voice_design,text=task['text'],language=language,instruct=(description+' '+feeling).strip())
    elif task['model'].endswith('clone'):
        if not task['reference'] or not task['ref_text'].strip(): raise ValueError('Reference audio and matching transcript required.')
        if hasattr(engine,'create_voice_clone_prompt'):
            from adapters.warm_cache import prompt,reference_key
            key=reference_key(task['reference'],task['ref_text'])
            prepared=prompt(key,lambda:engine.create_voice_clone_prompt(ref_audio=task['reference'],ref_text=task['ref_text']))
            wavs,sr=attempt(engine.generate_voice_clone,text=task['text'],language=language,voice_clone_prompt=prepared)
        else:
            wavs,sr=attempt(engine.generate_voice_clone,text=task['text'],language=language,ref_audio=task['reference'],ref_text=task['ref_text'])
    else:
        speaker=task['voice']
        if hasattr(engine,'get_supported_speakers'):
            supported=engine.get_supported_speakers() or []
            if supported:
                speaker=next((x for x in supported if x.lower()==speaker.lower()),None)
                if speaker is None:raise ValueError('Speaker not present in this checkpoint. Refresh voices in AI Setup.')
        kwargs={'instruct':opts.get('emotion_text','')} if task['model']=='qwen17bvoice' and opts.get('emotion_text') else {}
        wavs,sr=attempt(engine.generate_custom_voice,text=task['text'],language=language,speaker=speaker,**kwargs)
    dest=Path(task['output']); dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix('.rendering.wav'); sf.write(str(temp),wavs[0],sr,subtype='PCM_16'); temp.replace(dest)
