import hashlib, io, json, os, ssl, tempfile, unittest, urllib.error, zipfile
from pathlib import Path
from unittest.mock import patch
from studio import downloads, managed, network_trust, local_assets, package_cache, repository_metadata, weight_cache
from studio.core import sha256
from studio.log_filter import today_errors

class Response(io.BytesIO):
    def __init__(self,data=b'',status=200,headers=None):super().__init__(data);self.status=status;self.headers=headers or {}
    def geturl(self):return 'https://example.invalid/file'

class NewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.p=patch.dict(os.environ,{'AI_VOICE_STUDIO_HOME':self.tmp.name});self.p.start()
    def tearDown(self):self.p.stop();self.tmp.cleanup()
    def test_tls_context_secure(self):
        ctx=network_trust.context();self.assertTrue(ctx.check_hostname);self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
        ctx=network_trust.public_context();self.assertTrue(ctx.check_hostname);self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
    def test_certificate_not_retried(self):
        err=urllib.error.URLError(ssl.SSLCertVerificationError(1,'certificate invalid'))
        self.assertFalse(downloads.retryable(err))
    def test_range_size_fallback(self):
        with patch.object(downloads,'open_secure',side_effect=[Response(headers={}),Response(b'x',206,{'Content-Range':'bytes 0-0/12345','Content-Length':'1'})]) as op:
            self.assertEqual(downloads.head_size('https://example.invalid/file'),12345)
            self.assertEqual(op.call_args.args[0].get_header('Range'),'bytes=0-0')
        self.assertIsNone(downloads.response_size({'Content-Range':'bytes 0-0/*','Content-Length':'1'}))
    def test_dynamic_size(self):
        item=dict(name='data.bin',url='https://example.invalid/file',size=None);output=[]
        with patch.object(downloads,'open_secure',return_value=Response(b'abc',200,{'Content-Length':'3'})):
            downloads.download_set(self.root,[item],lambda d,t,s:output.append((d,t)),lambda:None,managed.valid_file,sha256)
        self.assertEqual(item['size'],3);self.assertIn((3,3),output)
    def test_parallel_set(self):
        fs=[dict(name=f'{i}.bin',url=f'https://example.invalid/{i}',size=3) for i in range(5)]
        def fake(req):return Response(b'abc',200,{'Content-Length':'3'})
        with patch.object(downloads,'open_secure',side_effect=fake):downloads.parallel_set(self.root,fs,lambda *_:None,lambda:None,managed.valid_file,sha256)
        self.assertEqual(len(json.loads((self.root/'.avs-manifest.json').read_text())),5)
        self.assertEqual(sum(p.stat().st_size for p in self.root.glob('*.bin')),15)
    def test_attach_zip_matching_only(self):
        z=self.root/'assets.zip';payload=b'test'; dest=self.root/'model';dest.mkdir()
        with zipfile.ZipFile(z,'w') as f:f.writestr('repo/model.safetensors',payload);f.writestr('repo/setup.py','raise Exception()')
        item=dict(name='model.safetensors',size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
        self.assertEqual(local_assets.import_assets(z,dest,[item],lambda *_:None,lambda:None),1)
        self.assertEqual((dest/item['name']).read_bytes(),payload);self.assertFalse((dest/'setup.py').exists())
    def test_attach_rejects_traversal(self):
        z=self.root/'bad.zip'
        with zipfile.ZipFile(z,'w') as f:f.writestr('../danger.py','bad')
        with self.assertRaises(ValueError):
            with local_assets.candidates(z):pass
    def test_attach_bad_hash(self):
        src=self.root/'src';src.mkdir();(src/'model.bin').write_bytes(b'bad');dst=self.root/'dst';dst.mkdir()
        self.assertEqual(local_assets.import_assets(src,dst,[dict(name='model.bin',size=3,sha256='0'*64)],lambda *_:None,lambda:None),0)
        self.assertFalse((dst/'model.bin').exists())
    def test_reuse_http_body(self):
        cache=self.root/'pip';url='https://example.invalid/file';key=hashlib.sha224(url.encode()).hexdigest();body=cache/'http-v2'/Path(*key[:5])/(key+'.body');body.parent.mkdir(parents=True);body.write_bytes(b'data')
        dest=self.root/'wheel.whl';self.assertFalse(package_cache.reuse_http_body(cache,url,dest,'0'*64));self.assertFalse(dest.exists())
        self.assertTrue(package_cache.reuse_http_body(cache,url,dest,sha256(body)));self.assertEqual(dest.read_bytes(),b'data')
    def test_torch_index_exact_cpu_wheel(self):
        digest='a'*64;html=f'<a href="https://download.pytorch.org/whl/cpu/torch-2.8.0%2Bcpu-cp311-cp311-win_amd64.whl#sha256={digest}">wheel</a>'
        with patch.object(downloads,'open_secure',return_value=Response(html.encode())):
            report=package_cache.torch_report('torch','2.8.0','cpu')
        self.assertEqual(report['download_info']['archive_info']['hashes']['sha256'],digest)
    def test_hf_direct_metadata(self):
        repository_metadata.CACHE.clear();rev='b'*40;digest='a'*64
        item=repository_metadata.hydrate([{'name':'test.bin','url':'https://huggingface.co/a/b/resolve/main/sub/test.bin'}],lambda _:dict(sha=rev,siblings=[dict(rfilename='sub/test.bin',size=5,lfs={'sha256':digest})]))[0]
        self.assertEqual(item['size'],5);self.assertEqual(item['sha256'],digest);self.assertIn(rev,item['url'])
    def test_shared_weight(self):
        src=self.root/'models'/'src';src.mkdir(parents=True);(src/'weight.bin').write_bytes(b'abc');digest=sha256(src/'weight.bin');(src/'.avs-manifest.json').write_text(json.dumps([dict(name='weight.bin',sha256=digest,size=3)]));dest=self.root/'models'/'dest';dest.mkdir()
        items=[dict(name='other.bin',size=3,sha256=digest)]
        self.assertEqual(weight_cache.reuse_weights(dest,items,lambda *_:None,lambda:None),1);self.assertEqual((dest/'other.bin').read_bytes(),b'abc')
    def test_today_multiline(self):
        log='[2026-11-20 12:01:00] ERROR: old\ntrace\n[2026-11-21 00:00:00] ERROR: foo\ntrace\n[2026-11-21 00:01:00] ERROR: bar\n'
        self.assertEqual(today_errors(log,'2026-11-21'),'[2026-11-21 00:00:00] ERROR: foo\ntrace\n[2026-11-21 00:01:00] ERROR: bar\n')
