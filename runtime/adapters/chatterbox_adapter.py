"""Chatterbox original English, explicitly local files only; reference required.

Speed: the reference voice is analysed once and reused for every following part (the library would
otherwise redo the analysis for each part). Optional half precision on NVIDIA cards.
"""
import time
from pathlib import Path


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    from adapters.timing import emit
    import torch
    import soundfile as sf
    from chatterbox.tts import ChatterboxTTS
    ref=Path(task.get("reference") or "")
    if not ref.is_file():
        raise ValueError("Choose a voice-cloning reference WAV with permission to use it")
    folder=Path(task["models_dir"])
    for name in ("ve.safetensors","t3_cfg.safetensors","s3gen.safetensors","tokenizer.json","conds.pt"):
        if not (folder/name).is_file():
            raise FileNotFoundError(f"Missing Chatterbox model file: {name}")
    opts=task.get("options",{})
    from adapters.gpu_choice import pick_device
    device=pick_device(torch,opts)
    loaded={"seconds":0.0}
    def make():
        started=time.perf_counter()
        model=ChatterboxTTS.from_local(folder,device=device)
        loaded["seconds"]=time.perf_counter()-started
        return model
    engine=warm_engine((str(folder),device),make)
    key=(str(ref),ref.stat().st_mtime_ns)
    fresh=getattr(engine,"conds",None) is None or getattr(engine,"_avs_ref",None)!=key
    kwargs=dict(exaggeration=float(opts.get("exaggeration",0.5)),cfg_weight=float(opts.get("cfg_weight",0.5)))
    if fresh:
        kwargs["audio_prompt_path"]=str(ref)
    precision="full"
    started=time.perf_counter()
    wav=None
    from adapters.gpu_choice import slow_half,gpu_dtype
    if opts.get("fast_precision") and device=="cuda" and not slow_half(torch):
        dtype=gpu_dtype(torch)
        try:
            with torch.autocast("cuda",dtype=dtype):
                wav=engine.generate(task["text"],**kwargs)
            precision=str(dtype).replace("torch.","")
        except Exception as exc:
            print("AVS_NOTE half precision failed, used full precision: "+str(exc)[:200],flush=True)
            wav=None
    if wav is None:
        wav=engine.generate(task["text"],**kwargs)
    seconds=time.perf_counter()-started
    engine._avs_ref=key
    try:
        used=str(next(engine.t3.parameters()).device)
    except Exception:
        used=device
    out=Path(task["output"])
    out.parent.mkdir(parents=True,exist_ok=True)
    temp=out.with_suffix(".rendering.wav")
    sf.write(str(temp),wav.squeeze().detach().cpu().numpy(),engine.sr,subtype="PCM_16")
    temp.replace(out)
    emit(task,device=used,cuda_available=bool(torch.cuda.is_available()),load_s=round(loaded["seconds"],2),gen_s=round(seconds,2),
         audio_s=round(float(wav.shape[-1])/engine.sr,2),reference_cached=not fresh,precision=precision)
