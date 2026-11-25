import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE","none")   # tests run without the owner key that ships with the software
import json,os,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('AI_VOICE_STUDIO_HOME',tempfile.mkdtemp())
import avs_boot
from studio import ed25519,updater

class Store:
    def __init__(s):s.d={}
    def setting(s,k,v=None):return s.d.get(k,v)
    def set_setting(s,k,v):s.d[k]=v

class IsolatedHome(unittest.TestCase):
    def setUp(s):
        s.old=os.environ.get('AI_VOICE_STUDIO_HOME');s.home=Path(tempfile.mkdtemp());os.environ['AI_VOICE_STUDIO_HOME']=str(s.home)
        s.src=Path(tempfile.mkdtemp())
        for rel,text in (('studio/a.py','VALUE=1\n'),('studio/__init__.py',''),('runtime/r.py','R=1\n'),('resources/x.json','{"a":1}')):
            f=s.src/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text)
        s.saved=(updater.source_dirs,updater.APP_VERSION);updater.source_dirs=lambda:{k:s.src/k for k in ('studio','runtime','resources')}
        s.store=Store();os.environ['AVS_BASE_VERSION']='1.0.0'
    def tearDown(s):
        os.environ.pop('AVS_BASE_VERSION',None)
        updater.source_dirs,updater.APP_VERSION=s.saved
        if s.old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
        else:os.environ['AI_VOICE_STUDIO_HOME']=s.old
        os.environ.pop('AVS_OVERLAY_DIR',None)
    def make(s,version):
        updater.APP_VERSION=version;return updater.make_update_file(s.store,Path(tempfile.mkdtemp()),'notes here')

class Signatures(unittest.TestCase):
    def test_rfc8032_vector_and_roundtrip(s):
        sk=bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
        s.assertEqual(ed25519.public_key(sk).hex(),'d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
        s.assertEqual(ed25519.sign(sk,b'').hex(),'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')
        sec=ed25519.generate_secret();pub=ed25519.public_key(sec);sig=ed25519.sign(sec,b'hello')
        s.assertTrue(ed25519.verify(pub,b'hello',sig));s.assertFalse(ed25519.verify(pub,b'hellO',sig));s.assertFalse(ed25519.verify(pub,b'hello',sig[:-1]+bytes([sig[-1]^1])))
        s.assertEqual(len(ed25519.fingerprint(pub)),19)

class UpdateFiles(IsolatedHome):
    def test_make_inspect_install_and_apply(s):
        zf=s.make('9.9.9');s.assertTrue(zf.name=='AVS_Update_9.9.9.zip' and zf.with_name(zf.name+'.sha256.txt').is_file())
        info=updater.inspect_update_file(zf);s.assertEqual((info['version'],info['files'],info['api']),('9.9.9',4,avs_boot.API))
        s.assertEqual(updater.pinned_key(s.store),info['public_key'])    # the maker's own PC trusts its key
        updater.APP_VERSION='9.0.0'
        asked=[];updater.stage_install(s.store,zf,lambda fp:asked.append(fp) or True);s.assertEqual(asked,[])  # already trusted: no question
        s.assertEqual(updater.pending()['action'],'install')
        s.assertIn('Update installed',avs_boot.apply_pending());s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'9.9.9')
        s.assertEqual((avs_boot.overlay_dir()/'studio'/'a.py').read_text(),'VALUE=1\n');s.assertIsNone(updater.pending())
    def test_first_update_asks_to_trust_then_pins(s):
        zf=s.make('9.9.9');pub=updater.pinned_key(s.store);s.store.d.clear();updater.APP_VERSION='9.0.0'
        with s.assertRaises(updater.UpdateError):updater.stage_install(s.store,zf,lambda fp:False)
        s.assertEqual(updater.pinned_key(s.store),'')
        updater.stage_install(s.store,zf,lambda fp:True);s.assertEqual(updater.pinned_key(s.store),pub)
        s.store.set_setting('update_trusted_key',updater.ed25519.public_key(bytes(32)).hex())
        with s.assertRaisesRegex(updater.UpdateError,'different publisher'):updater.stage_install(s.store,zf,lambda fp:True)
    def test_not_newer_is_refused(s):
        zf=s.make('1.0.0');updater.APP_VERSION='1.0.0'
        with s.assertRaisesRegex(updater.UpdateError,'not newer'):updater.stage_install(s.store,zf)
        updater.APP_VERSION='2.0.0'
        with s.assertRaisesRegex(updater.UpdateError,'not newer'):updater.stage_install(s.store,zf)
    def rewrite(s,zf,edit):
        out=Path(tempfile.mkdtemp())/'x.zip'
        with zipfile.ZipFile(zf) as a,zipfile.ZipFile(out,'w') as b:
            for i in a.infolist():b.writestr(i,edit(i.filename,a.read(i.filename)))
        return out
    def test_tampering_is_caught(s):
        zf=s.make('9.9.9')
        bad=s.rewrite(zf,lambda n,d:b'VALUE=666\n' if n=='payload/studio/a.py' else d)
        with s.assertRaisesRegex(updater.UpdateError,'damaged'):updater.inspect_update_file(bad)
        def version(n,d):
            if n!='AVS_UPDATE.json':return d
            m=json.loads(d);m['version']='99.0.0';return json.dumps(m).encode()
        with s.assertRaisesRegex(updater.UpdateError,'signature'):updater.inspect_update_file(s.rewrite(zf,version))
        with s.assertRaises(updater.UpdateError):updater.inspect_update_file(s.rewrite(zf,lambda n,d:d if n!='AVS_UPDATE.sig' else b'00'*64))
        junk=Path(tempfile.mkdtemp())/'j.zip';junk.write_bytes(b'not a zip')
        with s.assertRaisesRegex(updater.UpdateError,'not a valid'):updater.inspect_update_file(junk)
    def test_signed_but_dangerous_paths_are_refused(s):
        secret=ed25519.generate_secret();m={'format':1,'name':'AI Voice Studio','version':'9','api':1,'files':{'studio/../../evil.py':'0'*64},'public_key':ed25519.public_key(secret).hex()}
        zp=Path(tempfile.mkdtemp())/'e.zip'
        with zipfile.ZipFile(zp,'w') as z:
            z.writestr('AVS_UPDATE.json',json.dumps(m));z.writestr('AVS_UPDATE.sig',ed25519.sign(secret,updater.canonical(m)).hex());z.writestr('payload/studio/../../evil.py','x')
        with s.assertRaisesRegex(updater.UpdateError,'must not touch'):updater.inspect_update_file(zp)
    def test_newer_api_needs_installer(s):
        zf=s.make('9.9.9');old=avs_boot.API;avs_boot.API=old+1
        try:zf2=s.make('9.9.9')
        finally:avs_boot.API=old
        updater.APP_VERSION='9.0.0'
        with s.assertRaisesRegex(updater.UpdateError,'new setup file'):updater.stage_install(s.store,zf2,lambda fp:True)
    def test_history_and_go_back(s):
        updater.APP_VERSION='1.0.0';s.make('1.1.0')
        z1=s.make('1.1.0');updater.APP_VERSION='1.0.0';updater.stage_install(s.store,z1);avs_boot.apply_pending()
        (s.src/'studio'/'a.py').write_text('VALUE=2\n');z2=s.make('1.2.0');updater.APP_VERSION='1.1.0';updater.stage_install(s.store,z2);avs_boot.apply_pending()
        s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'1.2.0')
        rows=updater.history();s.assertEqual([r['version'] for r in rows],['1.1.0','1.0.0']);s.assertEqual(rows[-1]['id'],'base')
        updater.request_rollback(rows[0]['id']);avs_boot.apply_pending()
        s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'1.1.0');s.assertEqual((avs_boot.overlay_dir()/'studio'/'a.py').read_text(),'VALUE=1\n')
        updater.request_rollback('base');avs_boot.apply_pending();s.assertIsNone(avs_boot.read_manifest(avs_boot.overlay_dir()))
        with s.assertRaises(updater.UpdateError):updater.request_rollback('nonexistent')
    def test_cancel_pending(s):
        zf=s.make('9.9.9');updater.APP_VERSION='9.0.0';updater.stage_install(s.store,zf);s.assertIsNotNone(updater.pending());updater.cancel_pending();s.assertIsNone(updater.pending())

