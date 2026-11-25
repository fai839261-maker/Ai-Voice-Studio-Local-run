"""Non-destructive reference editing and export using the bundled FFmpeg."""
import math,os,shutil,tempfile,wave,struct
from pathlib import Path
from . import external
from .core import is_audio
from .audio_fx import ffmpeg_path

def ffmpeg(args):
    exe=ffmpeg_path()
    if not exe:raise RuntimeError('FFmpeg is unavailable. Repair AI Voice Studio.')
    r=external.run([exe,'-hide_banner','-loglevel','error','-nostdin','-y',*args],capture_output=True,timeout=900)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace')[-3000:])

def edit_reference(source,dest,start,end,gain=0):
    source=Path(source);dest=Path(dest)
    if not source.is_file() or source.resolve()==dest.resolve():raise ValueError('Choose an existing source and a different output.')
    if not all(math.isfinite(x) for x in (start,end,gain)) or start<0 or not .5<=end-start<=120 or not -24<=gain<=24:raise ValueError('Select a reference between 0.5 and 120 seconds, with valid gain.')
    tmp=dest.with_suffix('.editing.wav')
    try:
        ffmpeg(['-ss',str(start),'-i',str(source),'-t',str(end-start),'-vn','-ac','1','-ar','24000','-af',f'volume={gain}dB','-c:a','pcm_s16le',str(tmp)])
        if not is_audio(tmp):raise ValueError('Selected audio is empty or invalid.')
        with wave.open(str(tmp),'rb') as w:
            if w.getnframes()/w.getframerate()<.5:raise ValueError('Selected audio is shorter than 0.5 seconds.')
        os.replace(tmp,dest)
        return describe(dest)
    finally:tmp.unlink(missing_ok=True)

def describe(path):
    # Descriptive acoustic measurements, not an emotion classifier.
    with wave.open(str(path),'rb') as w:
        rate=w.getframerate();duration=w.getnframes()/rate
        if w.getsampwidth()!=2: return {'seconds':round(duration,2)}
        count=squares=quiet=blocks=peak=0
        while True:
            data=w.readframes(max(1,rate//10))
            if not data:break
            samples=[x[0] for x in struct.iter_unpack('<h',data)]
            power=sum(v*v for v in samples);squares+=power;count+=len(samples);peak=max(peak,max(map(abs,samples)));blocks+=1
            if power/max(1,len(samples)) < (32768*10**(-40/20))**2:quiet+=1
        db=lambda v:round(20*math.log10(max(v,1)/32768),1)
        return {'seconds':round(duration,2),'rms_dbfs':db(math.sqrt(squares/max(1,count))),'peak_dbfs':db(peak),'quiet_fraction':round(quiet/max(1,blocks),3)}

def export_audio(source,dest):
    source=Path(source);dest=Path(dest);ext=dest.suffix.lower()
    if not is_audio(source):raise ValueError('Generate a valid WAV first.')
    if ext not in ('.wav','.mp3','.mp2','.mpeg','.mpg'):raise ValueError('Choose WAV, MP3 or MPEG audio.')
    if source.resolve()==dest.resolve():return dest
    fd,name=tempfile.mkstemp(prefix='.avs-export-',suffix=ext,dir=dest.parent);os.close(fd);tmp=Path(name)
    try:
        if ext=='.wav':shutil.copy2(source,tmp)
        else:
            # MPEG-1 Layer II audio elementary stream, not a video container.
            args=['-c:a','libmp3lame','-b:a','192k','-f','mp3'] if ext=='.mp3' else ['-ar','48000','-c:a','mp2','-b:a','192k','-f','mp2']
            ffmpeg(['-i',str(source),'-vn',*args,str(tmp)])
        if tmp.stat().st_size<100:raise RuntimeError('No audio was exported.')
        os.replace(tmp,dest);return dest
    finally:tmp.unlink(missing_ok=True)
