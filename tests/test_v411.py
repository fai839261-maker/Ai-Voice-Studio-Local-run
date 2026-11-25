import unittest,tempfile,os,sys,wave,struct,math,shutil,json
from pathlib import Path
from unittest.mock import patch
from studio import warm_worker,reference_audio,clones,libtorch_sdk
from studio.core import Store,is_audio
from runtime.adapters import warm_cache

class VoiceUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.env=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.tmp.name});self.env.start();warm_worker.release()
        self.wav=self.root/'input.wav'
        with wave.open(str(self.wav),'wb') as w:
            w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b''.join(struct.pack('<h',round(6000*math.sin(i*.09))) for i in range(48000)))
    def tearDown(self):warm_worker.release();self.env.stop();self.tmp.cleanup()
    def test_analysis(self):
        data=reference_audio.describe(self.wav);self.assertEqual(data['seconds'],2);self.assertLess(data['peak_dbfs'],0);self.assertEqual(data['quiet_fraction'],0)
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg executable unavailable')
    def test_crop(self):
        out=self.root/'crop.wav';data=reference_audio.edit_reference(self.wav,out,.5,1.5);self.assertEqual(data['seconds'],1);self.assertTrue(is_audio(out));self.assertEqual(reference_audio.describe(self.wav)['seconds'],2)
    def test_invalid_crop(self):
        for a,b in [(1,0),(0,.1),(0,121),(float('nan'),3)]:
            with self.assertRaises(ValueError):reference_audio.edit_reference(self.wav,self.root/'crop.wav',a,b)
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg executable unavailable')
    def test_export(self):
        for ext in ('.wav','.mp3','.mp2','.mpeg'):
            out=self.root/('final'+ext);self.assertEqual(reference_audio.export_audio(self.wav,out),out);self.assertGreater(out.stat().st_size,100)
        self.assertTrue(is_audio(self.root/'final.wav'))
    def test_save_profile(self):
        s=Store();p=clones.save_profile(s,'Narrator','qwen06bclone',self.wav,'Matching words',True,{'output_speed':.9},style_notes='Measured pauses')
        self.assertEqual(clones.profiles(Store())[0]['style_notes'],'Measured pauses');self.assertEqual(p['reference_analysis']['seconds'],2);self.assertTrue(is_audio(Path(p['reference'])))
        with self.assertRaises(ValueError):clones.save_profile(s,'Narrator','qwen06bclone',self.wav,'',False)
    def test_engine_and_prompt_identity(self):
        warm_cache._engine_key=None
        a=warm_cache.engine('one',object);self.assertIs(a,warm_cache.engine('one',object));b=warm_cache.prompt('x',object);self.assertIs(b,warm_cache.prompt('x',object));self.assertIsNot(a,warm_cache.engine('two',object));self.assertIsNot(b,warm_cache.prompt('x',object))
    def test_prompt_changes_with_reference(self):
        key=warm_cache.reference_key(self.wav,'Words');self.assertNotEqual(key,warm_cache.reference_key(self.wav,'Different words'));self.wav.write_bytes(b'other');self.assertNotEqual(key,warm_cache.reference_key(self.wav,'Words'))
    def test_worker_reuse_and_switch(self):
        # Actual process transport with a synthetic engine. No AI weights loaded.
        path=self.root/'worker.py';path.write_text('import json,sys,os\nfor line in sys.stdin:\n r=json.loads(line);print("PID:"+str(os.getpid()),flush=True);print("AVS_RESULT "+json.dumps({"id":r["id"],"ok":True}),flush=True)\n')
        cmd=[sys.executable,str(path)];job={'model':'piper','voice':'a','models_dir':str(self.root),'options':{}};logs=[]
        warm_worker.run(cmd,job,os.environ,logs.append);first=warm_worker.slot_worker().p.pid
        warm_worker.run(cmd,job,os.environ,logs.append);self.assertEqual(first,warm_worker.slot_worker().p.pid);self.assertIn('Reusing loaded voice engine',logs)
        previous=warm_worker.slot_worker();job['voice']='b';warm_worker.run(cmd,job,os.environ,logs.append);self.assertIsNot(previous,warm_worker.slot_worker());self.assertIsNotNone(previous.p.poll())
        warm_worker.release();self.assertIsNone(warm_worker.slot_worker())
    def test_worker_failure_releases(self):
        p=self.root/'bad.py';p.write_text('import json,sys\nr=json.loads(sys.stdin.readline());print("AVS_RESULT "+json.dumps({"id":r["id"],"ok":False,"error":"test failure"}),flush=True)')
        job={'model':'piper','voice':'a','models_dir':str(self.root),'options':{}}
        with self.assertRaisesRegex(RuntimeError,'test failure'):warm_worker.run([sys.executable,str(p)],job,os.environ,lambda x:None)
        self.assertIsNone(warm_worker.slot_worker())
    def test_optional_sdk_url(self):
        self.assertTrue(libtorch_sdk.URL.startswith('https://download.pytorch.org/libtorch/cpu/'));self.assertIn('2.8.0',libtorch_sdk.NAME)
