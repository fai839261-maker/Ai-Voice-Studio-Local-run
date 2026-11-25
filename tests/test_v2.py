import hashlib,os,tempfile,threading,unittest,zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from studio.core import Store,models
from studio import managed, downloads
from unittest.mock import patch
import urllib.request
from studio.importers import read_script

class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=self.temp.name
    def tearDown(self):
        if self.old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
        else:os.environ['AI_VOICE_STUDIO_HOME']=self.old
        self.temp.cleanup()
    def test_paragraph_whole(self):
        s=Store();text='One. Two.\n\nThree. Four.'
        for mode,expected in [('whole',[text]),('paragraph',['One. Two.','Three. Four.']),('sentence',['One.','Two.','Three.','Four.'])]:
            j=s.new_job('Test',text,'kokoro','af_heart',options={'segment_mode':mode})
            self.assertEqual([x['body'] for x in s.sections(j)],expected)
    def test_docx_import(self):
        p=Path(self.temp.name)/'script.docx'
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',xml)
        self.assertEqual(read_script(p),'First\n\nSecond')
    def test_model_routes(self):
        from runtime.runner import ADAPTERS
        self.assertEqual({k for k,m in models().items() if not m.get('requires')},set(ADAPTERS))
    def test_cancel_resume_and_hash(self):
        data=b'abc'*400000;requests=[]
        class H(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                offset=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0]);requests.append(offset)
                self.send_response(206 if offset else 200);self.send_header('Content-Length',str(len(data)-offset))
                if offset:self.send_header('Content-Range',f'bytes {offset}-{len(data)-1}/{len(data)}')
                self.end_headers()
                try:self.wfile.write(data[offset:])
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),H);th=threading.Thread(target=server.serve_forever,daemon=True);th.start()
        # Test transport only: production requires HTTPS.
        req_patch=patch.object(downloads,'request',side_effect=lambda u,h=None,method='GET':urllib.request.Request(u,headers=h or {},method=method));open_patch=patch.object(downloads,'open_secure',side_effect=urllib.request.urlopen);req_patch.start();open_patch.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/test';items=[{'name':'model.safetensors','url':url,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}];cancel=threading.Event()
            def progress(done,total,stage):
                if done>=262144:cancel.set()
            with self.assertRaises(managed.Cancelled):managed.download_files('qwen06bvoice','Ryan',items,progress,cancel)
            self.assertFalse(managed.downloaded('qwen06bvoice','Ryan'))
            events=[];managed.download_files('qwen06bvoice','Ryan',items,lambda d,t,s:events.append((d,t)),threading.Event())
            self.assertTrue(managed.downloaded('qwen06bvoice','Ryan'));self.assertGreater(requests[-1],0);self.assertEqual(events[-1],(len(data),len(data)))
        finally:req_patch.stop();open_patch.stop();server.shutdown();server.server_close();th.join()
if __name__=='__main__':unittest.main()
