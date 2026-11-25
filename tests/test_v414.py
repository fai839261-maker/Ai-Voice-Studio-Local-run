import ast, hashlib, io, os, re, sys, tempfile, threading, unittest, wave
from pathlib import Path
from unittest.mock import patch
from studio import downloads, ranged_transfer, managed, services
from studio.core import Store,sha256,is_audio
from studio.profile_gate import ProfileGate

class Response(io.BytesIO):
    def __init__(self,data,status,headers):super().__init__(data);self.status=status;self.headers=headers
    def geturl(self):return 'https://example.invalid/file'

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name);self.data=b'0123456789abcdef'*64;self.item={'name':'model.bin','url':'https://example.invalid/file','size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()};self.block=patch.object(ranged_transfer,'BLOCK',128);self.minimum=patch.object(ranged_transfer,'MINIMUM',256);self.block.start();self.minimum.start()
    def tearDown(self):self.block.stop();self.minimum.stop();self.tmp.cleanup()
    def response(self,req):
        h=req.get_header('Range');n=len(self.data)
        if not h:return Response(self.data,200,{'Content-Length':str(n)})
        start,end=h.removeprefix('bytes=').split('-');a=int(start);b=int(end) if end else n-1
        return Response(self.data[a:b+1],206,{'Content-Range':f'bytes {a}-{b}/{n}','Content-Length':str(b-a+1)})
    def execute(self,progress=lambda *_:None,check=lambda:None):downloads.download_set(self.folder,[self.item],progress,check,managed.valid_file,sha256)
    def test_two_connections_and_checksum(self):
        # First two range requests must both be in flight before either returns.
        barrier=threading.Barrier(2);seen=[];lock=threading.Lock()
        def fake(req):
            with lock:seen.append(req.get_header('Range'));i=len(seen)
            if i<=2:barrier.wait(2)
            return self.response(req)
        with patch.object(downloads,'open_secure',side_effect=fake):self.execute()
        self.assertEqual((self.folder/'model.bin').read_bytes(),self.data);self.assertEqual(len(seen),8);self.assertIn('bytes=0-127',seen);self.assertIn('bytes=128-255',seen)
    def test_ordinary_fallback_preserves_prefix(self):
        dest=self.folder/'model.bin';part=downloads.part_path(dest,self.item);part.write_bytes(self.data[:128]);seen=[]
        def fake(req):
            h=req.get_header('Range');seen.append(h)
            if h and re.fullmatch('bytes=\\d+-\\d+',h):return Response(self.data,200,{'Content-Length':str(len(self.data))})
            return self.response(req)
        with patch.object(downloads,'open_secure',side_effect=fake):self.execute()
        self.assertEqual(seen[-1],'bytes=128-');self.assertEqual(dest.read_bytes(),self.data)
    def test_unknown_wheel_size_uses_range_after_headers(self):
        self.item['size']=None;seen=[]
        def fake(req):seen.append(req.get_header('Range'));return self.response(req)
        with patch.object(downloads,'open_secure',side_effect=fake):self.execute()
        self.assertIsNone(seen[0]);self.assertIn('bytes=0-127',seen);self.assertEqual(self.item['size'],len(self.data))
    def test_no_hash_no_ranges(self):
        self.item.pop('sha256');seen=[]
        def fake(req):seen.append(req.get_header('Range'));return self.response(req)
        with patch.object(downloads,'open_secure',side_effect=fake):self.execute()
        self.assertEqual(seen,[None])
    def test_range_cancel_preserves_verified_prefix(self):
        dest=self.folder/'model.bin';part=downloads.part_path(dest,self.item);part.write_bytes(self.data[:128]);flag=threading.Event()
        def progress(done,*_):
            if done>128:flag.set()
        def check():
            if flag.is_set():raise managed.Cancelled('cancel')
        with patch.object(downloads,'open_secure',side_effect=self.response):
            with self.assertRaises(managed.Cancelled):self.execute(progress,check)
        prefix=part.read_bytes();self.assertEqual(prefix,self.data[:len(prefix)]);self.assertFalse(dest.exists())
    def test_bad_range_digest_uses_single_stream(self):
        seen=[]
        def fake(req):
            h=req.get_header('Range');seen.append(h)
            r=self.response(req)
            if h and re.fullmatch('bytes=\\d+-\\d+',h):return Response(b'X'*len(r.getvalue()),206,r.headers)
            return r
        with patch.object(downloads,'open_secure',side_effect=fake),patch.object(downloads.time,'sleep',return_value=None):self.execute()
        self.assertIsNone(seen[-1]);self.assertEqual((self.folder/'model.bin').read_bytes(),self.data)

class QueueTests(unittest.TestCase):
    def test_shared_readers_exclude_writer(self):
        gate=ProfileGate();reader=gate.reader();reader.acquire();result=[]
        def other():
            r=gate.reader();result.append(r.acquire(blocking=False));result.append(gate.acquire(blocking=False));r.release()
        t=threading.Thread(target=other);t.start();t.join(2);self.assertEqual(result,[True,False]);reader.release();self.assertTrue(gate.acquire(blocking=False));gate.release()
    def test_two_same_model_jobs_complete_concurrently(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':td}):
            store=Store();store.set_setting('generation_mode','parallel');store.set_setting('parallel_jobs',2);ids=[store.new_job('Hi','Hi','kokoro','bf_emma') for _ in range(2)];barrier=threading.Barrier(2);errors=[]
            def render(model,voice,text,output,*a):
                barrier.wait(3)
                with wave.open(str(output),'wb') as w:w.setparams((1,2,24000,0,'NONE',''));w.writeframes(b'\x01\x00'*2400)
            def job(jid):
                try:services.process_job(store,jid)
                except BaseException as exc:errors.append(exc)
            with patch.object(managed,'downloaded',return_value=True),patch.object(managed,'marker_current',return_value=True),patch.object(managed,'engine_python',return_value=Path(sys.executable)),patch.object(services,'invoke_engine',side_effect=render),patch.object(services,'asset_signature',return_value='sig'):
                ts=[threading.Thread(target=job,args=(jid,)) for jid in ids]
                for t in ts:t.start()
                for t in ts:t.join(5)
            self.assertFalse(errors);self.assertTrue(all(store.job(j)['status']=='completed' and is_audio(Path(store.job(j)['output'])) for j in ids))
    def test_queue_and_removed_panel_contract(self):
        base=Path(__file__).resolve().parents[1];ui=(base/'studio/ui_v3.py').read_text();v2=(base/'studio/ui_v2.py').read_text();tree=ast.parse(ui);methods={n.name:ast.get_source_segment(ui,n) for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        self.assertIn('pending[:1]',methods['pump_jobs']);self.assertIn('generation_mode',methods['job_capacity']);self.assertIn('Add to queue',methods['refresh_studio_job']);self.assertNotIn('if self.studio_job in self.running',methods['add_script']);self.assertNotIn("uv.addWidget(label('Offline software update'",v2)
