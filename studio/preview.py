"""Short voice samples: listen to any ready voice (built-in or cloned) before using it."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .core import subdir, is_audio, models, bundled

SAMPLES = {
    'english': "Hello! This is a short sample of my voice. How does it sound to you?",
    'spanish': "¡Hola! Esta es una breve muestra de mi voz. ¿Cómo suena?",
    'french': "Bonjour ! Voici un court échantillon de ma voix. Comment la trouvez-vous ?",
    'italian': "Ciao! Questo è un breve campione della mia voce. Come ti sembra?",
    'portuguese': "Olá! Esta é uma breve amostra da minha voz. O que achou?",
    'german': "Hallo! Das ist eine kurze Hörprobe meiner Stimme. Wie klingt sie für dich?",
    'russian': "Здравствуйте! Это короткий образец моего голоса. Как вам звучание?",
    'hindi': "नमस्ते! यह मेरी आवाज़ का एक छोटा सा नमूना है। आपको कैसा लगा?",
    'japanese': "こんにちは。これは私の声の短いサンプルです。いかがですか。",
    'chinese': "你好！这是我声音的一个简短示例。听起来怎么样？",
    'mandarin': "你好！这是我声音的一个简短示例。听起来怎么样？",
    'korean': "안녕하세요! 이것은 제 목소리의 짧은 샘플입니다. 어떻게 들리나요?",
}


def sample_text(language: str = "") -> str:
    low = (language or "").lower()
    for key, text in SAMPLES.items():
        if key in low:
            return text
    return SAMPLES['english']


def cache_file(model: str, voice: str, text: str, reference: str = "", extra: dict | None = None) -> Path:
    stamp = ""
    if reference:
        try:
            stamp = str(Path(reference).stat().st_mtime_ns)
        except OSError:
            stamp = "missing"
    key = json.dumps([model, voice, text, reference, stamp, extra or {}], sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return subdir("previews") / f"{digest}.wav"


def warm_up(store, model: str, voice: str, reference: str = "", ref_text: str = "", options: dict | None = None,
            device: str = "cpu", log=lambda m: None) -> None:
    """Load the voice engine (and analyse the reference voice) before the person presses Generate.

    A tiny sentence is made with the same settings a real job uses, so the loaded engine is reused."""
    path = make_preview(store, model, voice, reference, ref_text, options, text="Ready.", device=device, log=log, force=True)
    path.unlink(missing_ok=True)


def clear_cache() -> int:
    count = 0
    for f in subdir("previews").glob("*.wav"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


def make_preview(store, model: str, voice: str, reference: str = "", ref_text: str = "", options: dict | None = None,
                 text: str = "", language: str = "", device: str = "cpu", log=lambda m: None, force: bool = False) -> Path:
    """Return a WAV sample for this voice, generating it only the first time.

    Raises RuntimeError with a plain-language message when the engine is not ready.
    """
    from . import managed, services
    entry = models()[model]
    text = (text or sample_text(language)).strip()
    options = dict(options or {})
    for noisy in ("segment_mode", "auto_chunk", "pause_ms", "normalize", "trim_silence", "fade_ms", "chunk_chars"):
        options.pop(noisy, None)
    shaping = {k: options.get(k) for k in ("output_speed", "pitch_semitones", "temperature", "guidance",
                                           "exaggeration", "cfg_weight", "emotion_text", "emotion_weight", "language",
                                           "voice_description", "style_alpha", "style_beta", "diffusion_steps", "embedding_scale")}
    target = cache_file(model, voice, text, reference, shaping)
    if is_audio(target) and not force:
        return target
    if not managed.engine_python(model).is_file():
        raise RuntimeError("This voice engine is not set up yet. Open AI Setup and press Download.")
    if not managed.downloaded(model, voice):
        raise RuntimeError("This voice is not downloaded yet. Open AI Setup to download it.")
    from .generation_policy import effective_device
    device = effective_device(entry, device)
    lock = managed.generation_lock(model)
    if not lock.acquire(timeout=3):
        raise RuntimeError("This engine is being installed or repaired right now. Try again in a moment.")
    try:
        if not managed.generation_runtime_ok(model, device, log):
            raise RuntimeError("This voice engine needs repair. Open AI Setup and press Download or Repair.")
        from .resource_policy import limits, thread_budget
        policy = limits(store)
        run_options = {**options, "device": device, "keep_model_warm": bool(store.setting("keep_model_warm", True)),
                       "cpu_threads": thread_budget(policy["cpu_limit"]), "gpu_memory_fraction": policy["gpu_limit"] / 100}
        speed = float(run_options.pop("output_speed", 1) or 1)
        pitch = float(run_options.pop("pitch_semitones", 0) or 0)
        services.invoke_engine(model, voice, text, target, run_options, reference, ref_text, log)
    finally:
        lock.release()
    if speed != 1 or pitch != 0:
        from .audio_fx import apply_voice_fx
        apply_voice_fx(target, speed, pitch)
    return target
