import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
import sys
from studio import move_worker as worker

# Do not inherit or re-export the historical suite into discovery.

class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.app=Path('/old/program')
        self.plan={'exe':'AI_Voice_Studio.exe','rows':[{'logical':str(self.app),'physical':str(self.app),'dst':'/new/Program'},{'logical':'/old/data','physical':'/old/data','dst':'/new/Data'}]}
        self.gui=(2520,10.0,self.app/self.plan['exe'])
        self.runtime=(1234,12.0,Path('/old/data/env/python.exe'))
        self.t=0.0
    def sleep(self,duration):self.t+=duration
    def run_wait(self,blocks,timeout=10,close=True):
        with patch.object(worker,'process_blockers',side_effect=blocks),patch.object(worker.time,'monotonic',side_effect=lambda:self.t),patch.object(worker.time,'sleep',side_effect=self.sleep),patch.object(worker,'request_app_close',return_value=True) as ask:
            worker.ensure_quiet(self.plan,timeout=timeout,allow_close=close)
            return ask
    def test_snapshot_filters_other_install_and_own_process(self):
        def proc(pid,exe):return SimpleNamespace(pid=pid,info={'pid':pid,'exe':str(exe),'create_time':10.0})
        scan=Mock(return_value=[proc(2520,self.gui[2]),proc(1234,self.runtime[2]),proc(33,Path('/another/AI_Voice_Studio.exe')),proc(999,self.gui[2])]);scan.cache_clear=Mock()
        fake=SimpleNamespace(process_iter=scan,NoSuchProcess=type('NoSuchProcess',(Exception,),{}),AccessDenied=type('AccessDenied',(Exception,),{}))
        with patch.dict(sys.modules,{'psutil':fake}),patch.object(worker.os,'getpid',return_value=999):
            self.assertEqual(worker.process_blockers(self.plan),[self.gui,(1234,10.0,self.runtime[2])])
        scan.cache_clear.assert_called_once()
    def test_transient_launcher_exits_and_quiet_interval(self):
        ask=self.run_wait([[self.gui]]*3+[[]]*5)
        ask.assert_not_called();self.assertGreaterEqual(self.t,3.5)
    def test_second_main_window_gets_normal_close_once(self):
        ask=self.run_wait([[self.gui]]*9+[[]]*5)
        ask.assert_called_once_with(*self.gui)
    def test_runtime_worker_never_closed_or_terminated(self):
        with patch.object(worker,'process_blockers',return_value=[self.runtime]),patch.object(worker.time,'monotonic',side_effect=lambda:self.t),patch.object(worker.time,'sleep',side_effect=self.sleep),patch.object(worker,'request_app_close') as ask:
            with self.assertRaisesRegex(RuntimeError,'python.exe.*1234'):
                worker.ensure_quiet(self.plan,timeout=5,allow_close=True)
            ask.assert_not_called()
    def test_same_named_file_in_cache_not_closed(self):
        other=(5,15.0,Path('/old/data/cache/AI_Voice_Studio.exe'))
        ask=self.run_wait([[other]]*9+[[]]*5)
        ask.assert_not_called()
    def test_process_reappearance_resets_quiet_interval(self):
        self.run_wait([[],[],[],[self.gui],[],[],[],[],[]],close=False)
        self.assertEqual(self.t,4.0)
    def test_recheck_never_requests_close(self):
        ask=self.run_wait([[self.gui]]*9+[[]]*5,close=False)
        ask.assert_not_called()
    def test_persistent_main_window_fails_without_force_kill(self):
        with patch.object(worker,'process_blockers',return_value=[self.gui]),patch.object(worker.time,'monotonic',side_effect=lambda:self.t),patch.object(worker.time,'sleep',side_effect=self.sleep),patch.object(worker,'request_app_close',return_value=True) as ask:
            with self.assertRaisesRegex(RuntimeError,'No process was forcibly terminated'):
                worker.ensure_quiet(self.plan,timeout=5,allow_close=True)
            ask.assert_called_once_with(*self.gui)

# An actual filesystem test reuses the fixture without collecting its old tests.
class BeforeSwitchTests(unittest.TestCase):
    def test_blocker_before_switch_keeps_sources(self):
        from studio import move_engine as m
        with __import__('tempfile').TemporaryDirectory() as tmp:
            base=Path(tmp).resolve();app=base/'app';data=base/'data';target=base/'target';app.mkdir();data.mkdir();target.mkdir();(app/'app.exe').write_bytes(b'app');(data/'model.part').write_bytes(b'partial')
            p=m.make_plan(app,data,target/'new','test');j=base/'plan.json';m.atomic_json(j,p)
            def blocked():raise RuntimeError('late process')
            with patch.object(m,'RESERVE',0):
                with self.assertRaisesRegex(RuntimeError,'late process'):
                    m.relocate(p,j,lambda:None,lambda *_:None,lambda _:None,before_switch=blocked)
            self.assertEqual(m.read_json(j)['phase'],'copying')
            m.rollback(m.read_json(j),lambda _:None)
            self.assertEqual((data/'model.part').read_bytes(),b'partial')
            self.assertEqual((app/'app.exe').read_bytes(),b'app')
            self.assertFalse((target/'new').exists())
