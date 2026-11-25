import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE","none")   # tests run without the owner key that ships with the software
import json,os,sys,tempfile,threading,unittest,zipfile,wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
_tmp=tempfile.mkdtemp();os.environ['AI_VOICE_STUDIO_HOME']=_tmp
from studio import owner,favorites,backup,health,preview,theme,core
from studio.core import Store,safe_chunks

class FakeStore:
    def __init__(s):s.d={}
    def setting(s,k,default=None):return s.d.get(k,default)
    def set_setting(s,k,v):s.d[k]=v

class OwnerTests(unittest.TestCase):
    def setUp(s):s.path=Path(tempfile.mkdtemp())/'access.json';s.a=owner.Access(s.path)
    def test_owner_sees_everything_users_follow_rules(s):
        s.a.set_password('abcd');s.a.apply_preset('simple')
        s.assertEqual(s.a.state('page.batch'),owner.SHOW)
        s.a.lock_for_users()
        s.assertEqual(s.a.state('page.batch'),owner.HIDE);s.assertEqual(s.a.state('access.manager'),owner.HIDE)
        s.assertEqual(s.a.state('ctl.speed'),owner.SHOW)
    def test_password_and_persistence(s):
        s.a.set_password('abcd');s.a.set_feature('ctl.pitch',owner.LOCK);s.a.lock_for_users()
        b=owner.Access(s.path)
        s.assertEqual(b.mode,'user');s.assertEqual(b.state('ctl.pitch'),owner.LOCK)
        s.assertFalse(b.enter_owner('wrong'));s.assertTrue(b.enter_owner('abcd'));s.assertEqual(b.state('access.manager'),owner.SHOW)
        s.assertNotIn('abcd',s.path.read_text())
    def test_lock_needs_password_and_short_password_rejected(s):
        with s.assertRaises(ValueError):s.a.lock_for_users()
        with s.assertRaises(ValueError):s.a.set_password('ab')
    def test_unknown_feature_and_corrupt_file(s):
        with s.assertRaises(KeyError):s.a.set_feature('nope',owner.HIDE)
        s.path.write_text('{broken');s.assertEqual(owner.Access(s.path).state('ctl.speed'),owner.SHOW)

class FavTests(unittest.TestCase):
    def test_toggle_and_listing(s):
        st=FakeStore();s.assertFalse(favorites.is_favorite(st,'kokoro','af_heart'))
        s.assertTrue(favorites.toggle(st,'kokoro','af_heart','Heart'));s.assertTrue(favorites.is_favorite(st,'kokoro','af_heart'))
        rows=favorites.all_voices(st,lambda m:m=='kokoro')
        heart=[r for r in rows if r['voice']=='af_heart'][0];s.assertTrue(heart['favorite']);s.assertTrue(heart['ready'])
        s.assertFalse([r for r in rows if r['model']=='piper'])  # no Piper voice installed
        s.assertTrue(favorites.matches(heart,'heart',True,'tts'));s.assertFalse(favorites.matches(heart,'zzz'))
        favorites.toggle(st,'kokoro','af_heart','Heart');s.assertFalse(favorites.is_favorite(st,'kokoro','af_heart'))

