"""Turn an audio file into text so the software can compare it with the script (the word check).
Runs inside the small 'word checker' environment (faster-whisper). Usage: python whisper_check.py MODEL_DIR AUDIO OUT_JSON [THREADS]"""
import json
import sys


def main() -> int:
    model_dir, audio, out = sys.argv[1], sys.argv[2], sys.argv[3]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    from faster_whisper import WhisperModel
    model = WhisperModel(model_dir, device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True)
    segments, info = model.transcribe(audio, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False,
                                      word_timestamps=False, temperature=0.0)
    parts = []
    for seg in segments:
        parts.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()})
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"text": " ".join(p["text"] for p in parts), "segments": parts, "duration": round(getattr(info, "duration", 0.0), 2)}, f)
    print("Completed word check transcription", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
