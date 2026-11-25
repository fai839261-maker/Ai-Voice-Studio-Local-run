import hashlib,json,os,sys,tempfile,unittest,zipfile,venv
from pathlib import Path
from unittest.mock import patch
from studio import managed

class StagedInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.environment=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.tmp.name});self.environment.start()
    def tearDown(self):self.environment.stop();self.tmp.cleanup()
    def test_verified_remote_wheel_reuse(self):
        name='avs_demo-1.0-py3-none-any.whl';p=managed.wheelhouse()/name;p.write_bytes(b'wheel data');digest=hashlib.sha256(p.read_bytes()).hexdigest();item={'download_info':{'url':'https://example.invalid/'+name,'archive_info':{'hashes':{'sha256':digest}}}}
        with patch.object(managed,'run') as run:
            self.assertEqual(managed.wheel_from_report(item,lambda _:None,None,sys.executable),p);run.assert_not_called()
    def test_source_requires_resolver(self):
        self.assertIsNone(managed.wheel_from_report({'download_info':{'url':'https://example.invalid/source.tar.gz'}},lambda _:None,None,sys.executable))
    def test_staged_local_wheel_install(self):
        # Real pip transaction, no remote network. Minimal valid test wheel.
        p=managed.wheelhouse()/'avs_cache_demo-1.0-py3-none-any.whl';prefix='avs_cache_demo-1.0.dist-info/'
        files={'avs_cache_demo.py':'VALUE = 42\n',prefix+'METADATA':'Metadata-Version: 2.1\nName: avs-cache-demo\nVersion: 1.0\n',prefix+'WHEEL':'Wheel-Version: 1.0\nGenerator: avs-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
        files[prefix+'RECORD']=''.join(k+',,\n' for k in files)+prefix+'RECORD,,\n'
        with zipfile.ZipFile(p,'w') as z:
            for k,v in files.items():z.writestr(k,v)
        target=Path(self.tmp.name)/'test_env';venv.EnvBuilder(with_pip=True).create(target);py=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python');log=[]
        managed.pip_install(py,['--no-index','avs-cache-demo==1.0'],log.append,None)
        ok,detail=managed.probe_import(py,'import avs_cache_demo;assert avs_cache_demo.VALUE==42');self.assertTrue(ok,detail);self.assertTrue(any('Installing 1 resolved packages from local storage' in x for x in log));self.assertTrue(any('Reusing cached wheel' in x for x in log))
        log.clear();managed.pip_install(py,['--no-index','avs-cache-demo==1.0'],log.append,None);self.assertIn('All required packages already installed',log)
    def test_voice_studio_stays_on_page(self):
        source=(Path(__file__).resolve().parents[1]/'studio/ui_v3.py').read_text();add=source.split('    def add_script(self):',1)[1].split('    def import_batch(self):',1)[0];events=source.split('    def drain_events(self):',1)[1].split('    def make_studio(self):',1)[0]
        self.assertNotIn('change_page(',add);self.assertNotIn('change_page(',events);self.assertIn('self.studio_player.load(job[\'output\'])',events);self.assertIn('self.pump_jobs()',add)