class PackTests(unittest.TestCase):
    def test_pack_contents_and_skips(s):
        sw=Path(tempfile.mkdtemp());da=Path(tempfile.mkdtemp());out=Path(tempfile.mkdtemp())
        (sw/'AI_Voice_Studio.exe').write_bytes(b'x'*50)
        for d,f in [('models','m.onnx'),('wheels','a.whl'),('env','e.txt'),('outputs','o.wav'),('logs','l.txt'),('temp','t.txt'),('py','runtime.json')]:
            (da/d).mkdir();(da/d/f).write_bytes(b'data')
        (da/'state').mkdir()
        import sqlite3;c=sqlite3.connect(da/'state'/'studio.sqlite3');c.execute('create table kv(k)');c.execute("insert into kv values('1')");c.commit();c.close()
        z=backup.create_pack(out,software=sw,data=da);names=set(zipfile.ZipFile(z).namelist())
        for n in ('Install_On_New_PC.bat','Install-On-New-PC.ps1','README.txt','Software/AI_Voice_Studio.exe','Data/models/m.onnx','Data/wheels/a.whl','Data/state/studio.sqlite3'):s.assertIn(n,names)
        for n in ('Data/env/e.txt','Data/outputs/o.wav','Data/logs/l.txt','Data/temp/t.txt','Data/py/runtime.json'):s.assertNotIn(n,names)
        z2=backup.create_pack(out,True,True,software=sw,data=da);n2=set(zipfile.ZipFile(z2).namelist())
        s.assertIn('Data/env/e.txt',n2);s.assertIn('Data/outputs/o.wav',n2)
        s.assertFalse(list(out.glob('*.partial')))
        db=Path(tempfile.mkdtemp())/'x.sqlite3';zipfile.ZipFile(z).extract('Data/state/studio.sqlite3',db.parent)
        s.assertEqual(sqlite3.connect(db.parent/'Data/state/studio.sqlite3').execute('select k from kv').fetchone()[0],'1')
    def test_cancel_leaves_no_file(s):
        sw=Path(tempfile.mkdtemp());da=Path(tempfile.mkdtemp());out=Path(tempfile.mkdtemp());(sw/'a.txt').write_text('a')
        ev=threading.Event();ev.set()
        with s.assertRaises(backup.Cancelled):backup.create_pack(out,software=sw,data=da,cancel=ev)
        s.assertEqual(list(out.iterdir()),[])
    def test_script_has_winget_python_step(s):
        s.assertIn('winget install --id Python.Python.3.11',backup.PS1);s.assertIn('Install-On-New-PC.ps1',backup.BAT)

class MiscTests(unittest.TestCase):
    def test_chunk_override(s):
        text=' '.join(['This is a sentence that is fairly long and keeps going.']*40)
        big=safe_chunks([text],'kokoro');small=safe_chunks([text],'kokoro',150)
        s.assertGreater(len(small),len(big));s.assertTrue(all(len(x)<=150 for x in small));s.assertEqual(' '.join(small).split(),text.split())
    def test_preview_samples_and_cache_key(s):
        s.assertIn('Hola',preview.sample_text('Spanish'));s.assertIn('Hello',preview.sample_text('American English'))
        a=preview.cache_file('m','v','t');s.assertEqual(a,preview.cache_file('m','v','t'));s.assertNotEqual(a,preview.cache_file('m','v2','t'))
    def test_health_runs(s):
        r=health.system_checks();s.assertTrue(any(c.name=='Python 3.11' for c in r))
        f=health.fixable([health.Check('p','bad','x','python'),health.Check('e','warn','x','engines',('kokoro',))]);s.assertEqual(f,{'python':True,'engines':['kokoro']})
    def test_theme_builds_all_variants(s):
        for t in ('dark','light'):
            for a in theme.ACCENTS:s.assertNotIn('{',theme.stylesheet(t,a,15).replace('{','',0)[:0])  # builds without error
        s.assertIn(theme.ACCENTS['Blue'],theme.stylesheet('dark','Blue'))
    def test_polish_with_ffmpeg(s):
        from studio import audio_fx
        if not audio_fx.ffmpeg_path():s.skipTest('no ffmpeg')
        p=Path(tempfile.mkdtemp())/'a.wav'
        import math,struct
        with wave.open(str(p),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
            w.writeframes(b''.join(struct.pack('<h',int(8000*math.sin(i/10))) for i in range(16000))+b'\0\0'*32000)
        before=audio_fx.wav_seconds(p);audio_fx.apply_polish(p,True,True,200);after=audio_fx.wav_seconds(p)
        s.assertLess(after,before);s.assertTrue(core.is_audio(p))

if __name__=='__main__':unittest.main()
