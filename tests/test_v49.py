"""Portable regression tests; Robocopy and Windows kernel behavior are mocked."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
import sys
from studio import move_engine as m, robocopy_engine as r, move_worker as w

class RobocopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.src=self.root/'app';self.data=self.root/'data';self.target=self.root/'target'
        for p in (self.src,self.data,self.target):p.mkdir()
        (self.src/'exe').write_bytes(b'app')
        self.ordinary=self.data/'env';self.ordinary.mkdir()
        for i in range(32):(self.ordinary/f'p{i}.txt').write_bytes(f'file{i}'.encode())
        self.p=m.make_plan(self.src,self.data,self.target/'new','test');self.j=self.root/'plan.json';m.atomic_json(self.j,self.p)
        self.logs=[]
    def tearDown(self):
        for p in (self.src,self.data):
            if m.exists(p) and m.is_link(p):m.remove_link(p)
        self.tmp.cleanup()
    def runmove(self):
        with patch.object(m,'RESERVE',0):m.relocate(m.read_json(self.j),self.j,lambda:None,lambda *_:None,self.logs.append)
    def fake_copy(self,exe,src,dst,*args):
        shutil.copytree(src,dst,dirs_exist_ok=True);return True
    def test_flags_restartable_non_destructive(self):
        cmd=r.arguments('robocopy.exe','C:/a','D:/b',8)
        self.assertEqual(cmd[:3],['robocopy.exe','C:/a','D:/b'])
        self.assertTrue({'/Z','/MT:8','/E','/XJ','/SL','/R:2','/W:2'}<=set(cmd))
        self.assertFalse({'/MIR','/PURGE','/MOVE','/MOV','/COPYALL','/B','/ZB'}&set(cmd))
    def test_missing_robocopy_falls_back(self):
        with patch.object(r,'executable',return_value=None),patch.object(r,'copy_tree') as copy:
            self.runmove();copy.assert_not_called()
        self.assertEqual((self.data/'env/p5.txt').read_bytes(),b'file5')
    def test_robocopy_stages_and_python_verifies(self):
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=self.fake_copy) as copy,patch.object(m,'file_digest',wraps=m.file_digest) as digest:
            self.runmove();self.assertEqual(copy.call_count,1);self.assertGreaterEqual(digest.call_count,64)
        self.assertEqual((self.data/'env/p5.txt').read_bytes(),b'file5')
    def test_wrong_data_repaired_even_on_success_status(self):
        def bad(exe,src,dst,*args):
            self.fake_copy(exe,src,dst,*args);(dst/'env/p5.txt').write_bytes(b'bad!!');return True
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=bad):self.runmove()
        self.assertEqual((self.data/'env/p5.txt').read_bytes(),b'file5')
    def test_failed_copy_falls_back_and_preserves_completed_files(self):
        def fail(exe,src,dst,*args):
            shutil.copy2(src/'env/p5.txt',dst/'env/p5.txt');return False
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=fail):self.runmove()
        self.assertTrue(any('incomplete transfer' in line for line in self.logs));self.assertEqual((self.data/'env/p6.txt').read_bytes(),b'file6')
    def test_launch_error_falls_back(self):
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=OSError('job denied')):self.runmove()
        self.assertTrue(any('job denied' in line for line in self.logs))
    def test_link_tree_fallback_with_safe_sibling_acceleration(self):
        cache=self.data/'cache';cache.mkdir();(cache/'link').symlink_to(self.ordinary,target_is_directory=True)
        d,f=m.inventory(self.data,[self.data]);self.assertEqual(r.safe_groups(self.data,d,f,set(),m.is_link),[Path('env')])
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=self.fake_copy):self.runmove()
        self.assertEqual((self.data/'cache/link/p5.txt').read_bytes(),b'file5');self.assertFalse(m.is_link(self.data/'cache/link'))
    def test_python_partial_branch_not_overwritten_by_robocopy(self):
        d,f=m.inventory(self.data,[self.data]);self.assertEqual(r.safe_groups(self.data,d,f,{Path('env/p5.txt')},m.is_link),[])
    def test_restart_after_robocopy_staging(self):
        def interrupted():raise OSError('simulated interruption')
        with patch.object(r,'executable',return_value=Path('robocopy.exe')),patch.object(r,'copy_tree',side_effect=self.fake_copy),patch.object(m,'RESERVE',0):
            with self.assertRaisesRegex(OSError,'simulated interruption'):m.relocate(self.p,self.j,lambda:None,lambda *_:None,self.logs.append,before_switch=interrupted)
        self.assertEqual(m.read_json(self.j)['phase'],'copying');self.assertFalse(m.is_link(self.data));self.runmove()
        self.assertEqual((self.data/'env/p5.txt').read_bytes(),b'file5')
    def test_surviving_robocopy_blocks_only_its_own_staging(self):
        stage=Path(self.p['staging']);exe=Path('/windows/robocopy.exe')
        def proc(pid,dst):return SimpleNamespace(pid=pid,info={'exe':str(exe),'create_time':1,'cmdline':[str(exe),str(self.data),str(dst)]})
        scan=Mock(return_value=[proc(123,stage/'Data'),proc(456,self.root/'another')]);scan.cache_clear=Mock()
        fake=SimpleNamespace(process_iter=scan,NoSuchProcess=type('NoSuchProcess',(Exception,),{}),AccessDenied=type('AccessDenied',(Exception,),{}))
        with patch.dict(sys.modules,{'psutil':fake}):self.assertEqual(w.process_blockers(self.p),[(123,1,exe)])
