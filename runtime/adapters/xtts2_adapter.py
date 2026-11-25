"""XTTS-v2 (Coqui TTS) cloning from a short reference clip. Local model files only."""
from pathlib import Path

LANG = {"English": "en", "Spanish": "es", "French": "fr", "German": "de", "Italian": "it", "Portuguese": "pt", "Polish": "pl",
        "Turkish": "tr", "Russian": "ru", "Dutch": "nl", "Czech": "cs", "Arabic": "ar", "Chinese": "zh-cn", "Japanese": "ja",
        "Hungarian": "hu", "Korean": "ko"}


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import torch
    import soundfile as sf
    from TTS.api import TTS
    ref = Path(task.get("reference") or "")
    if not ref.is_file():
        raise ValueError("XTTS-v2 needs a reference audio clip (clear speech, about 6 to 30 seconds)")
    folder = Path(task["models_dir"])
    for name in ("config.json", "model.pth", "vocab.json"):
        if not (folder / name).is_file():
            raise FileNotFoundError(f"Missing XTTS-v2 file: {name}. Download it from AI Setup first.")
    opts = task.get("options", {})
    from adapters.gpu_choice import pick_device
    device = pick_device(torch, opts)
    engine = warm_engine((str(folder), device), lambda: TTS(model_path=str(folder), config_path=str(folder / "config.json"), progress_bar=False).to(device))
    language = LANG.get(str(opts.get("language", "English")), "en")
    kwargs = dict(text=task["text"], speaker_wav=str(ref), language=language, split_sentences=True)
    if "temperature" in opts:
        kwargs["temperature"] = float(opts["temperature"])
    try:
        audio = engine.tts(**kwargs)
    except TypeError:
        kwargs.pop("temperature", None)
        audio = engine.tts(**kwargs)
    rate = int(getattr(getattr(engine, "synthesizer", None), "output_sample_rate", 24000) or 24000)
    dest = Path(task["output"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(".rendering.wav")
    sf.write(str(temp), audio, rate, subtype="PCM_16")
    temp.replace(dest)
