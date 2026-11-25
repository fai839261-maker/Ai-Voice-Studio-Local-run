"""F5-TTS integration: requires properly installed local vocoder cache.
The engine deliberately runs with HF_HUB_OFFLINE=1 so it cannot surprise-download weights.
"""
from pathlib import Path


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import soundfile as sf
    from f5_tts.api import F5TTS
    ref=Path(task.get("reference") or "")
    if not ref.is_file():
        raise ValueError("F5-TTS needs a reference audio WAV")
    checkpoint=Path(task["models_dir"])/"model_1250000.safetensors"
    if not checkpoint.exists():
        raise FileNotFoundError("F5-TTS checkpoint missing; download it from AI Setup first")
    vocoder=Path(task["models_dir"])/"vocos"
    if not (vocoder/"config.yaml").is_file() or not (vocoder/"pytorch_model.bin").is_file():
        raise FileNotFoundError("The local Vocos vocoder files are required; use AI Setup to download them")
    if not task.get("ref_text", "").strip():
        raise ValueError("F5-TTS needs a reference audio transcript to avoid an extra speech-recognition download")
    import torch
    from adapters.gpu_choice import pick_device
    device=pick_device(torch,task.get("options",{}))
    engine=warm_engine((str(checkpoint),device),lambda:F5TTS(model="F5TTS_v1_Base",ckpt_file=str(checkpoint),vocoder_local_path=str(vocoder),device=device))
    opts=task.get("options",{})
    audio,sr,_=engine.infer(ref_file=str(ref),ref_text=task.get("ref_text", ""),gen_text=task["text"],speed=float(opts.get("speed",1.0)))
    dest=Path(task["output"])
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=dest.with_suffix(".rendering.wav")
    sf.write(str(temp),audio,sr,subtype="PCM_16")
    temp.replace(dest)
