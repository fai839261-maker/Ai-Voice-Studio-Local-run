import ast, os, sys, tempfile, threading, time, unittest, wave, json
from pathlib import Path
from unittest.mock import patch, Mock
from datetime import datetime
from studio import managed, services, warm_worker
from studio.core import Store, is_audio
from studio.generation_policy import automatic_title, effective_device, setup_state, dependency_state, render_timeout
from studio.resource_policy import admission


def write_audio(path):
    with wave.open(str(path),'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*2400)


class GenerationUpdate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.temp.name});self.env.start()
    def tearDown(self):
        warm_worker.release();self.env.stop();self.temp.cleanup()
    def test_cpu_engine_ignores_gpu_admission(self):
        self.assertEqual(effective_device({'torch':False},'cuda'),'cpu')
        self.assertEqual(effective_device({'torch':True},'cuda'),'cuda')
        self.assertEqual(admission({'GPU':99},{'cpu_limit':85,'ram_limit':85,'gpu_limit':90},effective_device({},'cuda')),'')
    def test_auto_name(self):
        self.assertEqual(automatic_title('Hi',datetime(2026,11,21,1,2,3)),'Hi — 21 Nov 01-02-03')
        self.assertTrue(automatic_title('  ').startswith('Voice Over'))
    def test_setup_actions(self):
        for s,d,expected in [('model.safetensors','','Downloading'),('Connecting: model','','Resolving'),('Verifying model','','Checking'),('Connection interrupted','','Retrying'),('Installing dependencies','Repairing dependencies','Repairing'),('Installing dependencies','Downloading dependencies','Downloading'),('Installing dependencies','Waiting for dependencies','Waiting')]:self.assertEqual(setup_state(s,d),expected)
        self.assertEqual(setup_state('model.safetensors',cancelling=True),'Cancelling')
        self.assertEqual(dependency_state('Package repair needed: missing dependency'),'Repairing dependencies')
    def test_setup_wait_does_not_lock_installed_profile(self):
        entered=threading.Event();cancel=threading.Event();done=threading.Event()
        errors=[]
        def repair():
            try:managed.ensure_engine('piper','cpu',lambda _:entered.set(),cancel)
            except managed.Cancelled:pass
            except Exception as e:errors.append(e)
            finally:done.set()
        managed.INSTALL_LOCK.acquire();t=threading.Thread(target=repair,daemon=True);t.start()
        try:
            self.assertTrue(entered.wait(2));time.sleep(.05)
            lock=managed.profile_lock('kokoro');self.assertTrue(lock.acquire(blocking=False));lock.release()
            # Run a real queue transaction that writes and joins a synthetic PCM segment.
            store=Store();jid=store.new_job('Hi','Hi','kokoro','bf_emma',options={'device':'cuda'})
            with patch.object(managed,'downloaded',return_value=True),patch.object(managed,'marker_current',return_value=True),patch.object(managed,'engine_python',return_value=Path(sys.executable)),patch.object(services,'invoke_engine',side_effect=lambda model,voice,text,output,*a:write_audio(output)),patch.object(services,'asset_signature',return_value='signature'):
                services.process_job(store,jid)
            self.assertEqual(store.job(jid)['status'],'completed');self.assertTrue(is_audio(Path(store.job(jid)['output'])))
            self.assertFalse(done.is_set())
        finally:cancel.set();managed.INSTALL_LOCK.release();t.join(3)
        self.assertTrue(done.is_set());self.assertFalse(errors)
    def test_waiting_profile_does_not_hold_global_installer(self):
        lock=managed.profile_lock('piper');lock.acquire();entered=threading.Event();cancel=threading.Event();finished=threading.Event()
        def repair():
            try:managed.ensure_engine('piper','cpu',lambda _:entered.set(),cancel)
            except managed.Cancelled:pass
            finally:finished.set()
        t=threading.Thread(target=repair,daemon=True);t.start()
        try:
            self.assertTrue(entered.wait(2));self.assertTrue(managed.INSTALL_LOCK.acquire(timeout=1));managed.INSTALL_LOCK.release()
        finally:cancel.set();lock.release();t.join(3)
        self.assertTrue(finished.is_set())
    def test_valid_local_runtime_without_marker(self):
        with patch.object(managed,'marker_current',return_value=False),patch.object(managed,'engine_python',return_value=Path(sys.executable)),patch.object(managed,'engine_probe',return_value=(True,'OK')),patch.object(managed,'probe_import',return_value=(True,'OK')),patch.object(managed,'ensure_engine') as install:
            self.assertTrue(managed.generation_runtime_ok('kokoro','cpu'));install.assert_not_called()
    def test_repair_called_outside_profile_lock(self):
        store=Store();jid=store.new_job('Hi','Hi','kokoro','bf_emma')
        def repair(*a):
            available=[]
            def attempt():
                lock=managed.profile_lock('kokoro');ok=lock.acquire(blocking=False);available.append(ok)
                if ok:lock.release()
            t=threading.Thread(target=attempt);t.start();t.join(1);self.assertEqual(available,[True])
        with patch.object(managed,'downloaded',return_value=True),patch.object(managed,'generation_runtime_ok',return_value=False),patch.object(managed,'ensure_engine',side_effect=repair) as setup,patch.object(services,'_process_job',return_value='ok'):
            self.assertEqual(services.process_job(store,jid),'ok');setup.assert_called_once()
    def test_missing_model_fails_not_pending(self):
        store=Store();jid=store.new_job('Hi','Hi','kokoro','bf_emma')
        with patch.object(managed,'downloaded',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'missing'):services.process_job(store,jid)
        self.assertEqual(store.job(jid)['status'],'failed')
    def test_short_onnx_watchdog(self):
        self.assertEqual(render_timeout('kokoro','Hi'),120);self.assertEqual(render_timeout('qwen06bclone','Hi'),3600)
        p=Path(self.temp.name)/'stall.py';p.write_text('import time,sys\nsys.stdin.readline()\ntime.sleep(20)\n');job={'model':'kokoro','voice':'bf_emma','models_dir':self.temp.name,'options':{}}
        with self.assertRaises(warm_worker.WarmTransportError):warm_worker.run([sys.executable,str(p)],job,os.environ,lambda _:None,timeout=.2)
        self.assertIsNone(warm_worker.slot_worker())
    def test_cold_fallback_valid_audio(self):
        dest=Path(self.temp.name)/'out.wav'
        def cold(cmd,*a,**kw):write_audio(Path(json.loads(Path(cmd[-1]).read_text())['output']))
        with patch.object(managed,'engine_python',return_value=Path(sys.executable)),patch.object(warm_worker,'run',side_effect=warm_worker.WarmTransportError('pipe closed')),patch.object(services,'run_command',side_effect=cold) as run:
            services.invoke_engine('kokoro','bf_emma','Hi',dest,{'keep_model_warm':True});run.assert_called_once()
        self.assertTrue(is_audio(dest))
    def test_ui_contract_without_qt(self):
        p=Path(__file__).resolve().parents[1]/'studio/ui_v3.py';tree=ast.parse(p.read_text());methods={node.name:ast.get_source_segment(p.read_text(),node) for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)}
        self.assertIn('setWordWrap(False)',methods['make_setup']);self.assertIn("button('S'",methods['make_setup']);self.assertNotIn('Set up Piper',methods['make_setup']);self.assertNotIn('install_runtime',methods['make_setup']);self.assertIn("button('Python'",methods['make_settings']);self.assertNotIn('QInputDialog',methods['add_script']);self.assertIn("setEnabled(not files)",methods['refresh_card']);self.assertNotIn('Working…',methods['start_task'])
