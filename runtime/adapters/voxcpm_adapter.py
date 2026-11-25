from pathlib import Path

def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch
    import soundfile as sf
    from voxcpm import VoxCPM
    if not task['reference'] or not task['ref_text'].strip(): raise ValueError('Reference audio and matching transcript required.')
    device='cuda' if task['options'].get('device','cuda')=='cuda' and torch.cuda.is_available() else 'cpu'
    engine=warm_engine((task['models_dir'],device),lambda:VoxCPM(voxcpm_model_path=task['models_dir'],enable_denoiser=False,optimize=False,device=device))
    wav=engine.generate(text=task['text'],prompt_wav_path=task['reference'],prompt_text=task['ref_text'],denoise=False)
    dest=Path(task['output']); dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix('.rendering.wav'); sf.write(str(temp),wav,engine.tts_model.sample_rate,subtype='PCM_16'); temp.replace(dest)
