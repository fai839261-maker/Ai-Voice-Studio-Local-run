"""Record a voice sample from the microphone straight into a WAV file (no extra tools needed)."""
from __future__ import annotations
import array
import wave
from pathlib import Path

MAX_SECONDS = 30
RATES = (24000, 48000, 44100, 16000)


class Recorder:
    """Writes 16-bit mono PCM. Qt feeds it through feed(); tests can call feed() directly."""

    def __init__(self):
        self.rate = 24000
        self.path: Path | None = None
        self._wav = None
        self._frames = 0
        self.level = 0.0
        self._source = None
        self._io = None

    # ---- file writing (independent of Qt, so it can be tested)
    def open(self, path: Path, rate: int = 24000) -> None:
        self.path, self.rate, self._frames, self.level = Path(path), rate, 0, 0.0
        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(rate)

    def feed(self, data: bytes) -> None:
        if not self._wav or not data:
            return
        data = data[: len(data) // 2 * 2]
        self._wav.writeframes(data)
        self._frames += len(data) // 2
        samples = array.array("h")
        samples.frombytes(data)
        if samples:
            self.level = max(max(samples), -min(samples)) / 32768

    @property
    def seconds(self) -> float:
        return self._frames / max(1, self.rate)

    def close(self) -> Path | None:
        if self._wav:
            self._wav.close()
            self._wav = None
        return self.path

    # ---- microphone (Qt)
    def start(self, path: Path) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise RuntimeError("No microphone was found. Plug one in, or allow microphone access in Windows Settings > Privacy > Microphone.")
        chosen = None
        for rate in RATES:
            fmt = QAudioFormat()
            fmt.setSampleRate(rate)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isFormatSupported(fmt):
                chosen = fmt
                break
        if chosen is None:
            raise RuntimeError("This microphone does not offer a format the software can record. Record with another app and use Select file.")
        self.open(path, chosen.sampleRate())
        self._source = QAudioSource(device, chosen)
        self._io = self._source.start()
        if self._io is None:
            self.close()
            raise RuntimeError("The microphone could not be opened. Check that no other app is using it.")
        self._io.readyRead.connect(self._pull)

    def _pull(self) -> None:
        if self._io is not None:
            self.feed(bytes(self._io.readAll()))

    def stop(self) -> Path | None:
        if self._source is not None:
            self._pull()
            self._source.stop()
            self._source = None
            self._io = None
        return self.close()
