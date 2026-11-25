import json,os,sys,tempfile,unittest,threading
from pathlib import Path
from unittest.mock import patch,Mock
from studio import external,managed
from studio.setup_queue import available_slots
from studio.preflight import prepare_runtime

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.tmp.name});self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_external_import_reports_real_traceback(self):
        # Run an actual external Python process; see precise failed module in output.
        ok,detail=managed.probe_import(sys.executable,'import avs_nonexistent_test_module')
        self.assertFalse(ok);self.assertIn('ModuleNotFoundError',detail);self.assertIn('avs_nonexistent_test_module',detail)
    def test_clean_external_environment(self):
        bundle=str(Path(self.tmp.name)/'internal');keep=str(Path(self.tmp.name)/'system')
        e={'PATH':os.pathsep.join([bundle,str(Path(bundle)/'Qt'),keep]), 'PYTHONHOME':'bad','PYTHONPATH':'bad','QT_PLUGIN_PATH':'bad','_PYI_ARCHIVE_FILE':'bad','HF_HOME':'keep'}
        with patch.object(sys,'_MEIPASS',bundle,create=True):clean=external.clean_env(e)
        self.assertEqual(clean['PATH'],keep);self.assertEqual(clean['HF_HOME'],'keep')
        for key in ('PYTHONHOME','PYTHONPATH','QT_PLUGIN_PATH','_PYI_ARCHIVE_FILE'):self.assertNotIn(key,clean)
    def test_external_process_success(self):
        r=external.run([sys.executable,'-c','print("ok")'],capture_output=True,text=True,timeout=5)
        self.assertEqual((r.returncode,r.stdout.strip()),(0,'ok'))
    def test_queue_not_counted_as_active(self):
        tasks={'piper':{'phase':'work'},'qwen':{'phase':'queued'},'kokoro':{'phase':'queued'},'runtime':{'phase':'work'}}
        cards={'piper','qwen','kokoro'};self.assertEqual(available_slots(tasks,cards,1),0);self.assertEqual(available_slots(tasks,cards,3),2)
        tasks.pop('piper');self.assertEqual(available_slots(tasks,cards,1),1)
    def test_offline_preflight_does_not_fetch_runtime(self):
        with patch.object(managed,'runtime_python',return_value=None),patch.object(managed,'ensure_runtime_serial') as install:
            self.assertEqual(prepare_runtime(lambda:0,offline=True),0);install.assert_not_called()
    def test_compatible_engine_reuses_installed_packages(self):
        py=Path(self.tmp.name)/'python.exe';py.touch();resp=Mock(returncode=0,stdout='No broken requirements',stderr='')
        with patch.object(managed,'ensure_runtime',return_value=py),patch.object(managed,'engine_python',return_value=py),patch.object(managed,'probe',return_value=py),patch.object(managed,'import_ok',return_value=True),patch.object(managed,'engine_probe',return_value=(True,'OK')),patch.object(managed,'probe_import',return_value=(True,'OK')),patch.object(external,'run',return_value=resp),patch.object(managed,'run'),patch.object(managed,'pip_install') as pip:
            managed.ensure_engine('piper','cpu',lambda _:None);pip.assert_not_called();self.assertTrue(managed.marker_current('piper'))
    def test_old_marker_requires_check(self):
        path=managed.profile_dir('piper');path.mkdir(parents=True,exist_ok=True);(path/'piper.installed.json').write_text(json.dumps({'package':'piper-tts'}))
        self.assertFalse(managed.marker_current('piper'))
    def test_compatible_torch_pairs(self):
        self.assertEqual(managed.torch_version({'profile':'chatterbox'}),'2.6.0');self.assertEqual(managed.torch_version({'profile':'qwen'}),'2.8.0')
    def test_import_failure_does_not_set_marker(self):
        py=Path(self.tmp.name)/'python.exe';py.touch();resp=Mock(returncode=0,stdout='',stderr='')
        with patch.object(managed,'ensure_runtime',return_value=py),patch.object(managed,'engine_python',return_value=py),patch.object(managed,'probe',return_value=py),patch.object(managed,'import_ok',return_value=True),patch.object(managed,'engine_probe',return_value=(False,'ImportError: specific DLL')),patch.object(managed,'probe_import',return_value=(True,'OK')),patch.object(external,'run',return_value=resp),patch.object(managed,'pip_install'):
            with self.assertRaisesRegex(RuntimeError,'specific DLL'):managed.ensure_engine('piper','cpu',lambda _:None)
            self.assertFalse(managed.marker_current('piper'))
    def test_no_bottom_task_panel_and_theme(self):
        project=Path(__file__).resolve().parents[1];ui=(project/'studio/ui_v3.py').read_text();style=(project/'studio/ui.py').read_text();main=(project/'main.py').read_text()
        self.assertNotIn('task_shelf',ui);self.assertNotIn('Download limit reached',ui);self.assertIn('QDialog, QMessageBox',style);self.assertIn('studio.ui_v5',main);self.assertIn('app.setPalette(pal)',Path('studio/theme.py').read_text(encoding='utf-8'))