class Booting(IsolatedHome):
    def run_boot(s,code,extra_env=None):
        env={**os.environ,'AI_VOICE_STUDIO_HOME':str(s.home),'PYTHONPATH':str(ROOT),**(extra_env or {})}
        return subprocess.run([sys.executable,'-c','import avs_boot\n'+code],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    def put_layer(s,api=1,version='5.0.0'):
        d=avs_boot.overlay_dir();(d/'studio').mkdir(parents=True,exist_ok=True)
        (d/'studio'/'__init__.py').write_text('MARK="from the update layer"\n');(d/'AVS_UPDATE.json').write_text(json.dumps({'version':version,'api':api}))
    def test_layer_is_loaded_first_and_confirmed(s):
        s.put_layer()
        r=s.run_boot("m=avs_boot.activate();import studio;print(m['version'],getattr(studio,'MARK','real files'))");s.assertEqual(r.stdout.split()[:2],['5.0.0','from']);s.assertIn('update layer',r.stdout)
        s.assertEqual(json.loads((avs_boot.updates_dir()/'boot.json').read_text())['unconfirmed'],1)
        s.run_boot("avs_boot.mark_good()");s.assertEqual(json.loads((avs_boot.updates_dir()/'boot.json').read_text())['unconfirmed'],0)
    def test_two_failed_starts_put_the_layer_aside(s):
        s.put_layer()
        for _ in range(2):s.run_boot("avs_boot.activate()")           # started but never confirmed
        r=s.run_boot("m=avs_boot.activate();import studio;print(m,getattr(studio,'MARK','original files'))");s.assertIn('None original files',r.stdout)
        s.assertFalse(avs_boot.overlay_dir().exists());s.assertTrue(list((avs_boot.updates_dir()/'quarantine').iterdir()));s.assertIn('did not start',(avs_boot.updates_dir()/'last-problem.txt').read_text())
    def test_layer_needing_a_newer_installer_is_ignored(s):
        s.put_layer(api=99);r=s.run_boot("print(avs_boot.activate())");s.assertEqual(r.stdout.strip(),'None');s.assertIn('newer installer',(avs_boot.updates_dir()/'last-problem.txt').read_text())
    def test_reset_flag(s):
        s.put_layer();r=s.run_boot("import sys;sys.argv.append('--reset-updates');print(avs_boot.activate())");s.assertEqual(r.stdout.strip(),'None');s.assertFalse(avs_boot.overlay_dir().exists())
    def test_restore_from_backup_at_start(s):
        (s.home/'state').mkdir();(s.home/'state'/'studio.sqlite3').write_bytes(b'current')
        zp=Path(tempfile.mkdtemp())/'b.zip'
        with zipfile.ZipFile(zp,'w') as z:z.writestr('Data/state/studio.sqlite3','restored');z.writestr('Data/clones/v.wav','wav');z.writestr('Software/app.exe','no')
        avs_boot.pending_path().write_text(json.dumps({'action':'restore','zip':str(zp)}))
        s.assertIn('restored',avs_boot.apply_pending());s.assertEqual((s.home/'state'/'studio.sqlite3').read_text(),'restored');s.assertEqual((s.home/'state'/'studio.before-restore.sqlite3').read_text(),'current')
        s.assertTrue((s.home/'clones'/'v.wav').is_file());s.assertFalse((s.home/'Software').exists())
        bad=Path(tempfile.mkdtemp())/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('Data/../../evil.txt','x')
        avs_boot.pending_path().write_text(json.dumps({'action':'restore','zip':str(bad)}));s.assertIn('Failed',avs_boot.apply_pending());s.assertFalse((s.home.parent/'evil.txt').exists())

class GitHub(IsolatedHome):
    def release(s,names,tag='v9.9.9'):
        return {'tag_name':tag,'body':'What is new','html_url':'https://github.com/o/r/releases/tag/'+tag,'assets':[{'name':n,'size':1000,'browser_download_url':'https://github.com/o/r/releases/download/'+tag+'/'+n} for n in names]}
    def test_latest_release_and_check(s):
        fetch=lambda url:s.release(['notes.txt','AVS_Update_9.9.9.zip','AVS_Update_9.9.10.zip','AVS_Setup.exe'])
        r=updater.latest_release('o/r',fetch);s.assertEqual((r['version'],r['name']),('9.9.10','AVS_Update_9.9.10.zip'))
        s.assertIsNone(updater.latest_release('o/r',lambda u:s.release(['x.zip'])))
        s.assertEqual(updater.check_online(s.store,fetch)['status'],'no_repo');s.store.set_setting('update_repo','o/r')
        updater.APP_VERSION='9.0.0';res=updater.check_online(s.store,fetch);s.assertEqual(res['status'],'newer');s.assertEqual(s.store.setting('update_available'),'9.9.10')
        updater.APP_VERSION='9.9.10';s.assertEqual(updater.check_online(s.store,fetch)['status'],'current');s.assertEqual(s.store.setting('update_available'),'')
        s.assertEqual(updater.check_online(s.store,lambda u:s.release([]))['status'],'none')
    def test_repo_names_and_schedule(s):
        s.assertEqual(updater.clean_repo('https://github.com/unzip/spark.git'),'unzip/spark');s.assertEqual(updater.clean_repo(' unzip/spark '),'unzip/spark')
        for bad in ('','nope','https://evil.com/a/b','a/b/c/d e'):
            with s.assertRaises(updater.UpdateError):updater.clean_repo(bad)
        st=Store();s.assertFalse(updater.auto_check_due(st,1e9));st.d['update_repo']='o/r';s.assertTrue(updater.auto_check_due(st,1e9));st.d['update_last_check']=1e9
        s.assertFalse(updater.auto_check_due(st,1e9+3600));s.assertTrue(updater.auto_check_due(st,1e9+90000));st.d['update_auto_check']=False;s.assertFalse(updater.auto_check_due(st,1e12))
    def test_only_github_hosts_are_downloaded(s):
        with s.assertRaisesRegex(updater.UpdateError,'not hosted on GitHub'):updater.download_release({'url':'https://evil.example.com/AVS_Update_1.0.zip','name':'AVS_Update_1.0.zip'},Path(tempfile.mkdtemp()))
        with s.assertRaises(updater.UpdateError):updater.download_release({'url':'https://github.com/o/r/x.zip','name':'AVS_Update_1.0.zip','size':10**10},Path(tempfile.mkdtemp()))
    def test_version_compare(s):
        s.assertTrue(updater.is_newer('0.10.0','0.9.1'));s.assertTrue(updater.is_newer('1.0','0.99.9'));s.assertFalse(updater.is_newer('0.9.1','0.9.1'));s.assertFalse(updater.is_newer('0.9','0.9.1'));s.assertTrue(updater.is_newer('0.9.1.1','0.9.1'))
if __name__=='__main__':unittest.main()

import sqlite3,time
from studio import backup

class Backups(unittest.TestCase):
    def setUp(s):
        s.data=Path(tempfile.mkdtemp());s.sw=Path(tempfile.mkdtemp());s.out=Path(tempfile.mkdtemp())
        (s.sw/'Voice Studio V0.1.exe').write_bytes(b'x'*40)
        for d,f in (('models','m.onnx'),('wheels','a.whl'),('env','e.txt'),('clones','v.wav'),('training','t.json'),('outputs','o.wav'),('logs','l.txt'),('backups','old.zip'),('app_overlay','x.py'),('config','access.json')):
            (s.data/d).mkdir();(s.data/d/f).write_bytes(b'data')
        (s.data/'config'/'update_signing.key').write_text('secret')
        (s.data/'state').mkdir();c=sqlite3.connect(s.data/'state'/'studio.sqlite3')
        c.executescript("CREATE TABLE jobs(id,script);CREATE TABLE sections(job_id,body);CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT);INSERT INTO jobs VALUES('1','private script');INSERT INTO sections VALUES('1','private');INSERT INTO kv VALUES('clone_profiles','[1]');INSERT INTO kv VALUES('favorite_voices','[2]');INSERT INTO kv VALUES('ui_theme','dark');INSERT INTO kv VALUES('update_repo','o/r')");c.commit();c.close()
    def names(s,z):return set(zipfile.ZipFile(z).namelist())
    def kv(s,z):
        d=Path(tempfile.mkdtemp());zipfile.ZipFile(z).extract('Data/state/studio.sqlite3',d);c=sqlite3.connect(d/'Data/state/studio.sqlite3')
        r=({k:v for k,v in c.execute('select key,value from kv')},c.execute('select count(*) from jobs').fetchone()[0],c.execute('select count(*) from sections').fetchone()[0]);c.close();return r
    def test_owner_backup(s):
        z=backup.create_backup('owner',False,software=s.sw,data=s.data,dest=s.out);n=s.names(z)
        for want in ('AVS_BACKUP.json','Data/state/studio.sqlite3','Data/clones/v.wav','Data/training/t.json','Data/config/update_signing.key','Data/config/access.json'):s.assertIn(want,n)
        for no in ('Data/models/m.onnx','Data/env/e.txt','Data/outputs/o.wav','Data/logs/l.txt','Data/backups/old.zip','Data/app_overlay/x.py','Software/Voice Studio V0.1.exe'):s.assertNotIn(no,n)
        kv,jobs,secs=s.kv(z);s.assertEqual(jobs,1);s.assertIn('clone_profiles',kv)
        z2=backup.create_backup('owner',True,software=s.sw,data=s.data,dest=s.out);n2=s.names(z2);s.assertIn('Data/models/m.onnx',n2);s.assertIn('Data/env/e.txt',n2)
    def test_backup_for_others_keeps_private_things_out(s):
        z=backup.create_backup('others',True,software=s.sw,data=s.data,dest=s.out);n=s.names(z)
        for want in ('Install_On_New_PC.bat','Install-On-New-PC.ps1','Software/Voice Studio V0.1.exe','Data/models/m.onnx','Data/wheels/a.whl','Data/config/access.json'):s.assertIn(want,n)
        for no in ('Data/clones/v.wav','Data/training/t.json','Data/config/update_signing.key','Data/env/e.txt'):s.assertNotIn(no,n)
        kv,jobs,secs=s.kv(z);s.assertEqual((jobs,secs),(0,0));s.assertNotIn('clone_profiles',kv);s.assertNotIn('favorite_voices',kv);s.assertEqual(kv['ui_theme'],'dark');s.assertEqual(kv['update_repo'],'o/r')
        s.assertIn(b'private',(s.data/'state'/'studio.sqlite3').read_bytes())     # the live database was not touched
    def test_list_auto_snapshots_and_pruning(s):
        first=backup.auto_snapshot(data=s.data,dest=s.out,keep=2);s.assertTrue(first and first.is_file());s.assertIsNone(backup.auto_snapshot(data=s.data,dest=s.out,keep=2))   # nothing changed
        for i in range(3):
            time.sleep(1.05);(s.data/'clones'/f'n{i}.wav').write_bytes(b'x');backup.auto_snapshot(data=s.data,dest=s.out,keep=2)
        rows=backup.list_backups(s.out);autos=[r for r in rows if r['kind']=='auto'];s.assertEqual(len(autos),2);s.assertEqual(autos[0]['label'],'auto')
        n=s.names(autos[0]['path']);s.assertIn('Data/clones/n2.wav',n);s.assertNotIn('Data/models/m.onnx',n);s.assertNotIn('Data/training/t.json',n)
        s.assertGreaterEqual(rows[0]['created'],rows[-1]['created'])
    def test_cancel_and_space_and_unknown_kind(s):
        ev=threading.Event();ev.set()
        with s.assertRaises(backup.Cancelled):backup.create_backup('owner',data=s.data,dest=s.out,cancel=ev)
        s.assertEqual([p for p in s.out.iterdir()],[])
        with s.assertRaises(ValueError):backup.create_backup('weird',data=s.data,dest=s.out)
        sizes=backup.extra_sizes(s.data);s.assertEqual((sizes['models']>0,sizes['engines']>0),(True,True))
    def test_stage_restore(s):
        z=backup.create_backup('owner',data=s.data,dest=s.out);backup.stage_restore(z)
        job=json.loads(avs_boot.pending_path().read_text());s.assertEqual((job['action'],job['zip']),('restore',str(z)));avs_boot.pending_path().unlink()
        junk=s.out/'x.zip'
        with zipfile.ZipFile(junk,'w') as zz:zz.writestr('hello.txt','x')
        with s.assertRaises(ValueError):backup.stage_restore(junk)
import threading

class Windows(unittest.TestCase):
    def test_backup_and_update_tabs(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        home=Path(tempfile.mkdtemp());old=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=str(home)
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            w.access.enter_owner('abcd') if w.access.has_password else None;w.apply_access()
            s.assertEqual(w.settings_tabs.tabText(3),'Updates');s.assertFalse(hasattr(w,'upd_history'));s.assertFalse(hasattr(w,'upd_log'));s.assertFalse(hasattr(w,'upd_version'))
            # no update address yet
            w.check_updates();s.assertIn('No update address',w.upd_status.text())
            w.upd_repo.setText('https://github.com/unzip/spark');w.save_update_source();s.assertEqual(w.store.setting('update_repo'),'unzip/spark')
            w.upd_repo.setText('bad address');w.save_update_source();s.assertIn('owner/name',w.upd_status.text());s.assertEqual(w.store.setting('update_repo'),'unzip/spark')
            # result messages
            w.on_update_checked({'status':'newer','release':{'version':'99.0.0','notes':'Faster voices\nmore','name':'AVS_Update_99.0.0.zip','url':'https://github.com/x','size':1}})
            s.assertIn('99.0.0',w.upd_status.text());s.assertFalse(w.upd_get.isHidden());s.assertEqual(w.settings_tabs.tabText(3),'Updates •')
            w.on_update_checked({'status':'current'});s.assertIn('latest version',w.upd_status.text());s.assertEqual(w.settings_tabs.tabText(3),'Updates')
            w.on_update_checked({'status':'error','message':'offline'});s.assertIn('offline',w.upd_status.text())
            # make an update file from a tiny fake source and install it through the window
            src=Path(tempfile.mkdtemp())
            for rel,t in (('studio/__init__.py',''),('studio/a.py','A=1\n'),('runtime/r.py','R=1\n'),('resources/x.json','{}')):(src/rel).parent.mkdir(parents=True,exist_ok=True);(src/rel).write_text(t)
            saved=(updater.source_dirs,updater.APP_VERSION);updater.source_dirs=lambda:{k:src/k for k in ('studio','runtime','resources')}
            try:
                updater.APP_VERSION='55.0.0';zf=updater.make_update_file(w.store,Path(tempfile.mkdtemp()),'hi');updater.APP_VERSION='54.0.0'
                w.confirm=lambda *a,**k:False       # decline "restart now"
                w.install_file(zf);s.assertIn('55.0.0',w.upd_status.text());s.assertFalse(w.upd_restart.isHidden());s.assertIn('55.0.0',w.upd_restart_text.text())
                avs_boot.apply_pending();w.refresh_updates_view()
            finally:updater.source_dirs,updater.APP_VERSION=saved
            # backups
            w.refresh_backup_list();s.assertTrue(hasattr(w,'bk_list'));w.bk_models.setChecked(True);w.on_backup_sizes({'models':3e9,'engines':1e9});s.assertIn('4.00 GB',w.bk_sizes.text())
            w.on_backup_sizes({'models':3e9,'engines':1e9}) 
            z=backup.create_backup('owner',False);w.refresh_backup_list();s.assertGreaterEqual(w.bk_list.count(),1);s.assertIn('owner',w.bk_list.item(0).text())
            w.bk_list.setCurrentRow(0);restarted=[];w.restart_app=lambda:restarted.append(1);w.confirm=lambda *a,**k:True;w.restore_selected()
            s.assertEqual(restarted,[1]);s.assertEqual(json.loads(avs_boot.pending_path().read_text())['action'],'restore')
        finally:
            w.close()
            if old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
            else:os.environ['AI_VOICE_STUDIO_HOME']=old

import threading,importlib
from studio import guard,services,core

class StopAndProtect(unittest.TestCase):
    def test_stop_kills_a_running_engine_part(s):
        errors=[];result={}
        def work():
            services._CTX.job='jobX'
            try:services.run_command([sys.executable,'-c','import time;time.sleep(60)'],lambda m:None,timeout=120)
            except BaseException as exc:errors.append(exc)
            finally:services._CTX.job=None
        t=threading.Thread(target=work);t.start();time.sleep(1.0);t0=time.time();services.stop_job('jobX');t.join(15)
        s.assertFalse(t.is_alive());s.assertIsInstance(errors[0],services.JobStopped);s.assertLess(time.time()-t0,8)
        services._ABORT.discard('jobX')
    def test_stopped_part_is_never_retried_and_never_an_error(s):
        calls=[]
        def fake(*a,**k):calls.append(1);raise services.JobStopped()
        saved=services.invoke_engine;services.invoke_engine=fake
        try:
            with s.assertRaises(services.JobStopped):services.render_section('kokoro','','hi there',Path(tempfile.mkdtemp())/'p.wav',{},'','',lambda m:None,3,True)
            s.assertEqual(len(calls),1);s.assertFalse(issubclass(services.JobStopped,RuntimeError))
        finally:services.invoke_engine=saved
    def test_kill_now_without_worker_is_safe(s):
        from studio import warm_worker
        warm_worker.kill_now()
    def test_guard_waits_for_a_cool_card_then_continues(s):
        class St:
            d={'guard_on':True,'guard_temp':80}
            def setting(self,k,v=None):return self.d.get(k,v)
        st=St();temps=iter([85,82,77,70]);logs=[];sleeps=[]
        check=lambda store,recovering:guard.reason(store,recovering,gpu=(next(temps),10),ram=40)
        s.assertTrue(guard.wait_until_safe(st,logs.append,lambda:False,sleeps.append,check=check))
        s.assertEqual(len(sleeps),3)           # 85 and 82 are too hot, 77 is not cool enough yet (needs 75 or less)... then 70 is fine
        s.assertTrue(any('hot' in l for l in logs) and any('safe again' in l for l in logs))
        s.assertEqual(guard.reason(st,False,gpu=(60,99),ram=10),'graphics memory is almost full');s.assertIn('computer memory',guard.reason(st,False,gpu=(60,10),ram=96))
        st.d['guard_on']=False;s.assertEqual(guard.reason(st,False,gpu=(99,99),ram=99),'')
    def test_guard_can_be_stopped_and_gives_up(s):
        class St:
            def setting(self,k,v=None):return {'guard_on':True,'guard_temp':80}.get(k,v)
        hot=lambda store,recovering:'the graphics card is hot (90°C)'
        s.assertFalse(guard.wait_until_safe(St(),lambda m:None,lambda:True,lambda x:None,check=hot))
        with s.assertRaisesRegex(guard.TooHot,'protect your PC'):guard.wait_until_safe(St(),lambda m:None,lambda:False,lambda x:None,max_wait=20,check=hot)
    def test_paragraph_groups_make_fewer_longer_parts(s):
        st=core.Store();paras=[f'Paragraph number {i} has a few words in it.' for i in range(1,11)];text='\n\n'.join(paras)
        def parts(n):
            j=st.new_job('g',text,'kokoro','af_heart','','',{'segment_mode':'group','group_size':n});return [x['body'] for x in st.sections(j)],json.loads(st.job(j)['options'])
        b2,o2=parts(2);b3,_=parts(3);b4,_=parts(4);b9,_=parts(9)
        s.assertEqual((len(b2),len(b3),len(b4)),(5,4,3));s.assertEqual(len(b9),3)       # group size is kept between 2 and 4
        s.assertEqual(b2[0],paras[0]+'\n\n'+paras[1]);s.assertTrue(o2['exact_parts']);s.assertEqual(' '.join(' '.join(b2).split()),' '.join(text.split()))
        j=st.new_job('g2','Line one here.\nLine two here.\nLine three here.\nLine four here.','kokoro','af_heart','','',{'segment_mode':'group','group_size':2});s.assertEqual(len(st.sections(j)),2)

class NewScreens(unittest.TestCase):
    def test_queue_toolbar_history_and_protection_settings(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        home=Path(tempfile.mkdtemp());old=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=str(home)
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            names=list(w.queue_buttons);s.assertEqual(names[:6],['import','generate','all','pause','stop','clear']);s.assertIn('clearall',names)
            s.assertFalse(hasattr(w,'queue_detail'));s.assertTrue(hasattr(w,'studio_stop'))
            j=w.store.new_job('Done job','A short script for the list.','kokoro','af_heart','','',{'segment_mode':'auto','made_seconds':62})
            f=w.job_facts(w.store.job(j));info=w.history_info({**f,'length':'6:31'},'completed');s.assertEqual(info,'Length 6:31  ·  Generated in 1:02')
            s.assertNotIn('part',w.history_info(f,'waiting').lower());s.assertEqual(w.clock(3725),'1:02:05');s.assertEqual(w.clock(75),'1:15')
            w.refresh_history();card=w.history_box.itemAt(0).widget();texts=' '.join(l.text() for l in card.findChildren(type(w.script_counts)))
            s.assertNotIn('words',texts);s.assertNotIn('parts',texts)
            w.refresh_queue();s.assertEqual(w.queue_table.rowCount(),1)
            w.selected_job=lambda:j;w.clear_selected();s.assertIsNone(w.store.job(j))
            w.stop_generation();   # nothing running: just a message
            # paragraph groups in the mode list
            s.assertIn('group',[w.segment_mode.itemData(i) for i in range(w.segment_mode.count())])
            w.segment_mode.setCurrentIndex(w.segment_mode.findData('group'));w.group_combo.setCurrentIndex(w.group_combo.findData(4))
            o=w.mode_options();s.assertEqual((o['segment_mode'],o['group_size'],o['exact_parts']),('group',4,True));s.assertFalse(w.group_combo.isHidden())
            w.segment_mode.setCurrentIndex(w.segment_mode.findData('auto'));s.assertTrue(w.group_combo.isHidden())
            # protection settings
            s.assertTrue(w.guard_check.isChecked() and w.breaker_check.isChecked());w.guard_temp.setValue(78);s.assertEqual(w.store.setting('guard_temp'),78)
            for _ in range(3):w.note_failure_streak('boom')
            s.assertEqual(w._fail_streak,0);w.note_failure_streak(None);s.assertEqual(w._fail_streak,0)
        finally:
            w.close()
            if old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
            else:os.environ['AI_VOICE_STUDIO_HOME']=old

from studio import speedup
class PartsAtOnce(unittest.TestCase):
    def test_choice_and_automatic(s):
        class St:
            def __init__(self,v):self.v=v
            def setting(self,k,d=None):return self.v if k=='parts_at_once' else d
        c=speedup.choose_parts
        s.assertEqual(c(St(1),'chatterbox','cuda',10),1);s.assertEqual(c(St(3),'chatterbox','cuda',10),3);s.assertEqual(c(St(9),'chatterbox','cuda',10),4);s.assertEqual(c(St(4),'chatterbox','cuda',2),2);s.assertEqual(c(St('x'),'kokoro','cpu',5),1)
        s.assertEqual(c(St('auto'),'chatterbox','cpu',9,lambda:40),1)                 # processor: no gain
        s.assertEqual(c(St('auto'),'chatterbox','cuda',9,lambda:None),1);s.assertEqual(c(St('auto'),'chatterbox','cuda',9,lambda:8),1)    # (8-1.5)//5
        s.assertEqual(c(St('auto'),'chatterbox','cuda',9,lambda:13),2);s.assertEqual(c(St('auto'),'chatterbox','cuda',9,lambda:40),4);s.assertEqual(c(St('auto'),'turbo','cuda',3,lambda:40),3)
    def test_parts_run_side_by_side_and_in_order(s):
        live=[0];peak=[0];lock=threading.Lock();slots=set()
        def work(section,slot,failed):
            with lock:live[0]+=1;peak[0]=max(peak[0],live[0]);slots.add(slot)
            time.sleep(0.15)
            with lock:live[0]-=1
            return f'done{section}'
        out=services.schedule_parts('jobP',list(range(8)),3,work);s.assertEqual(out,[f'done{i}' for i in range(8)]);s.assertEqual(peak[0],3);s.assertEqual(slots,{0,1,2})
        peak[0]=0;out=services.schedule_parts('jobP',list(range(4)),1,work);s.assertEqual(peak[0],1);s.assertEqual(slots,{0,1,2})
    def test_one_failure_stops_new_parts_and_is_raised(s):
        started=[]
        def work(section,slot,failed):
            if failed.is_set():return 'skip'
            started.append(section)
            if section==1:raise RuntimeError('boom')
            time.sleep(0.2);return 'done'
        with s.assertRaisesRegex(RuntimeError,'boom'):services.schedule_parts('jobQ',list(range(12)),2,work)
        s.assertLess(len(started),12)
    def test_pause_outcome_stops_a_sequential_run(s):
        seen=[]
        def work(section,slot,failed):
            seen.append(section);return 'paused' if section==2 else 'done'
        out=services.schedule_parts('jobR',[0,1,2,3,4],1,work);s.assertEqual(seen,[0,1,2]);s.assertIn('paused',out)
    def test_slots_keep_separate_engines(s):
        from studio import warm_worker
        a,b=warm_worker._slot(0),warm_worker._slot(1);s.assertIsNot(a,b);s.assertIsNot(a.lock,b.lock);warm_worker.release();warm_worker.kill_now()

class ProjectUpdates(IsolatedHome):
    FILES={'AVS_SOURCE_UPDATE.json':None,'main.py':'print(1)\n','Build-Windows.ps1':'#\n','requirements-gui.txt':'PySide6\n','avs_boot.py':'API = 1\n','studio/__init__.py':'','studio/core.py':'X=1\n','studio/ui_v3.py':'Y=1\n','installer/AI_Voice_Studio.iss':';\n','resources/catalog.json':'{}','runtime/runner.py':'R=1\n','runtime/adapters/a.py':'A=1\n'}
    def project(s,version='9.9.9',extra=None,api=1):
        d=Path(tempfile.mkdtemp())/'AI_Voice_Studio_Project'
        files=dict(s.FILES);files.update(extra or {});files['avs_boot.py']=f'API = {api}\n'
        for rel,text in files.items():
            f=d/rel;f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text(json.dumps({'format':'avs-source-update-v1','app':'AI Voice Studio','version':version}) if rel=='AVS_SOURCE_UPDATE.json' else text)
        return d
    def zipped(s,folder,prefix=True):
        z=Path(tempfile.mkdtemp())/'AI_Voice_Studio_COMPLETE_9_9_9.zip'
        with zipfile.ZipFile(z,'w') as zf:
            for f in sorted(folder.rglob('*')):
                if f.is_file():zf.write(f,(folder.name+'/' if prefix else '')+f.relative_to(folder).as_posix())
        return z
    def test_zip_and_folder_both_install(s):
        proj=s.project();updater.APP_VERSION='9.0.0'
        info=updater.stage_any(s.store,s.zipped(proj));s.assertEqual((info['version'],info['files']),('9.9.9',6))
        s.assertIn('Update installed',avs_boot.apply_pending());m=avs_boot.read_manifest(avs_boot.overlay_dir());s.assertEqual((m['version'],m['unsigned']),('9.9.9',True))
        s.assertTrue((avs_boot.overlay_dir()/'studio'/'__init__.py').is_file());s.assertTrue((avs_boot.overlay_dir()/'runtime'/'adapters'/'a.py').is_file());s.assertFalse((avs_boot.overlay_dir()/'main.py').exists())
        avs_boot.archive_current();updater.APP_VERSION='9.0.0'
        (proj/'BuildOut').mkdir();(proj/'BuildOut'/'setup.exe').write_bytes(b'x'*50);(proj/'.build-venv').mkdir();(proj/'.build-venv'/'junk.py').write_text('x')
        updater.stage_any(s.store,proj);avs_boot.apply_pending();s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'9.9.9')
        avs_boot.archive_current();updater.stage_any(s.store,proj.parent);avs_boot.apply_pending();s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'9.9.9')   # parent folder of the project works too
        avs_boot.archive_current();updater.stage_any(s.store,s.zipped(proj));avs_boot.apply_pending();s.assertEqual(avs_boot.read_manifest(avs_boot.overlay_dir())['version'],'9.9.9')   # a ZIP that also holds build leftovers
        avs_boot.archive_current();updater.stage_any(s.store,s.zipped(s.project(),False));avs_boot.apply_pending();s.assertTrue(avs_boot.overlay_dir().exists())   # ZIP without an outer folder
    def test_refusals_are_plain(s):
        updater.APP_VERSION='9.9.9'
        with s.assertRaisesRegex(updater.UpdateError,'already have'):updater.stage_any(s.store,s.zipped(s.project('9.9.9')))
        with s.assertRaisesRegex(updater.UpdateError,'already have|nothing to update'):updater.stage_any(s.store,s.zipped(s.project('9.0.0')))
        updater.APP_VERSION='9.0.0'
        with s.assertRaisesRegex(updater.UpdateError,'new setup file'):updater.stage_any(s.store,s.zipped(s.project(api=2)))
        with s.assertRaisesRegex(updater.UpdateError,'mistake'):updater.stage_any(s.store,s.zipped(s.project(extra={'studio/broken.py':'def (:\n'})))
        empty=Path(tempfile.mkdtemp())
        with s.assertRaisesRegex(updater.UpdateError,'not an AI Voice Studio update'):updater.stage_any(s.store,empty)
        junk=Path(tempfile.mkdtemp())/'a.zip';junk.write_bytes(b'nope')
        with s.assertRaisesRegex(updater.UpdateError,'not a valid ZIP'):updater.stage_any(s.store,junk)
        bad=Path(tempfile.mkdtemp())/'b.zip'
        with zipfile.ZipFile(bad,'w') as z:z.writestr('hello.txt','x')
        with s.assertRaisesRegex(updater.UpdateError,'AVS_SOURCE_UPDATE.json was not found'):updater.stage_any(s.store,bad)
        s.assertIsNone(updater.pending())
    def test_signed_file_still_routes_to_the_signed_path(s):
        zf=s.make('9.9.9');updater.APP_VERSION='9.0.0';info=updater.stage_any(s.store,zf);s.assertNotEqual(info['fingerprint'],'')
    def test_github_accepts_the_complete_zip_name(s):
        fetch=lambda url:{'tag_name':'v0.11.0','assets':[{'name':'AI_Voice_Studio_COMPLETE_0_11_0.zip','size':5,'browser_download_url':'https://github.com/o/r/releases/download/v0.11.0/x.zip'},{'name':'notes.txt','size':1,'browser_download_url':'https://github.com/o/r/a.txt'}]}
        r=updater.latest_release('o/r',fetch);s.assertEqual((r['version'],r['name']),('0.11.0','AI_Voice_Studio_COMPLETE_0_11_0.zip'))
        both=lambda url:{'tag_name':'v1','assets':[{'name':'AVS_Update_0.9.0.zip','size':1,'browser_download_url':'https://github.com/a'},{'name':'AI_Voice_Studio_UPDATE_0_9_5_speed.zip','size':1,'browser_download_url':'https://github.com/b'}]}
        s.assertEqual(updater.latest_release('o/r',both)['version'],'0.9.5')

from studio import samples, favorites, clones as clones_mod

def _wav(path,seconds=0.3):
    import wave,struct,math
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b''.join(struct.pack('<h',int(4000*math.sin(i/8))) for i in range(int(16000*seconds))))
    return path

