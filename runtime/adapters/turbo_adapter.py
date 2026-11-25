"""Chatterbox Turbo (350M, English): the reference voice is analysed once and reused."""
import time
from pathlib import Path


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    from adapters.timing import emit
    import torch
    import soundfile as sf
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    ref=Path(task.get("reference") or "")
    if not ref.is_file():
        raise ValueError("Reference audio required.")
    if sf.info(str(ref)).duration<=5.0:
        raise ValueError("Chatterbox Turbo needs a reference clip longer than 5 seconds.")
    opts=task.get("options",{})
    from adapters.gpu_choice import pick_device
    device=pick_device(torch,opts,"cuda")
    loaded={"seconds":0.0}
    def make():
        started=time.perf_counter()
        model=ChatterboxTurboTTS.from_local(Path(task["models_dir"]),device=device)
        loaded["seconds"]=time.perf_counter()-started
        return model
    engine=warm_engine((task["models_dir"],device),make)
    key=(str(ref),ref.stat().st_mtime_ns)
    fresh=getattr(engine,"conds",None) is None or getattr(engine,"_avs_ref",None)!=key
    kwargs={"audio_prompt_path":str(ref)} if fresh else {}
    started=time.perf_counter()
    wav=engine.generate(task["text"],**kwargs)
    seconds=time.perf_counter()-started
    engine._avs_ref=key
    try:
        used=str(next(engine.t3.parameters()).device)
    except Exception:
        used=device
    dest=Path(task["output"])
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix(".rendering.wav")
    sf.write(str(temp),wav.squeeze().detach().cpu().numpy(),engine.sr,subtype="PCM_16")
    temp.replace(dest)
    emit(task,device=used,cuda_available=bool(torch.cuda.is_available()),load_s=round(loaded["seconds"],2),gen_s=round(seconds,2),
         audio_s=round(float(wav.shape[-1])/engine.sr,2),reference_cached=not fresh,precision="full")
