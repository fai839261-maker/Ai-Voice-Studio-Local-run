"""Piper: published OHF-Voice synthesize_wav API."""
from pathlib import Path
import wave


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    from piper import PiperVoice
    model = Path(task["models_dir"]) / (task["voice"] + ".onnx")
    if not model.exists() or not model.with_suffix(".onnx.json").exists():
        raise FileNotFoundError("Download the ONNX voice and configuration from AI Setup first")
    engine = warm_engine(str(model),lambda:PiperVoice.load(str(model), use_cuda=False))
    out = Path(task["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_suffix(".rendering.wav")
    with wave.open(str(temp), "wb") as f:
        engine.synthesize_wav(task["text"],f)
    temp.replace(out)
