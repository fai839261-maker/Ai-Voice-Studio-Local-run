"""OpenF5 uses its published DiT architecture and own tokenizer, not official F5 weights."""
from pathlib import Path

def generate(task):
    import torch
    import soundfile as sf
    from f5_tts.api import F5TTS
    from f5_tts.infer.utils_infer import load_model,load_vocoder
    from f5_tts.model import DiT
    from omegaconf import OmegaConf
    folder=Path(task['models_dir']);ref=Path(task['reference'])
    if not ref.is_file() or not task['ref_text'].strip():raise ValueError('Reference audio and matching transcript required.')
    # Use only the exact architecture data, not a runnable downloaded Python file.
    cfg=OmegaConf.load(folder/'config.yaml').model
    if cfg.backbone!='DiT' or cfg.mel_spec.mel_spec_type!='vocos':raise ValueError('Unsupported OpenF5 architecture or vocoder.')
    device='cuda' if task['options'].get('device','cuda')=='cuda' and torch.cuda.is_available() else 'cpu'
    from adapters.warm_cache import engine as warm_engine
    def load():
        engine=F5TTS.__new__(F5TTS)
        engine.device=device;engine.mel_spec_type='vocos';engine.target_sample_rate=24000;engine.ode_method='euler';engine.use_ema=True
        engine.vocoder=load_vocoder('vocos',True,str(folder/'vocos'),device,None)
        engine.ema_model=load_model(DiT,OmegaConf.to_container(cfg.arch,resolve=True),str(folder/'model.pt'),'vocos',str(folder/'vocab.txt'),'euler',True,device)
        return engine
    engine=warm_engine((str(folder),device),load)
    audio,sr,_=engine.infer(ref_file=str(ref),ref_text=task['ref_text'],gen_text=task['text'],speed=float(task['options'].get('speed',1.0)))
    dest=Path(task['output']);dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.rendering.wav');sf.write(str(tmp),audio,sr,subtype='PCM_16');tmp.replace(dest)
