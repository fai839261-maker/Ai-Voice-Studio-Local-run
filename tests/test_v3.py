import os,tempfile,unittest,wave,math,struct
from pathlib import Path
from studio.core import Store,is_audio,models
from studio.clones import save_profile,profiles,remove_profile
from studio.audio_fx import apply_voice_fx,ffmpeg_path,atempo_chain

class V3Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.old=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=self.t.name
        self.wav=Path(self.t.name)/'sample.wav'
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,2,16000,0,'NONE','not compressed'))
            f.writeframes(b''.join(struct.pack('<h',int(10000*math.sin(2*math.pi*220*i/16000))) for i in range(32000)))
    def tearDown(self):
        if self.old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
        else:os.environ['AI_VOICE_STUDIO_HOME']=self.old
        self.t.cleanup()
    def test_named_clone_persistence_and_rename(self):
        s=Store();p=save_profile(s,'Narrator','qwen06bclone',self.wav,'Authorized transcript',True,{'output_speed':.9});self.assertTrue(is_audio(Path(p['reference'])))
        self.wav.unlink();s2=Store();self.assertEqual(profiles(s2)[0]['name'],'Narrator');q=save_profile(s2,'New name',p['model'],p['reference'],p['transcript'],True,p['options'],p['id']);self.assertEqual(q['id'],p['id']);self.assertEqual(profiles(s2)[0]['name'],'New name');remove_profile(s2,p['id']);self.assertEqual(profiles(s2),[]);self.assertTrue(Path(p['reference']).exists())
    def test_permission_and_transcript(self):
        s=Store()
        for consent,transcript in [(False,'Hello'),(True,'')]:
            with self.assertRaises(ValueError):save_profile(s,'Test','dia',self.wav,transcript,consent)
    @unittest.skipUnless(ffmpeg_path(),'FFmpeg not available')
    def test_speed_pitch_real_audio(self):
        apply_voice_fx(self.wav,1.25,3);self.assertTrue(is_audio(self.wav))
        with wave.open(str(self.wav),'rb') as f:duration=f.getnframes()/f.getframerate()
        self.assertAlmostEqual(duration,1.6,delta=.18)
    def test_tempo_filter_range(self):
        self.assertEqual(atempo_chain(.25),['atempo=0.5','atempo=0.50000000']);self.assertEqual(atempo_chain(4),['atempo=2','atempo=2.00000000'])
    def test_new_model_entries(self):
        self.assertTrue(models()['indextts2']['snapshot']);self.assertEqual(models()['dia']['hf_repo'],'nari-labs/Dia-1.6B-0626');self.assertEqual(len(models()),17)
if __name__=='__main__':unittest.main()