class SavedSamples(unittest.TestCase):
    def setUp(s):
        s.old=os.environ.get('AI_VOICE_STUDIO_HOME');s.home=Path(tempfile.mkdtemp());os.environ['AI_VOICE_STUDIO_HOME']=str(s.home)
    def tearDown(s):
        if s.old is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
        else:os.environ['AI_VOICE_STUDIO_HOME']=s.old
    def test_one_folder_layout_and_safe_names(s):
        a=samples.voice_path('kokoro','af_heart');b=samples.clone_path('abc123')
        s.assertEqual(a,s.home/'samples'/'voices'/'kokoro'/'af_heart.wav');s.assertEqual(b,s.home/'samples'/'clones'/'abc123.wav')
        s.assertEqual(samples.voice_path('kokoro','../../evil').parent,s.home/'samples'/'voices'/'kokoro');s.assertNotIn('..',samples.voice_path('x','a/b\\c').name)
        s.assertEqual(samples.row_path({'kind':'clone','voice':'abc123','model':'chatterbox'}),b);s.assertEqual(samples.row_path({'kind':'tts','voice':'af_heart','model':'kokoro'}),a)
    def test_save_is_atomic_and_rejects_bad_audio(s):
        good=_wav(Path(tempfile.mkdtemp())/'g.wav');dest=samples.save(good,samples.voice_path('kokoro','v'));s.assertTrue(samples.has(dest));s.assertFalse(list(dest.parent.glob('*.saving.wav')))
        bad=Path(tempfile.mkdtemp())/'b.wav';bad.write_bytes(b'not audio')
        with s.assertRaises(RuntimeError):samples.save(bad,samples.voice_path('kokoro','w'))
        s.assertFalse(samples.has(samples.voice_path('kokoro','w')));s.assertFalse(list(dest.parent.glob('*.saving.wav')))
    def test_build_uses_the_right_voice_and_clone_sample(s):
        calls=[];saved=preview_mod.make_preview
        def fake(store,model,voice,reference,ref_text,options,**k):
            calls.append((model,voice,reference,ref_text,k.get('language')));return _wav(Path(tempfile.mkdtemp())/'m.wav')
        preview_mod.make_preview=fake
        try:
            class St:
                d={}
                def setting(self,k,v=None):return self.d.get(k,v)
                def set_setting(self,k,v):self.d[k]=v
            st=St();row={'kind':'tts','model':'kokoro','voice':'jf_alpha','name':'Alpha','language':'Japanese'}
            p=samples.build(st,row);s.assertTrue(samples.has(p));s.assertEqual(calls[-1],('kokoro','jf_alpha','','','Japanese'))
            ref=_wav(Path(tempfile.mkdtemp())/'r.wav',4);rec=clones_mod.save_profile(st,'Mine','chatterbox',ref,'',True,{})
            crow={'kind':'clone','model':'chatterbox','voice':rec['id'],'name':'Mine','language':'Cloned'}
            p2=samples.build(st,crow);s.assertEqual(p2,samples.clone_path(rec['id']));s.assertEqual(calls[-1][1:4],('',rec['reference'],''))
            rec2=clones_mod.save_profile(st,'Words','f5',ref,'Some words.',True,{})
            samples.build(st,{'kind':'clone','model':'f5','voice':rec2['id'],'name':'Words','language':'Cloned'});s.assertEqual(calls[-1][3],'Some words.')
            clones_mod.save_profile(st,'NoWords','f5',ref,'x',True,{});st.d['clone_profiles']=[{**q,'transcript':''} if q['name']=='NoWords' else q for q in st.d['clone_profiles']]
            nw=[q for q in st.d['clone_profiles'] if q['name']=='NoWords'][0]
            with s.assertRaisesRegex(ValueError,'words spoken'):samples.build(st,{'kind':'clone','model':'f5','voice':nw['id'],'name':'NoWords','language':'Cloned'})
            with s.assertRaises(ValueError):samples.build(st,{'kind':'clone','model':'chatterbox','voice':'gone','name':'x','language':''})
            samples.remove_clone(rec['id']);s.assertFalse(samples.has(samples.clone_path(rec['id'])));s.assertGreaterEqual(samples.clear_all(),1)
        finally:preview_mod.make_preview=saved
    def test_library_plays_saved_samples_instantly_and_builds_in_background(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            row={'kind':'tts','model':'kokoro','voice':'af_heart','name':'Heart · American English · Female','language':'American English','ready':False,'favorite':False,'family':'English','gender':'Female','model_name':'Kokoro 82M · ONNX'}
            # a voice whose model is not installed but has a saved sample still plays
            _wav(samples.row_path(row));card=w.voice_card(row);buttons=card.findChildren(type(w.preview_button));listen=[b for b in buttons if b.text()=='▶'][0]
            s.assertTrue(listen.isEnabled());s.assertNotIn('Needs setup',' '.join(l.text() for l in card.findChildren(type(w.script_counts))))
            w.start_preview=lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not generate'))
            w.library_listen(row);s.assertTrue(w.library_player.path==samples.row_path(row))
            gone={**row,'voice':'am_adam','name':'Adam','ready':False};card2=w.voice_card(gone);l2=[b for b in card2.findChildren(type(w.preview_button)) if b.text()=='▶'][0];s.assertFalse(l2.isEnabled())
            # on-demand result is kept for next time
            asked=[];w.start_preview=lambda *a,**k:asked.append(a)
            ready={**row,'voice':'bf_emma','name':'Emma','ready':True};w.library_listen(ready);s.assertEqual(asked[0][:2],('kokoro','bf_emma'));s.assertIs(w._library_row,ready)
            made=_wav(Path(tempfile.mkdtemp())/'m.wav');w.on_preview_ready(str(made),'library');s.assertTrue(samples.has(samples.row_path(ready)));s.assertIsNone(w._library_row)
            # background scan: builds only what is missing for ready voices, never twice, skips failures, steps aside when busy
            rows=[{'kind':'tts','model':'kokoro','voice':v,'name':v,'language':'English','ready':True,'favorite':False,'family':'English','gender':'','model_name':'Kokoro 82M'} for v in ('v1','v2','v3')]
            _wav(samples.row_path(rows[0]));built=[]
            saved_all,saved_build=favorites.all_voices,samples.build
            favorites.all_voices=lambda store,ready_fn=None:rows
            def fake_build(store,r,device='cpu',log=None):
                if r['voice']=='v3':raise RuntimeError('no')
                built.append(r['voice']);return _wav(samples.row_path(r))
            samples.build=fake_build
            try:
                w.store.set_setting('auto_samples',False);w.scan_samples();s.assertEqual(built,[])
                w.store.set_setting('auto_samples',True);w.store.set_setting('low_end_mode',True);w.scan_samples();s.assertEqual(built,[]);w.store.set_setting('low_end_mode',False)
                w.running['busy']=1;w.scan_samples();s.assertEqual(built,[]);w.running.clear()
                w.scan_samples()
                for _ in range(100):
                    app.processEvents();time.sleep(0.05)
                    if not w.sample_building:break
                s.assertEqual(built,['v2']);s.assertIn(('kokoro','v3'),w._sample_failed);built.clear();w.scan_samples();time.sleep(0.2);app.processEvents();s.assertEqual(built,[])
            finally:favorites.all_voices,samples.build=saved_all,saved_build
            # clone card plays the saved sample first, and deleting the voice deletes the sample
            ref=_wav(Path(tempfile.mkdtemp())/'r.wav',5);rec=clones_mod.save_profile(w.store,'CloneS','chatterbox',ref,'',True,{})
            w.scan_samples=lambda:None;w.play_clone(rec);s.assertEqual(w.clone_player.path,Path(rec['reference']))
            _wav(samples.clone_path(rec['id']));w.play_clone(rec);s.assertEqual(w.clone_player.path,samples.clone_path(rec['id']))
            w.confirm=lambda *a,**k:True;w.remove_clone_card(rec);s.assertFalse(samples.has(samples.clone_path(rec['id'])))
            s.assertTrue(w.samples_check.isChecked())
        finally:w.close()
from studio import preview as preview_mod

from studio import owner as owner_mod, permits, modelfind

class OwnerKey(unittest.TestCase):
    def setUp(s):
        s.dir=Path(tempfile.mkdtemp());s.path=s.dir/'access.json'
        s.secret=ed25519.generate_secret();s.pub=ed25519.public_key(s.secret).hex();s.key=owner_mod.encode_key(s.secret)
        s.real_machine=owner_mod.machine_id
    def tearDown(s):owner_mod.machine_id=s.real_machine
    def test_key_format(s):
        s.assertTrue(s.key.startswith('AVS-'));s.assertEqual(owner_mod.decode_key(s.key),s.secret);s.assertEqual(owner_mod.decode_key(s.key.lower().replace('-','  ')),s.secret)
        bad=s.key[:10]+('A' if s.key[10]!='A' else 'B')+s.key[11:]
        for wrong in (bad,'AVS-ABC','',s.key[:-5],'hello world',s.key+'AAAAA'):
            with s.assertRaises(ValueError):owner_mod.decode_key(wrong)
        for _ in range(20):
            k=ed25519.generate_secret();s.assertEqual(owner_mod.decode_key(owner_mod.encode_key(k)),k)
    def test_copies_start_locked_and_only_the_key_unlocks(s):
        a=owner_mod.Access(s.path,s.pub);s.assertTrue(a.key_mode);s.assertFalse(a.is_owner);s.assertEqual(a.state('page.sources'),owner_mod.HIDE);s.assertEqual(a.state('access.manager'),owner_mod.HIDE)
        for wrong in ('',owner_mod.encode_key(ed25519.generate_secret()),'AVS-nonsense'):s.assertFalse(a.enter_owner(wrong));s.assertFalse(a.is_owner)
        s.assertTrue(a.enter_owner(s.key));s.assertTrue(a.is_owner);s.assertEqual(a.owner_secret,s.secret);s.assertEqual(a.state('page.sources'),owner_mod.SHOW);s.assertEqual(a.state('set.backup'),owner_mod.SHOW)
        s.assertNotIn(s.key.replace('-','')[3:20],s.path.with_name('owner_proof.json').read_text())          # the key itself is never stored
    def test_unlock_is_remembered_on_this_pc_only(s):
        a=owner_mod.Access(s.path,s.pub);a.enter_owner(s.key)
        s.assertTrue(owner_mod.Access(s.path,s.pub).is_owner)                           # next start: still the owner
        owner_mod.machine_id=lambda:'another-pc-0000000000000000000000';s.assertFalse(owner_mod.Access(s.path,s.pub).is_owner)    # note copied to another PC
        owner_mod.machine_id=s.real_machine
        proof=s.path.with_name('owner_proof.json');data=json.loads(proof.read_text());data['signature']='00'*64;proof.write_text(json.dumps(data));s.assertFalse(owner_mod.Access(s.path,s.pub).is_owner)
        proof.write_text('{bad');s.assertFalse(owner_mod.Access(s.path,s.pub).is_owner)
        b=owner_mod.Access(s.path,s.pub);b.enter_owner(s.key);b.lock_this_copy();s.assertFalse(b.is_owner);s.assertFalse(proof.exists());s.assertFalse(owner_mod.Access(s.path,s.pub).is_owner)
        other=owner_mod.Access(s.dir/'x'/'access.json',ed25519.public_key(ed25519.generate_secret()).hex());s.assertFalse(other.enter_owner(s.key))   # a different owner's software
    def test_permits_let_a_copy_make_backups(s):
        a=owner_mod.Access(s.path,s.pub);s.assertFalse(a.backup_allowed());s.assertEqual(a.state('set.backup'),owner_mod.HIDE)
        permit=permits.make(s.secret,'Ali');s.assertTrue(permits.verify(permit,s.pub));permits.save(permit,s.dir/'backup_permit.json')
        s.assertTrue(a.backup_allowed());s.assertEqual(a.state('set.backup'),owner_mod.SHOW);s.assertEqual(a.state('access.manager'),owner_mod.HIDE)    # backups yes, owner controls no
        forged=permits.make(ed25519.generate_secret(),'Mallory');permits.save(forged,s.dir/'backup_permit.json');s.assertFalse(a.backup_allowed())
        tampered=json.loads(json.dumps(permit));tampered['token']['label']='Someone else';s.assertFalse(permits.verify(tampered,s.pub))
        wrong=json.loads(json.dumps(permit));wrong['token']['perm']='owner';s.assertFalse(permits.verify(wrong,s.pub))
        for junk in (None,{},{'token':1},{'token':{},'signature':'zz'}):s.assertFalse(permits.verify(junk,s.pub))
    def test_software_without_a_key_keeps_the_old_rules(s):
        a=owner_mod.Access(s.path,'');s.assertFalse(a.key_mode);s.assertTrue(a.is_owner)
    def test_shipped_public_key_is_valid(s):
        data=json.loads((ROOT/'resources'/'owner_key.json').read_text());s.assertEqual(len(bytes.fromhex(data['public_key'])),32);s.assertNotIn('secret',json.dumps(data).lower().replace('public_key',''))
        old=os.environ.get('AVS_OWNER_KEY_FILE');os.environ['AVS_OWNER_KEY_FILE']=str(ROOT/'resources'/'owner_key.json')
        try:s.assertEqual(owner_mod.embedded_public(),data['public_key'])
        finally:os.environ['AVS_OWNER_KEY_FILE']=old
        os.environ['AVS_OWNER_KEY_FILE']='none';s.assertEqual(owner_mod.embedded_public(),'')

class CompleteBackup(unittest.TestCase):
    def setUp(s):
        s.data=Path(tempfile.mkdtemp());s.out=Path(tempfile.mkdtemp());s.installer=s.out/'AI_Voice_Studio_Setup_9.9.9.exe';s.installer.write_bytes(b'MZ'+b'x'*500)
        for d,f in (('models','m.onnx'),('wheels','a.whl'),('env','e.txt'),('clones','v.wav'),('config','access.json')):(s.data/d).mkdir();(s.data/d/f).write_bytes(b'data')
        (s.data/'config'/'update_signing.key').write_text('secret');(s.data/'config'/'owner_proof.json').write_text('{}');(s.data/'config'/'backup_permit.json').write_text('{"old":1}')
        (s.data/'state').mkdir();c=sqlite3.connect(s.data/'state'/'studio.sqlite3');c.executescript("CREATE TABLE jobs(id,script);CREATE TABLE sections(job_id,body);CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT);INSERT INTO jobs VALUES('1','private');INSERT INTO kv VALUES('clone_profiles','[1]')");c.commit();c.close()
    def test_complete_pack_with_setup_file_and_models(s):
        permit={'token':{'v':1},'signature':'ab'}
        z=backup.create_backup('complete',True,data=s.data,dest=s.out,installer=s.installer,permit=permit);zf=zipfile.ZipFile(z);n=set(zf.namelist())
        for want in ('Install_Everything.bat','Install-Everything.ps1','README.txt','Setup/AI_Voice_Studio_Setup_9.9.9.exe','Setup/SHA256.txt','Data/models/m.onnx','Data/wheels/a.whl','Data/config/access.json','Data/config/backup_permit.json','Data/state/studio.sqlite3'):s.assertIn(want,n)
        for no in ('Data/clones/v.wav','Data/config/update_signing.key','Data/config/owner_proof.json','Data/env/e.txt'):s.assertNotIn(no,n)
        s.assertEqual(json.loads(zf.read('Data/config/backup_permit.json')),permit);s.assertFalse([x for x in n if x.startswith('Software/')])
        digest=zf.read('Setup/SHA256.txt').decode().split()[0];s.assertEqual(digest,backup.file_sha256(s.installer))
        ps=zf.read('Install-Everything.ps1').decode('utf-8-sig');s.assertIn('Get-FileHash',ps);s.assertIn('$PSScriptRoot' if False else 'Split-Path -Parent $MyInvocation.MyCommand.Path',ps);s.assertIn('robocopy',ps);s.assertNotIn("\\'",ps);s.assertIn('/SILENT',ps)
        m=json.loads(zf.read('AVS_BACKUP.json'));s.assertEqual((m['kind'],m['installer'],m['models']),('complete','AI_Voice_Studio_Setup_9.9.9.exe',True))
        db=Path(tempfile.mkdtemp());zf.extract('Data/state/studio.sqlite3',db);c=sqlite3.connect(db/'Data/state/studio.sqlite3');s.assertEqual(c.execute('select count(*) from jobs').fetchone()[0],0);s.assertEqual(c.execute("select count(*) from kv where key='clone_profiles'").fetchone()[0],0);c.close()
        z2=backup.create_backup('complete',False,data=s.data,dest=s.out,installer=s.installer);n2=s.names=set(zipfile.ZipFile(z2).namelist());s.assertNotIn('Data/models/m.onnx',n2);s.assertNotIn('Data/config/backup_permit.json',n2)
    def test_needs_the_setup_file_and_labels(s):
        for bad in (None,s.out/'missing.exe'):
            with s.assertRaisesRegex(ValueError,'setup file'):backup.create_backup('complete',data=s.data,dest=s.out,installer=bad)
        s.assertEqual(backup.LABELS['complete'],'with setup');rows=backup.list_backups(s.out);s.assertEqual(rows,[])
        z=backup.create_backup('complete',data=s.data,dest=s.out,installer=s.installer);s.assertEqual(backup.list_backups(s.out)[0]['label'],'with setup')
        s.assertGreater(backup.plan_backup('complete',False,data=s.data,installer=s.installer),500)
    def test_owner_and_others_packs_never_carry_private_files_to_others(s):
        sw=Path(tempfile.mkdtemp());(sw/'app.exe').write_bytes(b'x')
        z=backup.create_backup('others',False,software=sw,data=s.data,dest=s.out,permit={'token':{},'signature':'1'});n=set(zipfile.ZipFile(z).namelist())
        for no in ('Data/config/update_signing.key','Data/config/owner_proof.json','Data/clones/v.wav'):s.assertNotIn(no,n)
        s.assertIn('Data/config/backup_permit.json',n)
        own=set(zipfile.ZipFile(backup.create_backup('owner',False,data=s.data,dest=s.out)).namelist());s.assertIn('Data/config/update_signing.key',own);s.assertIn('Data/clones/v.wav',own)
    def test_find_installers(s):
        home=Path(tempfile.mkdtemp());(home/'Downloads'/'sub').mkdir(parents=True)
        a=home/'Downloads'/'AI_Voice_Studio_Setup_1.0.0.exe';b=home/'Downloads'/'sub'/'AI Voice Studio by Unzip Spark Setup.exe';a.write_bytes(b'x');b.write_bytes(b'y');os.utime(a,(1,1))
        old=Path.home;Path.home=staticmethod(lambda:home)
        try:found=backup.find_installers()
        finally:Path.home=old
        s.assertEqual(found,[b,a])

class FindModels(unittest.TestCase):
    def test_detect_and_connect(s):
        drive=Path(tempfile.mkdtemp());pack=drive/'MyBackup'/'Data'
        for rel,size in (('models/kokoro/m.onnx',50),('models/kokoro/v.bin',20),('wheels/a.whl',30),('hf_cache/x/y.bin',10)):
            f=pack/rel;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(b'x'*size)
        (pack/'clones').mkdir();(pack/'clones'/'private.wav').write_bytes(b'p')
        s.assertEqual(modelfind.looks_like_data(pack),['models','wheels','hf_cache']);s.assertEqual(modelfind.looks_like_data(drive),[])
        home=Path(tempfile.mkdtemp());target=Path(tempfile.mkdtemp());prior=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=str(target);s.addCleanup(lambda:os.environ.__setitem__('AI_VOICE_STUDIO_HOME',prior) if prior else os.environ.pop('AI_VOICE_STUDIO_HOME',None))
        found=[c['path'] for c in modelfind.candidates(extra=[drive])];s.assertIn(pack.resolve(),found)
        s.assertFalse([p for p in found if target.resolve() in p.parents or p==target.resolve()])   # this PC's own data folder is not offered
        s.assertEqual(modelfind.plan(pack),{'files':4,'bytes':110});got=[];n=modelfind.connect(pack,lambda f,name:got.append(name))
        s.assertEqual(n,4);s.assertEqual((target/'models'/'kokoro'/'m.onnx').stat().st_size,50);s.assertFalse((target/'clones').exists());s.assertTrue(got)
        s.assertEqual(modelfind.connect(pack),0);s.assertEqual(modelfind.plan(pack)['files'],0)                       # nothing twice
        (target/'models'/'kokoro'/'v.bin').write_bytes(b'bad');s.assertEqual(modelfind.connect(pack),1)             # a wrong-sized file is replaced
        ev=threading.Event();ev.set();(target/'wheels'/'a.whl').unlink()
        with s.assertRaises(modelfind.Cancelled):modelfind.connect(pack,cancel=ev)
        s.assertFalse(list(target.rglob('*.copying')))

class OwnerWindow(unittest.TestCase):
    """The window with the owner key: copies start locked, the Backup tab holds the owner controls, backups need the key or a permit."""
    def test_key_login_backup_tab_and_permit_flow(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        secret=ed25519.generate_secret();pub=ed25519.public_key(secret).hex();keyfile=Path(tempfile.mkdtemp())/'owner_key.json';keyfile.write_text(json.dumps({'public_key':pub}))
        home=Path(tempfile.mkdtemp());saved_env={k:os.environ.get(k) for k in ('AI_VOICE_STUDIO_HOME','AVS_OWNER_KEY_FILE')}
        os.environ['AI_VOICE_STUDIO_HOME']=str(home);os.environ['AVS_OWNER_KEY_FILE']=str(keyfile)
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            s.assertTrue(w.access.key_mode);s.assertFalse(w.access.is_owner);s.assertEqual(w.settings_tabs.count(),5);s.assertNotIn('Owner',[w.settings_tabs.tabText(i) for i in range(5)])
            s.assertFalse(w.settings_tabs.isTabVisible(2));s.assertIn('Owner login',w.owner_button.text())
            # wrong then right key
            w.ask_password=lambda *a,**k:'AVS-WRONG';warned=[];import PySide6.QtWidgets as W;orig=W.QMessageBox.warning;W.QMessageBox.warning=staticmethod(lambda *a,**k:warned.append(a[2]))
            try:
                w.toggle_owner_mode();s.assertFalse(w.access.is_owner);s.assertTrue(warned)
                w.ask_password=lambda *a,**k:owner_mod.encode_key(secret);w.toggle_owner_mode()
            finally:W.QMessageBox.warning=orig
            s.assertTrue(w.access.is_owner);s.assertTrue(w.settings_tabs.isTabVisible(2));s.assertIn('Lock',w.owner_button.text());s.assertIn('Owner key active',w.access_status.text());s.assertEqual(w.bk_owner.text(),'Owner backup')
            s.assertFalse(w.bk_others.isHidden());s.assertFalse(w.bk_complete.isHidden());s.assertFalse(w.owner_card.isHidden());s.assertIn('not chosen',w.installer_note.text())
            # the owner restarts the software: still the owner on this PC
            s.assertTrue(owner_mod.Access(w.access.path,pub).is_owner)
            # lock: others-only buttons disappear and nothing but a permit brings the Backup tab back
            w.confirm=lambda *a,**k:True;w.toggle_owner_mode();s.assertFalse(w.access.is_owner);s.assertFalse(w.settings_tabs.isTabVisible(2));s.assertTrue(w.bk_others.isHidden())
            w.run_backup('others');s.assertFalse(w.pack_busy)         # refused for non-owners
            permits.save(permits.make(secret,'Ali'),w.access.path.with_name(permits.NAME));w.apply_access()
            s.assertTrue(w.settings_tabs.isTabVisible(2));s.assertEqual(w.bk_owner.text(),'Backup');s.assertTrue(w.bk_others.isHidden());s.assertTrue(w.bk_complete.isHidden());s.assertTrue(w.owner_card.isHidden())
            s.assertTrue(w.access.backup_allowed());s.assertFalse(w.access.is_owner)
            # complete backup asks for the setup file
            w.access.login_with_key(owner_mod.encode_key(secret));w.apply_access()
            fake=Path(tempfile.mkdtemp())/'AI_Voice_Studio_Setup_1.exe';fake.write_bytes(b'MZ'+b'x'*100);w.store.set_setting('installer_path',str(fake));s.assertEqual(w.pick_installer(),fake)
            w.refresh_owner_card();s.assertIn(fake.name,w.installer_note.text())
            # permit question: "No" means no permit; with the key held in memory "Yes" signs one
            import PySide6.QtWidgets as W2
            class Box:
                answer='yes'
                warning=staticmethod(lambda *a,**k:None)
                def __init__(self,*a,**k):self.buttons={}
                def setWindowTitle(self,*a):pass
                def setText(self,*a):pass
                def setInformativeText(self,*a):pass
                def addButton(self,text,role=None):b=object();self.buttons[text.split(',')[0]]=b;return b
                def exec(self):pass
                def clickedButton(self):return self.buttons[{'yes':'Yes','no':'No','cancel':'Cancel'}[Box.answer]]
            origbox=W2.QMessageBox;import studio.ui_updates as UU;oldbox=UU.QMessageBox;UU.QMessageBox=Box;UU.QMessageBox.ButtonRole=origbox.ButtonRole
            oldin=UU.QInputDialog;UU.QInputDialog=type('QI',(),{'getText':staticmethod(lambda *a,**k:('Sara',True))})
            try:
                Box.answer='no';s.assertIsNone(w.ask_permit());Box.answer='cancel';s.assertIs(w.ask_permit(),False)
                Box.answer='yes';p=w.ask_permit();s.assertTrue(permits.verify(p,pub));s.assertEqual(p['token']['label'],'Sara')
                w.access.owner_secret=None;w.ask_password=lambda *a,**k:owner_mod.encode_key(ed25519.generate_secret());s.assertIs(w.ask_permit(),False)      # a wrong key signs nothing
                w.ask_password=lambda *a,**k:owner_mod.encode_key(secret);s.assertTrue(permits.verify(w.ask_permit(),pub))
            finally:UU.QMessageBox=oldbox;UU.QInputDialog=oldin
        finally:
            w.close()
            for k,v in saved_env.items():
                if v is None:os.environ.pop(k,None)
                else:os.environ[k]=v
from studio import owner as owner_mod

from studio import channels as ch_mod
class ChannelData(unittest.TestCase):
    def setUp(s):
        s.file=Path(tempfile.mkdtemp())/'state'/'channels.json';s.c=ch_mod.Channels(s.file)
    def test_channels_crud_and_persistence(s):
        a=s.c.add_channel('Tech Explainer','Tech, AI','About tools','📺');b=s.c.add_channel('History','History');s.assertEqual(s.c.active()['id'],a['id'])
        with s.assertRaisesRegex(ValueError,'name'):s.c.add_channel('  ')
        with s.assertRaisesRegex(ValueError,'already exists'):s.c.add_channel('tech explainer')
        s.c.set_active(b['id']);s.c.update_channel(b['id'],name='History & Documentary',master_prompt='Be neutral.',master_on=False,voice_direction='Calm')
        with s.assertRaises(ValueError):s.c.update_channel(b['id'],evil=1)
        with s.assertRaisesRegex(ValueError,'already exists'):s.c.update_channel(b['id'],name='Tech Explainer')
        again=ch_mod.Channels(s.file);s.assertEqual([c['name'] for c in again.all()],['Tech Explainer','History & Documentary']);s.assertEqual(again.active()['id'],b['id']);s.assertFalse(again.channel(b['id'])['master_on'])
        d=s.c.duplicate_channel(a['id']);s.assertEqual(d['name'],'Tech Explainer copy');s.assertNotEqual(d['id'],a['id']);s.assertEqual(s.c.duplicate_channel(a['id'])['name'],'Tech Explainer copy 2')
        s.c.delete_channel(b['id']);s.assertNotEqual(s.c.active()['id'],b['id']);
        with s.assertRaises(ValueError):s.c.delete_channel('nope')
        s.file.write_text('{broken');s.assertEqual(ch_mod.Channels(s.file).all(),[])
        s.assertFalse(list(s.file.parent.glob('*.tmp')))
    def test_scripts(s):
        a=s.c.add_channel('A');x=s.c.add_script(a['id'],'One','First paragraph here.\n\nSecond paragraph here.');y=s.c.add_script(a['id'],'Two','Another script.');s.assertEqual(x['status'],'Draft')
        s.c.update_script(a['id'],x['id'],title='Uno',status='Ready',notes='n');s.assertEqual(s.c.script(a['id'],x['id'])['status'],'Ready')
        with s.assertRaises(ValueError):s.c.update_script(a['id'],x['id'],bad=1)
        with s.assertRaises(ValueError):s.c.update_script(a['id'],'zzz',title='x')
        d=s.c.duplicate_script(a['id'],x['id']);s.assertEqual((d['title'],d['status']),('Uno (copy)','Draft'))
        m=s.c.merge_scripts(a['id'],[x['id'],y['id']]);s.assertIn('First paragraph here.\n\nSecond paragraph here.\n\nAnother script.',m['text'])
        with s.assertRaises(ValueError):s.c.merge_scripts(a['id'],[x['id']])
        big=s.c.add_script(a['id'],'Big','\n\n'.join(' '.join(f'word{i}x{j}' for j in range(60)) for i in range(6)));parts=s.c.split_script(a['id'],big['id'],130)
        s.assertEqual(len(parts),3);s.assertTrue(all(ch_mod.word_count(p['text'])<=130 for p in parts));s.assertEqual([p['title'] for p in parts],['Big (part 1)','Big (part 2)','Big (part 3)'])
        s.assertEqual(' '.join(' '.join(p['text'] for p in parts).split()),' '.join(big['text'].split()))
        with s.assertRaisesRegex(ValueError,'shorter'):s.c.split_script(a['id'],y['id'],300)
        s.c.delete_script(a['id'],x['id']);s.assertIsNone(s.c.script(a['id'],x['id']))
    def test_split_helper_handles_long_paragraphs_and_sentences(s):
        text=' '.join(f'Sentence {i} has exactly seven words in it.' for i in range(40));pieces=ch_mod.split_by_length(text,50)
        s.assertTrue(all(ch_mod.word_count(p)<=50 for p in pieces));s.assertEqual(' '.join(' '.join(pieces).split()),text)
        s.assertEqual(ch_mod.split_by_length('x '*500,100)[0].count('x'),100);s.assertEqual(ch_mod.split_by_length('',100),[])
        s.assertEqual(ch_mod.duration_seconds(' '.join(['w']*150)),60);s.assertEqual(ch_mod.clock(138),'02:18');s.assertEqual(ch_mod.word_count('a  b\nc'),3)
    def test_prompts_apply_templates(s):
        a=s.c.add_channel('A');p1=s.c.add_prompt(a['id'],'script','Long','text 1');p2=s.c.add_prompt(a['id'],'script','Short','text 2');v=s.c.add_prompt(a['id'],'video','V','vid')
        s.assertTrue(p1['applied'] and not p2['applied'] and v['applied']);s.c.apply_prompt(a['id'],p2['id']);s.assertEqual(s.c.applied_prompt(a['id'],'script')['id'],p2['id']);s.assertEqual(s.c.applied_prompt(a['id'],'video')['id'],v['id'])
        s.c.delete_prompt(a['id'],p2['id']);s.assertEqual(s.c.applied_prompt(a['id'],'script')['id'],p1['id']);s.assertIsNone(s.c.applied_prompt(a['id'],'image'))
        t=s.c.add_from_template(a['id'],'t-image');s.assertEqual(t['kind'],'image');s.assertIn('thumbnail',t['text'])
        with s.assertRaises(ValueError):s.c.add_prompt(a['id'],'weird','x')
        with s.assertRaises(ValueError):s.c.add_from_template(a['id'],'nope')
        s.c.update_prompt(a['id'],p1['id'],text='changed');s.assertEqual([p for p in s.c.channel(a['id'])['prompts'] if p['id']==p1['id']][0]['text'],'changed')
        s.assertEqual({t['kind'] for t in ch_mod.TEMPLATES},{'script','video','image'})
    def test_voices_and_stats(s):
        a=s.c.add_channel('A');s.assertIsNone(s.c.main_voice(a['id']))
        s.c.assign_voice(a['id'],'chatterbox','v1','Wise','alt');s.assertEqual(s.c.main_voice(a['id'])['voice'],'v1')       # the first voice is always the main one
        s.c.assign_voice(a['id'],'kokoro','v2','Calm','alt');s.c.assign_voice(a['id'],'kokoro','v3','Warm','main');s.assertEqual(s.c.main_voice(a['id'])['voice'],'v3');s.assertEqual([v['role'] for v in s.c.channel(a['id'])['voices']].count('main'),1)
        s.c.assign_voice(a['id'],'kokoro','v3','Warm renamed','main');s.assertEqual(len(s.c.channel(a['id'])['voices']),3)
        s.c.remove_voice(a['id'],'kokoro','v3');s.assertEqual(sum(v['role']=='main' for v in s.c.channel(a['id'])['voices']),1)
        for i in range(8):
            try:s.c.assign_voice(a['id'],'kokoro',f'x{i}','n','alt')
            except ValueError as exc:s.assertIn('up to 6',str(exc));break
        else:s.fail('limit not enforced')
        with s.assertRaises(ValueError):s.c.assign_voice(a['id'],'kokoro','q','n','boss')
        s.c.add_script(a['id'],'t','x');s.assertEqual(s.c.stats(a['id'],voice_overs=3),{'scripts':1,'voice_overs':3,'prompts':0,'voices':len(s.c.channel(a['id'])['voices'])})

class ChannelWindow(unittest.TestCase):
    def test_channels_page_flow(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        home=Path(tempfile.mkdtemp());prior=os.environ.get('AI_VOICE_STUDIO_HOME');os.environ['AI_VOICE_STUDIO_HOME']=str(home)
        from studio.ui_v5 import StudioWindow
        from studio import managed
        w=StudioWindow()
        try:
            s.assertEqual(w.stack.count(),11);s.assertIn('Channels',w.nav_buttons[10].text());s.assertEqual([t.text() for t in w.ch_tabs],['Channel Manager','Scripts','Prompts','Voice Overs','Batch Queue'])
            s.assertEqual(w.channel_data.all(),[]);w.change_page(10);s.assertEqual(w.stack.currentIndex(),10)
            c=w.channel_data.add_channel('History & Documentary','History, Civilizations','Real stories','🏛');w.refresh_channels()
            s.assertEqual(w.ch_combo.count(),1);s.assertEqual(w.ch_name.text(),'History & Documentary');s.assertEqual(w.ch_cards_flow.count(),1)
            w.ch_name.setText('History');w.ch_status.setCurrentText('Paused');w.ch_save_details();s.assertEqual(w.channel_data.active()['name'],'History');s.assertEqual(w.channel_data.active()['status'],'Paused')
            w.ch_master.setPlainText('Be neutral and clear.');w.ch_master_on.setChecked(False);w.ch_save_master();s.assertEqual((w.channel_data.active()['master_prompt'],w.channel_data.active()['master_on']),('Be neutral and clear.',False))
            # scripts
            w.ch_new_script();s.assertEqual(w.ch_table.rowCount(),1);s.assertEqual(len(w.ch_ids()),1)
            w.ch_title.setText('The Lost City');w.ch_text.setPlainText('A short script about a lost city. '*20);w.ch_save_script(quiet=True)
            sc=w.channel_data.active()['scripts'][0];s.assertEqual(sc['title'],'The Lost City');s.assertIn('words',w.ch_counts.text());s.assertIn('Est. duration',w.ch_counts.text())
            w.ch_dup_script();s.assertEqual(w.ch_table.rowCount(),2)
            w.ch_table.selectAll();w.confirm=lambda *a,**k:True;w.ch_merge();s.assertEqual(w.ch_table.rowCount(),3)
            w.ch_table.clearSelection();w.ch_table.selectRow(0);w.ch_del_script();s.assertEqual(len(w.channel_data.active()['scripts']),2)
            # voices
            w.select_voice('kokoro','af_heart');w.ch_add_voice('main');s.assertEqual(w.ch_voice_list.count(),1);s.assertIn('Main',w.ch_voice_list.item(0).text())
            w.ch_voice_list.setCurrentRow(0);listened=[];w.library_listen=lambda row:listened.append(row);w.ch_listen_voice();s.assertEqual(listened[0]['model'],w.model_combo.currentData())
            # generate
            first=w.channel_data.active()['scripts'][0]['id'];w.ch_table.clearSelection()
            for r in range(w.ch_table.rowCount()):
                if w.ch_table.item(r,0).data(Qt.ItemDataRole.UserRole)==first:w.ch_table.selectRow(r)
            w.ch_direction.setPlainText('Calm and friendly.');saved=(managed.downloaded,w.pump_jobs);pumped=[]
            managed.downloaded=lambda *a,**k:True;w.pump_jobs=lambda:pumped.append(1)
            try:ids=w.ch_generate([first],start=True)
            finally:managed.downloaded,w.pump_jobs=saved
            s.assertEqual(len(ids),1);job=w.store.job(ids[0]);opts=json.loads(job['options']);s.assertEqual((opts['channel_id'],opts['script_id'],opts['source_type']),(w.channel_data.active()['id'],first,'Channel'));s.assertTrue(job['title'].startswith('History – '))
            s.assertIn(ids[0],w.pending_jobs);s.assertTrue(pumped);states={w.ch_table.item(r,0).data(Qt.ItemDataRole.UserRole):w.ch_table.item(r,2).text() for r in range(w.ch_table.rowCount())};s.assertEqual(states[first],'In queue')
            s.assertEqual(w.ch_batch_table.rowCount(),1)
            w.store.job_state(ids[0],'completed');w.pending_jobs=[];w.refresh_channels();states={w.ch_table.item(r,0).data(Qt.ItemDataRole.UserRole):w.ch_table.item(r,2).text() for r in range(w.ch_table.rowCount())};s.assertEqual(states[first],'Done');s.assertEqual(w.ch_vo_table.rowCount(),1);s.assertIn('1 voice overs',w.ch_stats.text())
            w.ch_clear_finished();s.assertEqual(w.ch_batch_table.rowCount(),0)
            # scripts without text are skipped, nothing selected only gives a message
            s.assertEqual(w.ch_generate(['nope']),[])
            # prompts
            ui=w.ch_prompt_ui['script'];ui['edit'].setPlainText('Write a 1200 word script on [TOPIC].');w.ch_prompt_save('script');p=w.channel_data.applied_prompt(w.channel_data.active()['id'],'script');s.assertEqual(p['text'],'Write a 1200 word script on [TOPIC].')
            ui['edit'].setPlainText('Short version.');w.ch_prompt_new('script') if False else w.channel_data.add_prompt(w.channel_data.active()['id'],'script','Short','Short version.');w.refresh_channels();s.assertEqual(ui['combo'].count(),2)
            ui['combo'].setCurrentIndex(1);w.ch_prompt_choose('script');s.assertEqual(ui['edit'].toPlainText(),'Short version.');w.ch_prompt_apply('script');s.assertEqual(w.channel_data.applied_prompt(w.channel_data.active()['id'],'script')['title'],'Short')
            w.ch_prompt_delete('script');s.assertEqual(ui['combo'].count(),1)
            # second channel, switching keeps each channel's own scripts; deleting
            w.channel_data.add_channel('Feng Shui');w.refresh_channels();w.ch_pick(1);s.assertEqual(w.channel_data.active()['name'],'Feng Shui');s.assertEqual(w.ch_table.rowCount(),0)
            w.ch_duplicate();s.assertEqual(w.ch_combo.count(),3);w.ch_delete();s.assertEqual(w.ch_combo.count(),2)
            # the owner can hide the page from other people
            w.access.apply_preset('simple');s.assertEqual(w.access.configured('page.channels'),'hide')
            # the data survives a restart
            s.assertEqual(len(channels_check(w.channel_data.file)),2)
        finally:
            w.close()
            if prior is None:os.environ.pop('AI_VOICE_STUDIO_HOME',None)
            else:os.environ['AI_VOICE_STUDIO_HOME']=prior
def channels_check(file):return ch_mod.Channels(file).all()
from PySide6.QtCore import Qt

from studio import autoresolve, managed as managed_mod, capabilities as caps_mod, model_sources
from studio.core import models
KOKORO_ERROR=("ERROR: Failed to build 'pyopenjtalk' when getting requirements to build wheel\nsubprocess.CalledProcessError: Command '['cmake', '..']' returned non-zero exit status 1.")
class AutoResolve(unittest.TestCase):
    def setUp(s):
        s.prior={k:os.environ.get(k) for k in ('AI_VOICE_STUDIO_HOME','AVS_AUTO_RESOLVE')};os.environ['AI_VOICE_STUDIO_HOME']=tempfile.mkdtemp();os.environ.pop('AVS_AUTO_RESOLVE',None)
    def tearDown(s):
        for k,v in s.prior.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v
    def test_detection(s):
        s.assertEqual(autoresolve.reducible('kokoro',KOKORO_ERROR),'Japanese');s.assertEqual(autoresolve.reducible('kokoro','error: Microsoft Visual C++ 14.0 or greater is required'),'Japanese')
        s.assertIsNone(autoresolve.reducible('kokoro','No matching distribution found for kokoro-onnx'));s.assertIsNone(autoresolve.reducible('chatterbox',KOKORO_ERROR));s.assertIsNone(autoresolve.reducible('kokoro',''))
        os.environ['AVS_AUTO_RESOLVE']='0';s.assertFalse(autoresolve.enabled());s.assertIsNone(autoresolve.reducible('kokoro',KOKORO_ERROR))
    def test_packages_and_probe(s):
        s.assertIn('misaki-fork[en,ja,zh]==0.9.6',model_sources.extra_packages('kokoro'));s.assertIn('misaki-fork[en,zh]==0.9.6',model_sources.extra_packages('kokoro',True));s.assertEqual(model_sources.extra_packages('dia',True),['soundfile','librosa'])
        s.assertIn('en,espeak,ja,zh',managed_mod.engine_symbol('kokoro'));managed_mod.set_reduced('kokoro');s.assertTrue(managed_mod.is_reduced('kokoro'))
        sym=managed_mod.engine_symbol('kokoro');s.assertIn('en,espeak,zh',sym);s.assertNotIn(',ja',sym);s.assertEqual(managed_mod.engine_symbol('chatterbox'),managed_mod.ENGINE_SYMBOLS['chatterbox'])
        managed_mod.clear_reduced();s.assertFalse(managed_mod.is_reduced('kokoro'))
    def test_install_retries_without_the_optional_part(s):
        calls=[];logs=[]
        def installer(py,args,log,cancel):
            calls.append(list(args))
            if len(calls)==1:log('Building pyopenjtalk');raise RuntimeError(KOKORO_ERROR)
        managed_mod.install_with_resolve('kokoro','py',['--constraint','c'],'kokoro-onnx==0.5.0',logs.append,None,installer)
        s.assertEqual(len(calls),2);s.assertIn('misaki-fork[en,ja,zh]==0.9.6',calls[0]);s.assertIn('misaki-fork[en,zh]==0.9.6',calls[1]);s.assertNotIn('misaki-fork[en,ja,zh]==0.9.6',calls[1]);s.assertTrue(managed_mod.is_reduced('kokoro'));s.assertTrue(any('Auto resolve' in l for l in logs))
        calls.clear();managed_mod.install_with_resolve('kokoro','py',[],'kokoro-onnx==0.5.0',logs.append,None,lambda py,args,log,cancel:calls.append(list(args)));s.assertEqual(len(calls),1);s.assertIn('misaki-fork[en,zh]==0.9.6',calls[0])    # already reduced: no more retries
        managed_mod.clear_reduced()
    def test_no_retry_when_off_unrelated_or_still_failing(s):
        def fail(text):
            n=[0]
            def installer(py,args,log,cancel):n[0]+=1;raise RuntimeError(text)
            return installer,n
        os.environ['AVS_AUTO_RESOLVE']='0';i,n=fail(KOKORO_ERROR)
        with s.assertRaises(RuntimeError):managed_mod.install_with_resolve('kokoro','py',[],'p',lambda m:None,None,i)
        s.assertEqual(n[0],1);s.assertFalse(managed_mod.is_reduced('kokoro'));os.environ['AVS_AUTO_RESOLVE']='1'
        i,n=fail('No matching distribution found');
        with s.assertRaises(RuntimeError):managed_mod.install_with_resolve('kokoro','py',[],'p',lambda m:None,None,i)
        s.assertEqual(n[0],1);i,n=fail(KOKORO_ERROR)
        with s.assertRaises(RuntimeError):managed_mod.install_with_resolve('kokoro','py',[],'p',lambda m:None,None,i)
        s.assertEqual(n[0],2);managed_mod.clear_reduced()      # the reduced install failed too: the real error is shown
        i,n=fail(KOKORO_ERROR)
        with s.assertRaises(RuntimeError):managed_mod.install_with_resolve('chatterbox','py',[],'p',lambda m:None,None,i)
        s.assertEqual(n[0],1)
    def test_japanese_voices_are_left_out_only_when_reduced(s):
        jp=[v for v in models()['kokoro']['voices'] if v['language'].startswith('Japanese')];en=[v for v in models()['kokoro']['voices'] if v['language'].startswith('American')]
        s.assertTrue(jp and en);s.assertEqual(caps_mod.voice_unavailable('kokoro',jp[0]),'');managed_mod.set_reduced('kokoro')
        try:
            s.assertIn('build tools',caps_mod.voice_unavailable('kokoro',jp[0]));s.assertEqual(caps_mod.voice_unavailable('kokoro',en[0]),'');s.assertEqual(caps_mod.voice_unavailable('piper',jp[0]),'')
            class St:
                def setting(self,k,d=None):return d
            from studio import favorites as fav
            names={r['voice'] for r in fav.all_voices(St(),lambda m:True) if r['model']=='kokoro'};s.assertNotIn(jp[0]['id'],names);s.assertIn(en[0]['id'],names)
        finally:managed_mod.clear_reduced()
        names={r['voice'] for r in fav.all_voices(St(),lambda m:True) if r['model']=='kokoro'};s.assertIn(jp[0]['id'],names)
    def test_build_tools_installer(s):
        ran=[]
        class R:
            def __init__(self,code=0,out=''):self.returncode=code;self.stdout=out;self.stderr=''
        def run(cmd,**k):ran.append(cmd);return R()
        old=os.name
        try:
            os.name='posix';s.assertFalse(autoresolve.install_build_tools(run=run)[0])
            os.name='nt';ok,msg=autoresolve.install_build_tools(run=run,which=lambda n:None);s.assertFalse(ok);s.assertIn('winget',msg)
            ok,msg=autoresolve.install_build_tools(run=run,which=lambda n:'winget');s.assertTrue(ok);s.assertEqual(len(ran),2);s.assertIn('Kitware.CMake',ran[0]);s.assertIn('Microsoft.VisualStudio.2022.BuildTools',ran[1]);s.assertIn('Microsoft.VisualStudio.Workload.VCTools',' '.join(ran[1]))
            s.assertTrue(autoresolve.install_build_tools(run=lambda c,**k:R(-1978335189,'already installed'),which=lambda n:'w')[0])
            ok,msg=autoresolve.install_build_tools(run=lambda c,**k:R(5,'\nInstall failed badly'),which=lambda n:'w');s.assertFalse(ok);s.assertIn('Install failed badly',msg)
            def boom(c,**k):raise OSError('no access')
            s.assertFalse(autoresolve.install_build_tools(run=boom,which=lambda n:'w')[0])
        finally:os.name=old
    def test_settings_buttons_in_the_window(s):
        from PySide6.QtWidgets import QApplication,QPushButton
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1';app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            tools=w.settings_tabs.widget(4);names=[b.text() for b in tools.findChildren(QPushButton)]
            for want in ('Install build tools','Repair engines','Check everything'):s.assertIn(want,names)
            s.assertTrue(w.auto_resolve_check.isChecked());s.assertEqual(os.environ.get('AVS_AUTO_RESOLVE'),'1')
            w.auto_resolve_check.setChecked(False);s.assertFalse(w.store.setting('auto_resolve'));s.assertEqual(os.environ['AVS_AUTO_RESOLVE'],'0');w.auto_resolve_check.setChecked(True);s.assertEqual(os.environ['AVS_AUTO_RESOLVE'],'1')
            managed_mod.set_reduced('kokoro');checked=[];w.run_health_check=lambda:checked.append(1);w.repair_engines();s.assertFalse(managed_mod.is_reduced('kokoro'));s.assertTrue(checked)     # nothing broken: a full check runs
            installed=[];w.install_card=lambda k:installed.append(k);w.engine_ready=lambda m:False;real=managed_mod.engine_python
            import pathlib;fake=pathlib.Path(tempfile.mkdtemp())/'python.exe';fake.write_text('x');managed_mod.engine_python=lambda k:fake if k=='kokoro' else pathlib.Path('/nonexistent')
            try:w.repair_engines()
            finally:managed_mod.engine_python=real
            s.assertEqual(installed,['kokoro'])
            w.tasks={'x':1};alerts=[];w.alert=lambda m:alerts.append(m);w.repair_engines();s.assertTrue(alerts);w.tasks={}
            toasts=[];w.toast=lambda m:toasts.append(m);w.on_build_tools_done((True,'Done.'));s.assertEqual(toasts,['Done.']);w.on_build_tools_done((False,'Bad.'));s.assertEqual(alerts[-1],'Bad.')
        finally:w.close()

class CppToolsWithPython(unittest.TestCase):
    def setUp(s):s.old=os.name
    def tearDown(s):os.name=s.old
    def test_present_checks(s):
        class R:
            def __init__(self,out):self.stdout=out;self.stderr='';self.returncode=0
        os.name='posix';s.assertTrue(autoresolve.cpp_tools_present());s.assertTrue(autoresolve.cmake_present());s.assertTrue(autoresolve.build_tools_present())
        tmp=tempfile.mkdtemp();vs=Path(tmp)/'Microsoft Visual Studio'/'Installer';vs.mkdir(parents=True);(vs/'vswhere.exe').write_bytes(b'x');old=os.environ.get('ProgramFiles(x86)')
        os.name='nt';s.assertFalse(autoresolve.cpp_tools_present(run=lambda *a,**k:R('C:\\VS')))        # Microsoft's finder tool is not on this machine
        os.environ['ProgramFiles(x86)']=tmp
        try:
            s.assertTrue(autoresolve.cpp_tools_present(run=lambda *a,**k:R('C:\\Program Files\\Microsoft Visual Studio\\2022\\BuildTools\n')));s.assertFalse(autoresolve.cpp_tools_present(run=lambda *a,**k:R('  \n')))
            def boom(*a,**k):raise OSError('x')
            s.assertFalse(autoresolve.cpp_tools_present(run=boom))
        finally:
            if old is None:os.environ.pop('ProgramFiles(x86)',None)
            else:os.environ['ProgramFiles(x86)']=old
    def test_only_missing_tools_are_installed(s):
        ran=[]
        class R:
            returncode=0;stdout='';stderr=''
        def run(cmd,**k):ran.append(cmd);return R()
        os.name='nt';logs=[]
        ok,_=autoresolve.install_build_tools(logs.append,run=run,which=lambda n:'w',have_cmake=lambda:True,have_cpp=lambda:False);s.assertTrue(ok);s.assertEqual(len(ran),1);s.assertIn('Microsoft.VisualStudio.2022.BuildTools',ran[0]);s.assertTrue(any('CMake is already installed' in l for l in logs))
        ran.clear();ok,_=autoresolve.install_build_tools(run=run,which=lambda n:'w',have_cmake=lambda:True,have_cpp=lambda:True);s.assertTrue(ok);s.assertEqual(ran,[])
    def test_python_install_also_installs_the_tools(s):
        calls=[];logs=[]
        saved=(managed_mod.ensure_runtime,autoresolve.build_tools_present,autoresolve.install_build_tools)
        managed_mod.ensure_runtime=lambda log,cancel=None:'python311'
        try:
            os.name='nt'
            autoresolve.build_tools_present=lambda:False;autoresolve.install_build_tools=lambda log=None,**k:(calls.append(1),(True,'Build tools installed. Press Repair engines to finish.'))[1]
            s.assertEqual(managed_mod.ensure_runtime_and_tools(logs.append),'python311');s.assertEqual(calls,[1]);s.assertTrue(any('Microsoft C++' in l for l in logs))
            calls.clear();logs.clear();autoresolve.build_tools_present=lambda:True;managed_mod.ensure_runtime_and_tools(logs.append);s.assertEqual(calls,[]);s.assertTrue(any('already installed' in l for l in logs))
            # tools failing (for example Windows permission refused) never stops Python
            autoresolve.build_tools_present=lambda:False;autoresolve.install_build_tools=lambda log=None,**k:(False,'Microsoft C++ build tools could not be installed (cancelled).');logs.clear()
            s.assertEqual(managed_mod.ensure_runtime_and_tools(logs.append),'python311');s.assertTrue(any('English voices work without them' in l for l in logs))
            os.name='posix';calls.clear();autoresolve.install_build_tools=lambda log=None,**k:calls.append(1);managed_mod.ensure_runtime_and_tools(logs.append);s.assertEqual(calls,[])
        finally:managed_mod.ensure_runtime,autoresolve.build_tools_present,autoresolve.install_build_tools=saved
    def test_english_is_never_dropped(s):
        for key,m in models().items():
            voices=m.get('voices',[]);langs=m.get('languages',[])
            has=any(any(x in v.get('language','') for x in ('English','American','British')) for v in voices) or 'English' in langs
            s.assertTrue(has,f'{key} must offer English')
        s.assertIn('misaki-fork[en,zh]==0.9.6',model_sources.extra_packages('kokoro',True));s.assertNotIn('ja',model_sources.extra_packages('kokoro',True)[1].split('[')[1].split(']')[0].split(','))
        for part in autoresolve.OPTIONAL.values():s.assertNotIn('english',part['name'].lower())
        managed_mod.set_reduced('kokoro')
        try:
            s.assertIn('en,espeak,zh',managed_mod.engine_symbol('kokoro'))
            eng=[v for v in models()['kokoro']['voices'] if v['language'].startswith(('American','British'))];s.assertTrue(eng and all(caps_mod.voice_unavailable('kokoro',v)=='' for v in eng))
        finally:managed_mod.clear_reduced()
    def test_window_button_and_task(s):
        from PySide6.QtWidgets import QApplication,QPushButton
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1';app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            names=[b.text() for b in w.settings_tabs.widget(4).findChildren(QPushButton)];s.assertIn('Install / repair Python and C++ tools',names);s.assertNotIn('Install / repair Python',names)
            started=[];w.start_task=lambda key,model,fn,label:started.append((key,label,fn));w.install_runtime();s.assertEqual(started[0][0],'runtime');s.assertIn('C++',started[0][1])
            seen=[];saved=managed_mod.ensure_runtime_and_tools;managed_mod.ensure_runtime_and_tools=lambda l,c:seen.append((l,c));started[0][2](None,None,'LOG');managed_mod.ensure_runtime_and_tools=saved;s.assertEqual(seen,[('LOG',None)])
        finally:w.close()
