from pathlib import Path

def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch,soundfile as sf
    from transformers import VitsModel,AutoTokenizer
    opts=task.get('options',{});device='cuda' if opts.get('device')=='cuda' and torch.cuda.is_available() else 'cpu'
    path=task['models_dir']
    tokenizer,model=warm_engine((path,device),lambda:(AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False),VitsModel.from_pretrained(path,local_files_only=True,use_safetensors=True).to(device).eval()))
    inp=tokenizer(task['text'],return_tensors='pt');inp={k:v.to(device) for k,v in inp.items()}
    with torch.inference_mode():wav=model(**inp).waveform[0].detach().cpu().float().numpy()
    out=Path(task['output']);out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix('.rendering.wav');sf.write(str(tmp),wav,model.config.sampling_rate,subtype='PCM_16');tmp.replace(out)
