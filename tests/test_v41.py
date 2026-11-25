import tempfile,os,json,wave,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from studio.core import Store,models,is_audio,safe_chunks
from studio.resource_policy import limits,admission,thread_budget
from studio.hf_import import make_entry,parse_repo,import_repo
from studio import services,managed
from runtime.adapters.onnx_inputs import TypedSession

class V41(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.tmp.name});self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    @staticmethod
    def wav(p):
        Path(p).parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(p),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\x00\x00'*1000)
    def test_onnx_graph_declared_types(self):
        try:import numpy as np
        except ImportError:self.skipTest('NumPy not installed')
        sess=Mock();sess.get_inputs.return_value=[Mock(name='x') for _ in range(3)]
        for obj,name,dtype in zip(sess.get_inputs.return_value,['input_ids','style','speed'],['tensor(int64)','tensor(float)','tensor(float)']):obj.name=name;obj.type=dtype
        TypedSession(sess).run(None,{'tokens':np.array([[1,2]],dtype=np.int32),'style':np.ones((1,256),dtype=np.float64),'speed':np.array([1],dtype=np.int32)})
        feeds=sess.run.call_args.args[1];self.assertEqual(feeds['speed'].dtype,np.float32);self.assertEqual(feeds['input_ids'].dtype,np.int64);self.assertNotIn('tokens',feeds)
    def test_gpu_retry_new_process(self):
        p=Path(self.tmp.name)/'out.wav';py=Path(self.tmp.name)/'python';py.touch();devices=[]
        def run(cmd,*args,**kwargs):
            payload=json.loads(Path(cmd[-1]).read_text());devices.append(payload['options']['device'])
            if len(devices)==1:raise RuntimeError('CUDA error: device-side assert triggered')
            self.wav(payload['output'])
        opts={'device':'cuda'}
        with patch.object(services,'run_command',side_effect=run),patch.object(managed,'engine_python',return_value=py):services.invoke_engine('chatterbox','','Test',p,opts)
        self.assertEqual(devices,['cuda','cpu']);self.assertEqual(opts['device'],'cpu');self.assertTrue(is_audio(p))
    def test_resource_saved_and_admission(self):
        s=Store();s.set_setting('cpu_limit',50);s.set_setting('ram_limit',75);s.set_setting('gpu_limit',60);p=limits(Store());self.assertEqual(thread_budget(p['cpu_limit'],8),4);self.assertTrue(admission({'RAM':80},p,'cpu'));self.assertEqual(admission({'GPU':99},p,'cpu'),'');self.assertTrue(admission({'GPU':99},p,'cuda'));self.assertEqual(admission({'CPU':30,'RAM':30,'GPU':20},p,'cuda'),'')
    def test_chunk_and_single_output(self):
        s=Store();text=' '.join(['This is a sentence in a long script.']*60);jid=s.new_job('Story',text,'chatterbox','',options={'segment_mode':'whole','auto_chunk':True});parts=s.sections(jid);self.assertGreater(len(parts),1);self.assertEqual(' '.join(x['body'] for x in parts),text);self.assertTrue(all(len(x['body'])<=240 for x in parts));self.assertTrue(s.job(jid)['output'].endswith('.wav'))
        for x in parts:self.wav(x['file'])
        from studio.core import join_wave
        p=Path(s.job(jid)['output']);join_wave([Path(x['file']) for x in parts],p);self.assertTrue(is_audio(p));s.clear_jobs();self.assertEqual(s.jobs(),[]);self.assertTrue(p.exists())
    def test_clear_active_refused(self):
        s=Store();jid=s.new_job('Active','Test','piper','en_GB-alan-medium');s.job_state(jid,'running')
        with self.assertRaises(RuntimeError):s.clear_jobs()
        self.assertIsNotNone(s.job(jid))
    def test_hf_safe_import_and_persistence(self):
        info={'sha':'a'*40,'siblings':[{'rfilename':'model.safetensors'}],'cardData':{'license':'apache-2.0'}};cfg={'model_type':'vits','architectures':['VitsModel']}
        with patch.object(managed,'fetch_json',side_effect=[info,cfg]):m,added=import_repo('https://huggingface.co/example/voice')
        self.assertTrue(added);self.assertIn(m['id'],models());self.assertEqual(m['hf_revision'],'a'*40);self.assertEqual(m['profile'],'hf_vits');self.assertEqual(import_repo('example/voice')[1],False)
        for bad in [{'model_type':'bark'},dict(cfg,auto_map={'AutoModel':'evil.Class'})]:
            with self.assertRaises(ValueError):make_entry('example/voice',info,bad)
        with self.assertRaises(ValueError):make_entry('example/voice',dict(info,siblings=[{'rfilename':'pytorch_model.bin'}]),cfg)
        for bad in ['http://huggingface.co/a/b','https://evil.example/a/b','a/../b','https://huggingface.co/a/b/tree/main']:
            with self.assertRaises(ValueError):parse_repo(bad)
