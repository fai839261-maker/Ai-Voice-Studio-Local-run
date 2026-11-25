"""IndexTTS2 direct upstream API; optional CUDA kernels deliberately disabled."""
from pathlib import Path

def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch
    from indextts.infer_v2 import IndexTTS2
    folder=Path(task['models_dir']); opts=task['options']
    if not Path(task['reference']).is_file():raise ValueError('Choose a saved reference WAV.')
    cuda=opts.get('device')=='cuda' and torch.cuda.is_available()
    emo=opts.get("emotion_text", "").strip()
    engine=warm_engine((str(folder),cuda,bool(emo)),lambda:IndexTTS2(cfg_path=str(folder/'config.yaml'),model_dir=str(folder),device='cuda:0' if cuda else 'cpu',use_fp16=cuda,use_cuda_kernel=False,use_deepspeed=False,use_qwen_emo=bool(emo)))
    emo=opts.get('emotion_text','').strip()
    engine.infer(spk_audio_prompt=task['reference'],text=task['text'],output_path=task['output'],use_emo_text=bool(emo),emo_text=emo or None,emo_alpha=float(opts.get('emotion_weight',1)),temperature=float(opts.get('temperature',.8)))
