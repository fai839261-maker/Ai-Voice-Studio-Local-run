"""Portable interruption recovery and downloader/UI regressions."""
import hashlib, tempfile, unittest, os, io, json
from pathlib import Path
from unittest.mock import patch
from studio import move_engine as m, move_worker as worker, downloads, managed
from studio.core import sha256

class RecoveryMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.app=self.root/'app';self.data=self.root/'data';self.target=self.root/'target'
        for x in (self.app,self.data,self.target):x.mkdir()
        self.payload=b'0123456789'*10000
        (self.app/'app.exe').write_bytes(b'app')
        (self.data/'weights.bin').write_bytes(self.payload)
        self.dest=self.target/'new';self.j=self.root/'plan.json';self.p=m.make_plan(self.app,self.data,self.dest,'test');m.atomic_json(self.j,self.p)
    def tearDown(self):
        for x in (self.app,self.data):
            if m.exists(x) and m.is_link(x):m.remove_link(x)
        self.tmp.cleanup()
    def move(self,**kwargs):
        with patch.object(m,'RESERVE',0):return m.relocate(m.read_json(self.j),self.j,lambda:None,lambda *_:None,lambda _:None,**kwargs)
    def interrupted_copy(self):
        def stop():raise OSError('simulated power loss before switch')
        with self.assertRaisesRegex(OSError,'simulated power loss'):self.move(before_switch=stop)
        self.assertEqual(m.read_json(self.j)['phase'],'copying')
        return Path(self.p['staging'])
    def test_completed_file_reused_without_rewrite_after_interruption(self):
        stage=self.interrupted_copy();st=stage/'Data'/'weights.bin';old=st.stat().st_ino
        self.move();self.assertEqual((self.dest/'Data'/'weights.bin').stat().st_ino,old)
        self.assertEqual((self.data/'weights.bin').read_bytes(),self.payload)
        self.assertEqual(m.read_json(self.j)['phase'],'complete')
    def test_partial_prefix_resumes(self):
        part=self.root/'checkpoint';dest=self.root/'finished';src=self.data/'weights.bin';part.write_bytes(self.payload[:50000]);old=part.stat().st_ino
        m.copy_verified(src,dest,len(self.payload),src.stat().st_mtime_ns,lambda _:None,part)
        self.assertEqual(dest.stat().st_ino,old);self.assertEqual(dest.read_bytes(),self.payload);self.assertFalse(part.exists())
    def test_corrupt_partial_repaired(self):
        part=self.root/'checkpoint';dest=self.root/'finished';src=self.data/'weights.bin';part.write_bytes(b'X'*50000)
        m.copy_verified(src,dest,len(self.payload),src.stat().st_mtime_ns,lambda _:None,part)
        self.assertEqual(dest.read_bytes(),self.payload)
    def test_corrupt_completed_stage_repaired(self):
        stage=self.interrupted_copy();(stage/'Data'/'weights.bin').write_bytes(b'X'*len(self.payload))
        self.move();self.assertEqual((self.data/'weights.bin').read_bytes(),self.payload)
    def test_interrupted_switch_forward_recovery(self):
        stage=self.interrupted_copy();p=m.read_json(self.j);stage.rename(self.dest);p['phase']='switching';m.atomic_json(self.j,p);m.switch(p['rows'][0])
        m.prepare_resume(m.read_json(self.j),self.j);self.assertFalse(m.is_link(self.app));self.assertTrue((stage/'Data'/'weights.bin').is_file())
        self.move();self.assertEqual((self.data/'weights.bin').read_bytes(),self.payload)
    def test_interrupted_publish_forward_recovery(self):
        stage=self.interrupted_copy();p=m.read_json(self.j);p['phase']='publishing';m.atomic_json(self.j,p);stage.rename(self.dest)
        m.prepare_resume(m.read_json(self.j),self.j);self.move();self.assertTrue((self.data/'weights.bin').is_file())
    def test_user_can_restore_original_after_copy(self):
        stage=self.interrupted_copy();m.rollback(m.read_json(self.j),lambda _:None)
        self.assertFalse(stage.exists());self.assertFalse(m.is_link(self.app));self.assertEqual((self.data/'weights.bin').read_bytes(),self.payload)
    def test_worker_keeps_checkpoint_after_failure_and_accepts_rollback(self):
        control=self.root/'control';control.mkdir();run=control/'run';run.mkdir();p=m.read_json(self.j);p.update(control=str(control),run=str(run),log=str(self.root/'log'));m.atomic_json(self.j,p);m.atomic_json(control/'active.json',{'run':str(run)})
        with patch.object(worker,'wait_parent'),patch.object(worker,'ensure_quiet'),patch.object(worker,'privacy'),patch.object(worker,'smoke'),patch.object(m,'RESERVE',0),patch.object(m,'copy_verified',side_effect=OSError('simulated write failure')):
            self.assertFalse(worker.execute(self.j,lambda *_:None)[2]);self.assertTrue((control/'active.json').exists())
        with patch.object(worker,'wait_parent'),patch.object(worker,'ensure_quiet'):
            self.assertTrue(worker.execute(self.j,lambda *_:None,'rollback')[2]);self.assertFalse((control/'active.json').exists());self.assertEqual((self.data/'weights.bin').read_bytes(),self.payload)
    def test_compact_model_card_and_startup_recovery_contract(self):
        project=Path(__file__).resolve().parents[1];ui=(project/'studio/ui_v3.py').read_text();self.assertIn('bottom.addWidget(install);bottom.addWidget(source);bottom.addWidget(cancel)',ui);self.assertNotIn('top.addWidget(source)',ui)
        source=(project/'studio/migration.py').read_text();self.assertIn('register_recovery(helper)',source);self.assertIn("'--recover'",source)

class DownloadEfficiencyTests(unittest.TestCase):
    def test_existing_verified_file_hashed_once(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);data=b'weights';(folder/'file.bin').write_bytes(data)
            item={'name':'file.bin','url':'https://example.invalid/file','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            with patch.object(downloads,'open_secure') as network,patch('studio.core.sha256',wraps=sha256) as hash_fn:
                downloads.download_set(folder,[item],lambda *_:None,lambda:None,managed.valid_file,hash_fn)
                network.assert_not_called();self.assertEqual(hash_fn.call_count,1)
