import hashlib, io, json, os, tempfile, unittest, urllib.error
from pathlib import Path
from unittest.mock import patch
from studio import managed, downloads, services
from studio.core import sha256

class Response(io.BytesIO):
    def __init__(self,data,status,headers):
        super().__init__(data);self.status=status;self.headers=headers
    def geturl(self):return 'https://example.invalid/file'

class DownloadV44(unittest.TestCase):
    def test_range_rejected_restarts_cleanly(self):
        payload=b'new data';item={'name':'file.bin','url':'https://example.invalid/file','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);dest=folder/item['name'];downloads.part_path(dest,item).write_bytes(b'old')
            requests=[]
            def open_(req):
                requests.append(req.get_header('Range'))
                if len(requests)==1:raise urllib.error.HTTPError(req.full_url,416,'Range invalid',{},None)
                return Response(payload,200,{'Content-Length':str(len(payload))})
            with patch.object(downloads,'open_secure',side_effect=open_),patch.object(downloads.time,'sleep'),patch.object(downloads.time,'monotonic',side_effect=range(1000)):
                downloads.download_set(folder,[item],lambda *_:None,lambda:None,managed.valid_file,sha256)
            self.assertEqual(requests,['bytes=3-',None]);self.assertEqual(dest.read_bytes(),payload)
    def test_resumable_wheel_uses_shared_transfer(self):
        data=b'verified wheel';hash_=hashlib.sha256(data).hexdigest();name='demo-1-py3-none-any.whl'
        item={'download_info':{'url':'https://example.invalid/'+name,'archive_info':{'hashes':{'sha256':hash_}}}}
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':td}):
            folder=managed.wheelhouse();dest=folder/name;downloads.part_path(dest,{'url':item['download_info']['url']}).write_bytes(data[:4])
            with patch.object(downloads,'open_secure',return_value=Response(data[4:],206,{'Content-Length':str(len(data)-4),'Content-Range':f'bytes 4-{len(data)-1}/{len(data)}'})) as opener:
                result=managed.wheel_from_report(item,lambda _:None,None,'ignored')
            self.assertEqual(dest,result);self.assertEqual(dest.read_bytes(),data);self.assertEqual(opener.call_args.args[0].get_header('Range'),'bytes=4-')
    def test_pending_plan_reused(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':td}):
            with patch.object(services,'download_size',return_value=42) as size:
                a=managed.file_manifest('kokoro','');b=managed.file_manifest('kokoro','');self.assertEqual(a,b);self.assertEqual(size.call_count,len(a))
    def test_retry_only_transient_package_failure(self):
        self.assertTrue(managed.network_failure(RuntimeError('ReadTimeout: connection broken')))
        self.assertFalse(managed.network_failure(RuntimeError('ResolutionImpossible: incompatible requirements')))
        self.assertTrue(managed.is_network_wait('Downloading encodec-0.1.1.tar.gz (3.7 MB)'))
        self.assertFalse(managed.is_network_wait('Building wheel for encodec'))
    def test_pip_network_configuration(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':td}):
            e=managed.env(False);self.assertEqual(e['PIP_RESUME_RETRIES'],'10');self.assertEqual(e['PIP_TIMEOUT'],'120')
