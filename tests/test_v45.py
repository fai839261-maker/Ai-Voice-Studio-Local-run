import tempfile, unittest, os, shutil
from pathlib import Path
from unittest.mock import patch
from studio import move_engine as m

class MoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name).resolve()
        self.app=self.base/'old-program';self.app.mkdir()
        self.data=self.base/'old-data';self.data.mkdir()
        self.target=self.base/'other drive';self.target.mkdir()
        self.dest=self.target/'AI Voice Studio'
        (self.app/'application.exe').write_bytes(b'program')
        (self.data/'models').mkdir()
        (self.data/'models'/'model.safetensors').write_bytes(b'model weights')
        (self.data/'models'/'partial.part').write_bytes(b'partial')
        (self.data/'state').mkdir()
        (self.data/'state'/'saved.json').write_text('settings',encoding='utf-8')
        self.journal=self.base/'plan.json';self.messages=[]
    def tearDown(self):self.tmp.cleanup()
    def plan(self,dest=None,token='test'):
        p=m.make_plan(self.app,self.data,dest or self.dest,token)
        m.atomic_json(self.journal,p);return p
    def execute(self,plan,smoke=lambda:None):
        with patch.object(m,'RESERVE',0):
            return m.relocate(plan,self.journal,smoke,lambda *_:None,self.messages.append)
    def clean_links(self):
        # On Windows, never let tempfile cleanup follow junctions.
        for p in (self.app,self.data):
            if m.exists(p) and m.is_link(p):m.remove_link(p)
    def test_full_transaction_preserves_absolute_paths_and_partial_files(self):
        try:
            p=self.plan()
            self.assertEqual(self.execute(p,lambda:self.assertEqual((self.app/'application.exe').read_bytes(),b'program')),[])
            self.assertEqual(self.app.resolve(),self.dest/'Program')
            self.assertEqual(self.data.resolve(),self.dest/'Data')
            self.assertEqual((self.data/'models'/'partial.part').read_bytes(),b'partial')
            self.assertEqual((self.data/'state'/'saved.json').read_text(),'settings')
            self.assertFalse(Path(p['rows'][0]['backup']).exists())
            self.assertEqual(m.read_json(self.journal)['phase'],'complete')
            (self.data/'models'/'future.bin').write_bytes(b'future')
            self.assertTrue((self.dest/'Data'/'models'/'future.bin').is_file())
        finally:self.clean_links()
    def test_smoke_failure_restores_both_sources(self):
        p=self.plan()
        def broken():raise RuntimeError('self-check failed')
        with self.assertRaisesRegex(RuntimeError,'self-check failed'):self.execute(p,broken)
        m.rollback(m.read_json(self.journal),self.messages.append)
        self.assertFalse(m.is_link(self.app));self.assertFalse(m.is_link(self.data))
        self.assertEqual((self.data/'models'/'model.safetensors').read_bytes(),b'model weights')
        self.assertFalse(self.dest.exists())
    def test_interrupted_switch_restores_source_even_with_empty_placeholder(self):
        p=self.plan();p['phase']='switching';m.atomic_json(self.journal,p)
        self.app.rename(Path(p['rows'][0]['backup']));self.app.mkdir()
        m.rollback(p,self.messages.append)
        self.assertEqual((self.app/'application.exe').read_bytes(),b'program')
        self.assertTrue((self.data/'state'/'saved.json').is_file())
    def test_nested_directory_link_materialized_without_following_cleanup(self):
        m.make_junction(self.data/'cached-models',self.data/'models')
        try:
            p=self.plan();self.execute(p)
            copied=self.dest/'Data'/'cached-models'
            self.assertFalse(m.is_link(copied))
            self.assertEqual((copied/'model.safetensors').read_bytes(),b'model weights')
        finally:self.clean_links()
    def test_hf_file_symlink_materialized(self):
        link=self.data/'snapshot.bin';blob=self.data/'models'/'model.safetensors'
        try:link.symlink_to(blob)
        except OSError as e:self.skipTest(f'File symlinks not permitted: {e}')
        try:
            self.execute(self.plan());self.assertFalse(m.is_link(self.data/'snapshot.bin'))
            self.assertEqual((self.data/'snapshot.bin').read_bytes(),b'model weights')
        finally:self.clean_links()
    def test_recursive_move_from_existing_junction(self):
        try:
            self.execute(self.plan());second=self.target/'next move';second.mkdir()
            new=second/'AI Voice Studio';self.execute(self.plan(new,'second'))
            self.assertEqual(self.app.resolve(),new/'Program');self.assertEqual(self.data.resolve(),new/'Data')
            self.assertFalse((self.dest/'Program').exists());self.assertFalse((self.dest/'Data').exists())
            self.assertEqual((self.data/'models'/'partial.part').read_bytes(),b'partial')
        finally:self.clean_links()
    def test_failed_repeat_move_restores_original_junctions(self):
        try:
            self.execute(self.plan());new=self.target/'another folder';p=self.plan(new,'second')
            def failed():raise RuntimeError('test smoke failure')
            with self.assertRaises(RuntimeError):self.execute(p,failed)
            m.rollback(m.read_json(self.journal),self.messages.append)
            self.assertEqual(self.app.resolve(),self.dest/'Program');self.assertEqual(self.data.resolve(),self.dest/'Data')
        finally:self.clean_links()
    def test_low_storage_does_not_change_any_source(self):
        p=self.plan()
        with patch.object(m.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
            with self.assertRaisesRegex(RuntimeError,'Insufficient destination storage'):self.execute(p)
        self.assertFalse(self.dest.exists());self.assertFalse(m.is_link(self.data));self.assertTrue((self.app/'application.exe').is_file())
    def test_choose_new_folder_if_old_failed_move_exists(self):
        self.dest.mkdir();(self.dest/'untouched').write_text('old',encoding='utf-8')
        p=m.next_destination(self.target)
        self.assertEqual(p.name,'AI Voice Studio (2)')
        self.assertEqual((self.dest/'untouched').read_text(),'old')
    def test_reject_destination_inside_source_roots(self):
        with self.assertRaisesRegex(RuntimeError,'outside'):self.plan(self.data/'new-location')
    def test_external_directory_link_refused_without_copying(self):
        outside=self.base/'unrelated';outside.mkdir();(outside/'private.txt').write_text('untouched')
        link=self.data/'external';m.make_junction(link,outside)
        try:
            with self.assertRaisesRegex(RuntimeError,'outside managed'):self.execute(self.plan())
            self.assertFalse(self.dest.exists());self.assertEqual((outside/'private.txt').read_text(),'untouched')
        finally:m.remove_link(link)
    def test_circular_link_refused(self):
        link=self.data/'loop';m.make_junction(link,self.data)
        try:
            with self.assertRaisesRegex(RuntimeError,'Circular'):self.execute(self.plan())
            self.assertFalse(self.dest.exists())
        finally:m.remove_link(link)
    def test_copy_verification_failure_leaves_sources(self):
        p=self.plan()
        with patch.object(m,'copy_verified',side_effect=OSError('disk write error')):
            with self.assertRaisesRegex(OSError,'disk write error'):self.execute(p)
        m.rollback(m.read_json(self.journal),self.messages.append)
        self.assertTrue((self.app/'application.exe').is_file());self.assertTrue((self.data/'models'/'partial.part').is_file())
        self.assertFalse(Path(p['staging']).exists())
    def test_second_junction_failure_restores_first_junction_and_data(self):
        p=self.plan();real=m.make_junction
        def broken(link,target):
            if Path(link)==self.data:raise OSError('simulated junction failure')
            return real(link,target)
        with patch.object(m,'make_junction',side_effect=broken):
            with self.assertRaisesRegex(OSError,'junction failure'):self.execute(p)
        m.rollback(m.read_json(self.journal),self.messages.append)
        self.assertFalse(m.is_link(self.app));self.assertFalse(m.is_link(self.data))
        self.assertEqual((self.app/'application.exe').read_bytes(),b'program')
        self.assertEqual((self.data/'models'/'partial.part').read_bytes(),b'partial')
        self.assertFalse(self.dest.exists())
    def test_cleanup_does_not_delete_unowned_destination(self):
        p=self.plan();p['phase']='publishing';self.dest.mkdir()
        (self.dest/'unrelated.txt').write_text('keep')
        m.rollback(p,self.messages.append)
        self.assertEqual((self.dest/'unrelated.txt').read_text(),'keep')
    def test_cleanup_never_deletes_a_link_target(self):
        target=self.base/'outside';target.mkdir();(target/'important').write_text('keep')
        folder=self.base/'safe-deletion';folder.mkdir();m.make_junction(folder/'link',target)
        m.safe_remove_tree(folder)
        self.assertEqual((target/'important').read_text(),'keep')

if __name__=='__main__':unittest.main()
