import hashlib, tempfile, threading, unittest, urllib.request, io, os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from studio import managed, downloads
from studio.core import sha256

class Response(io.BytesIO):
    def __init__(self, data, status, headers):super().__init__(data);self.status=status;self.headers=headers
    def geturl(self):return 'https://example.invalid/file'

class RecoveryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.folder=Path(self.tmp.name);self.data=b'a'*(1024*1024);self.item={'name':'model.safetensors','url':'https://example.invalid/file','size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}
    def tearDown(self):self.tmp.cleanup()
    def execute(self,progress=lambda *_:None):downloads.download_set(self.folder,[self.item],progress,lambda:None,managed.valid_file,sha256)
    def test_incomplete_response_automatic_resume(self):
        requests=[];n=262144
        def open_(r):
            requests.append(r.get_header('Range'))
            return Response(self.data[:n],200,{'Content-Length':str(len(self.data))}) if len(requests)==1 else Response(self.data[n:],206,{'Content-Length':str(len(self.data)-n),'Content-Range':f'bytes {n}-{len(self.data)-1}/{len(self.data)}'})
        with patch.object(downloads,'open_secure',side_effect=open_),patch.object(downloads.time,'sleep'),patch.object(downloads,'RESERVE',0),patch.object(downloads.time,'monotonic',side_effect=range(1000)):
            self.execute()
        self.assertEqual(requests,[None,f'bytes={n}-']);self.assertEqual((self.folder/self.item['name']).read_bytes(),self.data)
    def test_disk_counts_only_missing_bytes(self):
        dest=self.folder/self.item['name'];part=downloads.part_path(dest,self.item);n=3*262144;part.write_bytes(self.data[:n]);missing=len(self.data)-n
        response=Response(self.data[n:],206,{'Content-Length':str(missing),'Content-Range':f'bytes {n}-{len(self.data)-1}/{len(self.data)}'})
        with patch.object(downloads,'open_secure',return_value=response),patch.object(downloads.shutil,'disk_usage',return_value=SimpleNamespace(free=missing+1)),patch.object(downloads,'RESERVE',0):self.execute()
        self.assertEqual(dest.read_bytes(),self.data)
    def test_storage_error_preserves_partial(self):
        part=downloads.part_path(self.folder/self.item['name'],self.item);part.write_bytes(self.data[:10])
        with patch.object(downloads.shutil,'disk_usage',return_value=SimpleNamespace(free=20)),patch.object(downloads,'RESERVE',0):
            with self.assertRaises(downloads.StorageError):self.execute()
        self.assertEqual(part.read_bytes(),self.data[:10]);self.assertFalse(downloads.CLAIMS)
    def test_insecure_urls_refused(self):
        with self.assertRaises(ValueError):downloads.request('http://example.invalid/file')
    def test_profile_lock_shared(self):
        from studio.core import models
        groups={}
        for key,m in models().items():
            lock=managed.profile_lock(key)
            if m['profile'] in groups:self.assertIs(lock,groups[m['profile']])
            groups[m['profile']]=lock
        self.assertEqual(len(set(groups.values())),len(groups))
