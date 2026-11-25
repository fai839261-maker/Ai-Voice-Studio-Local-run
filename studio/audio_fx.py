"""Independent speed/pitch post-processing via bundled FFmpeg; never fake native controls."""
from . import external
import math,shutil,subprocess,wave,os
from pathlib import Path

def ffmpeg_path():
    found=shutil.which('ffmpeg')
    if found:return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError,RuntimeError):return None

def atempo_chain(factor):
    if not math.isfinite(factor) or factor<=0:raise ValueError('Invalid tempo')
    stages=[]
    while factor>2:stages.append('atempo=2');factor/=2
    while factor<.5:stages.append('atempo=0.5');factor*=2
    stages.append(f'atempo={factor:.8f}');return stages

def apply_voice_fx(path,speed=1.,pitch=0.):
    if speed==1 and pitch==0:return
    if not .5<=speed<=2 or not -12<=pitch<=12:raise ValueError('Speed or pitch outside supported range.')
    ffmpeg=ffmpeg_path()
    if not ffmpeg:raise RuntimeError('Bundled FFmpeg unavailable.')
    with wave.open(str(path),'rb') as f:rate=f.getframerate()
    ratio=2**(pitch/12);shifted=max(1,round(rate*ratio));actual=shifted/rate
    filters=[f'asetrate={shifted}',f'aresample={rate}',*atempo_chain(speed/actual)]
    tmp=path.with_suffix('.fx.wav')
    try:
        r=external.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(path),'-af',','.join(filters),'-c:a','pcm_s16le',str(tmp)],capture_output=True,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if r.returncode:raise RuntimeError('Audio adjustment failed: '+r.stderr.decode(errors='replace'))
        from .core import is_audio
        if not is_audio(tmp):raise RuntimeError('Audio adjustment did not produce valid WAV')
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)


def _run_filter(path, filters):
    """Run one FFmpeg audio filter chain over a WAV file, replacing it only on success."""
    ffmpeg=ffmpeg_path()
    if not ffmpeg:raise RuntimeError('Bundled FFmpeg unavailable.')
    tmp=path.with_suffix('.pol.wav')
    try:
        r=external.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(path),'-af',','.join(filters),'-c:a','pcm_s16le',str(tmp)],capture_output=True,timeout=1800,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if r.returncode:raise RuntimeError('Audio polish failed: '+r.stderr.decode(errors='replace'))
        from .core import is_audio
        if not is_audio(tmp):raise RuntimeError('Audio polish did not produce valid WAV')
        tmp.replace(path)
    finally:tmp.unlink(missing_ok=True)

def wav_seconds(path):
    with wave.open(str(path),'rb') as f:return f.getnframes()/max(1,f.getframerate())

def apply_polish(path,normalize=False,trim_silence=False,fade_ms=0,volume_pct=100):
    """Optional finishing: even loudness, shorter long silences, soft fade in/out.
    Every step is optional; with nothing selected the file is not touched."""
    path=Path(path);fade_ms=max(0,min(2000,int(fade_ms or 0)))
    first=[]
    if trim_silence:first.append('silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:stop_periods=-1:stop_duration=0.9:stop_threshold=-50dB:stop_silence=0.4')
    if normalize:first.append('loudnorm=I=-16:TP=-1.5:LRA=11')
    if first:_run_filter(path,first)
    volume_pct=max(25,min(200,int(volume_pct or 100)))
    if volume_pct!=100:
        factor=volume_pct/100
        _run_filter(path,[f'volume={factor:.3f}']+(['alimiter=limit=0.97'] if factor>1 else []))
    if fade_ms:
        seconds=wav_seconds(path);d=min(fade_ms/1000,seconds/2)
        if d>0:_run_filter(path,[f'afade=t=in:d={d:.3f}',f'afade=t=out:st={max(0,seconds-d):.3f}:d={d:.3f}'])


def denoise(path):
    """Gentle clean-up for a voice sample: removes low rumble and steady background hiss."""
    _run_filter(Path(path),['highpass=f=80','afftdn=nf=-25'])
