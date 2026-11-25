import json,os,sqlite3,stat,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from studio.core import APP_VERSION
from studio.source_update import stage_any,stage_source,SourcePackage,REQUIRED,backup_state,MANIFEST

class SourceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':str(self.root/'data')});self.env.start()
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def package(self,version=APP_VERSION,prefix='Project/',extra=(),omit=()):
        dest=self.root/'update.zip'
        with zipfile.ZipFile(dest,'w') as z:
            for n in sorted(REQUIRED-set(omit)):
                data=json.dumps({'format':'avs-source-update-v1','app':'AI Voice Studio','version':version}) if n==MANIFEST else 'test only'
                z.writestr(prefix+n,data)
            for n,data in extra:z.writestr(n,data)
        return dest
    def test_root_and_wrapper_source_staged_not_executed(self):
        for prefix in ('','Project/'):
            p=stage_any(self.package(prefix=prefix));self.assertIsInstance(p,SourcePackage);self.assertEqual(p.version,APP_VERSION);self.assertEqual((p.project/'main.py').read_text(),'test only');self.assertEqual(len(p.digest),64)
    def test_downgrade(self):
        with self.assertRaisesRegex(ValueError,'Older'):stage_source(self.package(version='0.1.0'))
    def test_incomplete(self):
        with self.assertRaisesRegex(ValueError,'missing required'):stage_source(self.package(omit=['main.py']))
    def test_path_traversal_windows_names_and_outside_wrapper(self):
        for name in ['Project/../escape.txt','Project/studio/CON.py','Project/studio/x.py:evil','Project/studio/x.','Project/studio\\evil.py','Other/studio/a.py','Project/studio/Main.py/../../evil','Project/studio/core.PY']:
            with self.subTest(name=name),self.assertRaises(ValueError):stage_source(self.package(extra=[(name,'bad')]))
    def test_symlink(self):
        i=zipfile.ZipInfo('Project/studio/alias.py');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaisesRegex(ValueError,'Links'):stage_source(self.package(extra=[(i,'target')]))
    def test_legacy_compiled_zip(self):
        p=self.root/'compiled.zip'
        with zipfile.ZipFile(p,'w') as z:z.writestr(f'AI_Voice_Studio_Setup_{APP_VERSION}.exe',b'MZ mock only')
        target,declared,actual=stage_any(p);self.assertTrue(target.is_file());self.assertEqual(len(actual),64)
    def test_database_backup_with_wal(self):
        db=self.root/'state.sqlite3';con=sqlite3.connect(db)
        try:
            con.execute('pragma journal_mode=wal');con.execute('create table t(v)');con.execute('insert into t values (42)');con.commit()
            bak=backup_state(db)
            b=sqlite3.connect(bak)
            try:self.assertEqual(b.execute('select v from t').fetchone(),(42,))
            finally:b.close()
        finally:con.close()
    def test_version_files_consistent(self):
        project=Path(__file__).resolve().parents[1]
        self.assertEqual(json.loads((project/MANIFEST).read_text())['version'],APP_VERSION)
        for path in ('Build-Windows.ps1','installer/AI_Voice_Studio.iss','.github/workflows/build-windows.yml'):
            self.assertIn('AI_Voice_Studio_Setup_'+APP_VERSION,(project/path).read_text())
