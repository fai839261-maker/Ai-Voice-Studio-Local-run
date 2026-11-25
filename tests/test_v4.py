import hashlib,json,os,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
from studio import managed,external
from studio.core import APP_VERSION
from studio.update_local import stage_update,verify_update
from studio.environment_audit import audit_existing,write_license_inventory

class V4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.env=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':str(self.path)});self.env.start()
        self.exe='AI_Voice_Studio_Setup_'+APP_VERSION+'.exe';self.data=b'MZ synthetic installer for test only';self.digest=hashlib.sha256(self.data).hexdigest()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def archive(self,entries):
        p=self.path/'update.zip'
        with zipfile.ZipFile(p,'w') as z:
            for n,d in entries:z.writestr(n,d)
        return p
    def test_update_zip_and_exe(self):
        z=self.archive([(self.exe,self.data),(self.exe+'.sha256.txt',self.digest)])
        p,declared,actual=stage_update(z);self.assertEqual((declared,actual),(self.digest,self.digest));verify_update(p,self.digest)
        with self.assertRaisesRegex(ValueError,'does not match'):verify_update(p,'0'*64)
        exe=self.path/self.exe;exe.write_bytes(self.data);self.assertEqual(stage_update(exe)[2],self.digest)
    def test_reject_source_zip_and_traversal(self):
        for entries in [[('main.py','print(1)')],[(self.exe,self.data),('../evil.exe',b'MZ')],[('../'+self.exe,self.data)],[(self.exe,self.data),(self.exe+'.sha256.txt','0'*64)],[('AI_Voice_Studio_Setup_0.1.0.exe',self.data)]]:
            with self.assertRaises(ValueError):stage_update(self.archive(entries))
    def test_version_audit_invalidates_only_existing_affected_profile(self):
        py=managed.engine_python('piper');py.parent.mkdir(parents=True);py.touch();mark=managed.profile_dir('piper')/'piper.installed.json';mark.write_text('{}')
        resp=Mock(returncode=1,stdout='example package requires incompatible version',stderr='')
        with patch.object(managed,'probe',return_value=py),patch.object(external,'run',return_value=resp),patch.object(managed,'probe_import',return_value=(True,'OK')):
            result=audit_existing()
        self.assertEqual(result['repair_required'],1);self.assertFalse(mark.exists());self.assertIn('incompatible version',str(result));self.assertTrue((self.path/'logs/environment-audit.json').is_file())
    def test_no_network_or_install_for_empty_audit(self):
        with patch.object(managed,'run') as run,patch.object(managed,'ensure_runtime') as install:
            self.assertEqual(audit_existing()['repair_required'],0);run.assert_not_called();install.assert_not_called()
        self.assertEqual(len(json.loads(write_license_inventory().read_text())['models']),17)
    def test_layout_source_contract(self):
        base=Path(__file__).resolve().parents[1];s=(base/'studio/ui_v3.py').read_text();queue=s.split('    def make_queue(self):',1)[1].split('    def open_output(self):',1)[0];self.assertNotIn('Selected job',queue);self.assertIn('ResizeMode.Interactive',queue);self.assertIn('self.section_table.hide()',queue);self.assertIn('Start imported jobs automatically',s);self.assertIn('AI Voice Studio updates (*.exe *.zip)',s);self.assertNotIn("label('Model sources and licenses'",s)
        self.assertIn('Compress-Archive',(base/'Build-Windows.ps1').read_text())
