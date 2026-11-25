"""Kokoro: ONNX model + voices array; speed applies only here and F5."""
from pathlib import Path


def generate(task):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    folder = Path(task["models_dir"])
    from adapters.warm_cache import engine as warm_engine
    def load():
        engine = Kokoro(str(folder/"kokoro-v1.0.onnx"), str(folder/"voices-v1.0.bin"))
        from adapters.onnx_inputs import TypedSession
        if task.get('options',{}).get('cpu_threads'):
            import onnxruntime as ort
            config=ort.SessionOptions();config.intra_op_num_threads=int(task['options']['cpu_threads']);config.inter_op_num_threads=1
            engine.sess=ort.InferenceSession(str(folder/'kokoro-v1.0.onnx'),sess_options=config,providers=['CPUExecutionProvider'])
        engine.sess = TypedSession(engine.sess)
        return engine
    engine=warm_engine((str(folder),task.get("options",{}).get("cpu_threads")),load)
    voice=task["voice"]
    from adapters.language import phonemes
    ps=phonemes(task["text"],voice)
    samples, sr=engine.create(ps,voice=voice,speed=float(task.get("options",{}).get("speed",1.0)),is_phonemes=True)
    out = Path(task["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    temp=out.with_suffix(".rendering.wav")
    sf.write(str(temp),samples,sr,subtype="PCM_16")
    temp.replace(out)
