"""StyleTTS 2 (pip package) with a reference clip. Reads only the downloaded local checkpoint."""
import inspect
from pathlib import Path


def generate(task):
    from adapters.warm_cache import engine as warm_engine
    import soundfile as sf
    from styletts2 import tts as styletts
    ref = Path(task.get("reference") or "")
    folder = Path(task["models_dir"])
    config = folder / "Models" / "LibriTTS" / "config.yml"
    checkpoint = folder / "Models" / "LibriTTS" / "epochs_2nd_00020.pth"
    for path in (config, checkpoint):
        if not path.is_file():
            raise FileNotFoundError(f"Missing StyleTTS 2 file: {path.name}. Download it from AI Setup first.")
    engine = warm_engine((str(folder),), lambda: styletts.StyleTTS2(model_checkpoint_path=str(checkpoint), config_path=str(config)))
    accepted = set(inspect.signature(engine.inference).parameters)
    opts = task.get("options", {})
    kwargs = {}
    for option, argument, cast in (("style_alpha", "alpha", float), ("style_beta", "beta", float),
                                   ("diffusion_steps", "diffusion_steps", int), ("embedding_scale", "embedding_scale", float)):
        if option in opts and argument in accepted:
            kwargs[argument] = cast(opts[option])
    if ref.is_file() and "target_voice_path" in accepted:
        kwargs["target_voice_path"] = str(ref)
    audio = engine.inference(task["text"], **kwargs)
    dest = Path(task["output"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(".rendering.wav")
    sf.write(str(temp), audio, 24000, subtype="PCM_16")
    temp.replace(dest)
