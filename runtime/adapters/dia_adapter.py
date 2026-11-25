"""Dia via official Transformers integration; only the newly generated waveform is saved."""
def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch,torchaudio,soundfile as sf
    from transformers import AutoProcessor,DiaForConditionalGeneration
    opts=task['options'];device='cuda' if opts.get('device')=='cuda' and torch.cuda.is_available() else 'cpu'
    proc,model=warm_engine((task['models_dir'],device),lambda:(AutoProcessor.from_pretrained(task['models_dir'],local_files_only=True),DiaForConditionalGeneration.from_pretrained(task['models_dir'],local_files_only=True,torch_dtype=torch.float16 if device=='cuda' else torch.float32).to(device).eval()))
    ref=task['ref_text'].strip()
    if not ref:raise ValueError('Dia requires an exact reference transcript.')
    audio,sr=sf.read(task['reference'],dtype='float32',always_2d=True)
    rate=44100;audio=torchaudio.functional.resample(torch.from_numpy(audio.mean(axis=1)),sr,rate).numpy() if sr!=rate else audio.mean(axis=1)
    text=('[S1] '+ref if not ref.startswith('[S1]') else ref)+' '+task['text']
    inputs=proc(text=[text],audio=audio,padding=True,return_tensors='pt').to(device)
    prompt_len=proc.get_audio_prompt_len(inputs['decoder_attention_mask'])
    budget=2048  # runtime generation guard, not a validated maximum script length
    with torch.inference_mode():out=model.generate(**inputs,max_new_tokens=budget,do_sample=True,temperature=float(opts.get('temperature',1.0)),guidance_scale=float(opts.get('guidance',3.0)))
    if out.shape[1]-inputs['decoder_input_ids'].shape[1]>=budget:raise RuntimeError('Dia reached the generation budget; use shorter paragraph or sentence chunks.')
    waveform=proc.batch_decode(out,audio_prompt_len=prompt_len)[0]
    sf.write(task['output'],waveform,rate,subtype='PCM_16')
