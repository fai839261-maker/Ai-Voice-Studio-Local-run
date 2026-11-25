import os as _os
_os.environ.setdefault("AVS_OWNER_KEY_FILE","none")   # tests run without the owner key that ships with the software
import os,sys,tempfile,unittest,wave,struct,math,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['AI_VOICE_STUDIO_HOME']=tempfile.mkdtemp()
from studio import capabilities,emotions,training,sources,audio_fx
from studio.core import models,is_audio

def tone(path,seconds=5,amp=6000,rate=16000):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h',int(amp*math.sin(i/7))) for i in range(int(rate*seconds))))

class Caps(unittest.TestCase):
    def test_clamp_and_unknown_untouched(s):
        o={'temperature':5,'exaggeration':3,'diffusion_steps':99,'device':'cpu','speed':'x'}
        capabilities.clamp_inplace('xtts2',o);s.assertEqual(o['temperature'],1.0);s.assertEqual(o['device'],'cpu')
        capabilities.clamp_inplace('chatterbox',o);s.assertEqual(o['exaggeration'],1.0)
        s.assertEqual(o['diffusion_steps'],15);s.assertIsInstance(o['diffusion_steps'],int)
        o2={'diffusion_steps':15};capabilities.clamp_inplace('styletts2',o2,True);s.assertEqual(o2['diffusion_steps'],8)
        o3={'temperature':'bad'};capabilities.clamp_inplace('dia',o3);s.assertEqual(o3['temperature'],0.8)
    def test_fish_unavailable_on_windows_only(s):
        fish=models()['fishs2'];s.assertIn('24 GB',capabilities.unavailable_reason(fish,'nt'));s.assertEqual(capabilities.unavailable_reason(fish,'posix'),'')
        s.assertEqual(capabilities.unavailable_reason(models()['xtts2'],'nt'),'')
    def test_new_engines_registered(s):
        m=models();s.assertEqual(m['xtts2']['kind'],'clone');s.assertEqual(m['qwen17bdesign']['kind'],'design');s.assertIn('voice_description',m['qwen17bdesign']['supports'])
        from runtime.runner import ADAPTERS
        for k in ('xtts2','styletts2','qwen17bdesign'):s.assertIn(k,ADAPTERS)
        for f in ('xtts2_adapter','styletts2_adapter'):s.assertTrue(Path('runtime/adapters/'+f+'.py').exists() or Path(__file__).resolve().parents[1].joinpath('runtime/adapters/'+f+'.py').exists())
    def test_emotions(s):
        m=models()
        s.assertEqual(emotions.mode(m['qwen17bvoice']),'text');s.assertEqual(emotions.mode(m['chatterbox']),'numeric');s.assertEqual(emotions.mode(m['piper']),'')
        s.assertIn('sadly',emotions.values(m['qwen17bvoice'],'sad')['emotion_text'])
        v=emotions.values(m['chatterbox'],'excited');s.assertTrue(0.25<=v['exaggeration']<=1.0)
        s.assertEqual(emotions.values(m['piper'],'sad'),{});s.assertNotIn('whisper',emotions.available(m['chatterbox']))

class Training(unittest.TestCase):
    def setUp(s):
        if not audio_fx.ffmpeg_path():s.skipTest('no ffmpeg')
        s.p=training.create('My Voice');s.src=Path(tempfile.mkdtemp())
    def test_flow(s):
        files=[]
        for i,(sec,amp) in enumerate([(6,6000),(8,6000),(0.5,6000),(5,32767)]):
            f=s.src/f'c{i}.wav';tone(f,sec,amp);files.append(f)
            if i<2:f.with_suffix('.txt').write_text(f'Sentence number {i} is here.')
        added,problems=training.add_clips(s.p['id'],files);s.assertEqual(len(added),4);s.assertFalse(problems)
        r=training.report(s.p['id']);s.assertEqual(r['clips'],4);s.assertEqual(r['with_text'],2);s.assertTrue(r['problems'])
        wav,text=training.build_reference(s.p['id']);s.assertTrue(is_audio(wav));s.assertIn('Sentence number 0',text)
        cid=[c for c in training.load(s.p['id'])['clips'] if not c['text']][0]['id'];training.set_text(s.p['id'],cid,'Added | text')
        s.assertIn('Added',[c['text'] for c in training.load(s.p['id'])['clips']][2]+'Added')
        out=training.export_dataset(s.p['id'],'xtts',Path(tempfile.mkdtemp()))
        lines=(out/'metadata.csv').read_text().splitlines();s.assertEqual(lines[0],'audio_file|text|speaker_name');s.assertTrue(all(l.count('|')==2 for l in lines))
        with wave.open(str(next((out/'wavs').glob('*.wav'))),'rb') as w:s.assertEqual(w.getframerate(),22050)
        out2=training.export_dataset(s.p['id'],'styletts2',Path(tempfile.mkdtemp()));s.assertTrue((out2/'train_list.txt').exists() and (out2/'val_list.txt').exists())
    def test_errors(s):
        with s.assertRaises(ValueError):training.build_reference(s.p['id'])
        with s.assertRaises(ValueError):training.export_dataset(s.p['id'],'xtts',Path(tempfile.mkdtemp()))
        bad=s.src/'x.wav';bad.write_bytes(b'not audio');a,pr=training.add_clips(s.p['id'],[bad]);s.assertEqual(a,[]);s.assertEqual(len(pr),1)

class Sources(unittest.TestCase):
    def fetch(s,url):
        if 'api.github.com' in url:return {'license':{'spdx_id':'MIT'},'pushed_at':'2026-01-01','stargazers_count':5,'description':'x','archived':True,'topics':['tts']}
        if url.endswith('config.json'):return {'model_type':'vits'}
        if 'coqui/XTTS-v2' in url:return {'tags':['text-to-speech'],'pipeline_tag':'text-to-speech','cardData':{'license':'other'},'siblings':[{'rfilename':'model.pth','size':2000000000}],'lastModified':'x'}
        if 'gated' in url:return {'gated':'auto','tags':[],'siblings':[]}
        if 'mms-tts' in url:return {'tags':['vits','text-to-speech'],'pipeline_tag':'text-to-speech','cardData':{'license':'cc-by-nc-4.0'},'siblings':[{'rfilename':'m.safetensors','size':1}]}
        return {'tags':['text-to-speech'],'cardData':{'license':'apache-2.0'},'siblings':[{'rfilename':'big.safetensors','size':9000000000}]}
    def test_parse(s):
        s.assertEqual(sources.parse_source('https://github.com/idiap/coqui-ai-TTS/'),('github','idiap/coqui-ai-TTS'))
        s.assertEqual(sources.parse_source('https://huggingface.co/a/b'),('hf','a/b'));s.assertEqual(sources.parse_source('gh:o/r.git'),('github','o/r'))
        for bad in ('http://github.com/a/b','https://evil.com/a/b','a','https://huggingface.co/datasets/a/b','https://github.com/a/b?x=1'):
            with s.assertRaises(ValueError):sources.parse_source(bad)
    def test_licences(s):
        s.assertEqual(sources.license_class('apache-2.0'),'open');s.assertEqual(sources.license_class('cc-by-nc-4.0'),'noncommercial')
        s.assertEqual(sources.license_class('other'),'unknown');s.assertEqual(sources.license_class('fish-audio-research'),'noncommercial')
    def test_verdicts_and_persistence(s):
        r=sources.add('coqui/XTTS-v2',s.fetch);s.assertEqual(r['verdict'],'supported');s.assertEqual(r['engine'],'xtts2')
        r=sources.add('someone/new-tts',s.fetch);s.assertEqual(r['verdict'],'needs_adapter');s.assertTrue(any('Large' in n for n in r['notes']));s.assertEqual(r['license_class'],'open')
        r=sources.add('facebook/mms-tts-urd',s.fetch);s.assertEqual(r['verdict'],'importable');s.assertEqual(r['license_class'],'noncommercial')
        s.assertEqual(sources.add('x/gated-model',s.fetch)['verdict'],'blocked')
        g=sources.add('gh:owner/repo',s.fetch);s.assertTrue(any('archived' in n for n in g['notes']))
        s.assertEqual(len(sources.saved()),5);sources.remove('github','owner/repo');s.assertEqual(len(sources.saved()),4)
    def test_recheck_reports_changes(s):
        sources.add('someone/new-tts',s.fetch)
        def newer(url):
            d=s.fetch(url);d['cardData']={'license':'mit'};return d
        ch=sources.recheck_all(newer);s.assertTrue(any('licence changed' in c for c in ch))
        def down(url):raise OSError('offline')
        sources.recheck_all(down);s.assertTrue(any('Could not check' in n for x in sources.saved() for n in x['notes']))
    def test_auto_due(s):
        class St:
            d={}
            def setting(self,k,v=None):return self.d.get(k,v)
        st=St();s.assertTrue(sources.auto_due(st,1e9));st.d['sources_last_auto']=1e9;s.assertFalse(sources.auto_due(st,1e9+3600));s.assertTrue(sources.auto_due(st,1e9+8*86400))
        st.d['sources_auto_check']=False;s.assertFalse(sources.auto_due(st,1e12))
    def test_builtin_list(s):
        rows=sources.builtin();s.assertTrue(any(r['repo']=='coqui/XTTS-v2' for r in rows));s.assertTrue(all(r['url'].startswith('https://') for r in rows))
if __name__=='__main__':unittest.main()

class Design(unittest.TestCase):
    def test_compose_matches_example(s):
        from studio import design
        s.assertEqual(design.compose('65-year-old','male','British','deep','slow','documentary narration','calm and authoritative'),
                      'A 65-year-old British male, deep voice, slow documentary narration, calm and authoritative.')
        s.assertEqual(design.compose(),'');s.assertTrue(design.compose(gender='female').startswith('A female'))
        s.assertTrue(design.compose(age='elderly',gender='male').startswith('An elderly male'))
        with s.assertRaises(ValueError):design.valid_description('hi')
        s.assertEqual(design.valid_description('  a  calm   voice here '),'a calm voice here')

class OwnerDefaults(unittest.TestCase):
    def test_owner_only_items_and_setup_always_open(s):
        from studio import owner
        a=owner.Access(Path(tempfile.mkdtemp())/'a.json');a.set_password('abcd');a.apply_preset('simple');a.lock_for_users()
        for key in ('page.sources','set.backup','setup.attach','setup.add_model','access.manager'):s.assertEqual(a.state(key),owner.HIDE,key)
        s.assertEqual(a.state('page.setup'),owner.SHOW);s.assertEqual(a.state('page.design'),owner.SHOW)
        for key in ('page.sources','set.backup','setup.attach','page.setup'):
            with s.assertRaises(KeyError):a.set_feature(key,owner.SHOW)
        a.enter_owner('abcd')
        for key in ('page.sources','set.backup','setup.attach'):s.assertEqual(a.state(key),owner.SHOW,key)
        for preset in owner.PRESETS:a.apply_preset(preset);s.assertEqual(a.state('page.setup'),owner.SHOW)
class FishGate(unittest.TestCase):
    def test_window_hides_fish_on_windows(s):
        import types
        from studio import capabilities
        saved=capabilities.os;capabilities.os=types.SimpleNamespace(name='nt')
        try:s.assertTrue(capabilities.unavailable_reason(models()['fishs2']));s.assertFalse(capabilities.unavailable_reason(models()['styletts2']))
        finally:capabilities.os=saved

class Speed(unittest.TestCase):
    def test_perf_summary_and_advice(s):
        from studio import perf
        perf.record('j1',0,9.0,{'device':'cpu','cuda_available':True,'load_s':12,'audio_s':4,'reference_cached':False},'chatterbox')
        for i in range(1,4):perf.record('j1',i,8.0,{'device':'cpu','cuda_available':True,'load_s':0,'audio_s':4,'reference_cached':True},'chatterbox')
        sm=perf.summary('j1');s.assertEqual(sm['parts'],4);s.assertEqual(sm['cached'],3);s.assertEqual(sm['loads'],1);s.assertGreater(sm['rtf'],1)
        text=' '.join(perf.explain(sm,'cuda'));s.assertIn('processor, not the graphics card',text);s.assertIn('Turbo',text)
        s.assertIn('left',perf.eta_text('j1',10));s.assertIn('Faster than real time',perf.verdict(0.5));s.assertIn('Slower',perf.verdict(2.0))
        s.assertEqual(perf.parse_line('AVS_TIMING {"gen_s": 1}'),{'gen_s':1});s.assertIsNone(perf.parse_line('hello'));s.assertIsNone(perf.parse_line('AVS_TIMING {bad'))
        perf.record('j2',0,3,{'device':'cuda:0','load_s':20,'audio_s':6},'x');perf.record('j2',1,3,{'device':'cuda:0','load_s':20,'audio_s':6},'x')
        s.assertIn('loaded 2 times',' '.join(perf.explain(perf.summary('j2'))))
    def test_meter_zones(s):
        from studio.widgets import StudioMeter,HOT
        n=StudioMeter.SEGMENTS
        colors=[StudioMeter.segment_color(i,n) for i in range(n)]
        s.assertEqual(colors[0],'#34d399');s.assertEqual(colors[-1],'#ef4444')
        first_red=colors.index('#ef4444');s.assertGreaterEqual((first_red)/n*100,HOT-5);s.assertLessEqual((first_red)/n*100,HOT+5)
    def test_chatterbox_reference_analysed_once(s):
        import types,importlib
        calls={'prepare':0,'generate':[]}
        class T:
            def __init__(self,shape):self.shape=shape
            def squeeze(self):return self
            def detach(self):return self
            def cpu(self):return self
            def numpy(self):return b'x'
        class Eng:
            sr=24000;conds=None
            class t3:
                @staticmethod
                def parameters():return iter([types.SimpleNamespace(device='cuda:0')])
            def generate(self,text,audio_prompt_path=None,exaggeration=.5,cfg_weight=.5):
                if audio_prompt_path:calls['prepare']+=1;self.conds=object()
                else:assert self.conds is not None
                calls['generate'].append(audio_prompt_path);return T((1,24000))
        torch=types.ModuleType('torch');torch.cuda=types.SimpleNamespace(is_available=lambda:False,is_bf16_supported=lambda:True)
        sf=types.ModuleType('soundfile');sf.write=lambda path,*a,**k:Path(path).write_bytes(b'RIFF')
        cb=types.ModuleType('chatterbox');cbt=types.ModuleType('chatterbox.tts')
        class CB:
            @staticmethod
            def from_local(folder,device=None):return Eng()
        cbt.ChatterboxTTS=CB
        saved={k:sys.modules.get(k) for k in('torch','soundfile','chatterbox','chatterbox.tts')}
        sys.modules.update({'torch':torch,'soundfile':sf,'chatterbox':cb,'chatterbox.tts':cbt})
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
        try:
            import adapters.warm_cache as wc;wc._engine=None;wc._engine_key=None
            ad=importlib.import_module('adapters.chatterbox_adapter');importlib.reload(ad)
            d=Path(tempfile.mkdtemp())
            for n in ('ve.safetensors','t3_cfg.safetensors','s3gen.safetensors','tokenizer.json','conds.pt'):(d/n).write_bytes(b'x')
            ref=d/'r.wav';ref.write_bytes(b'x')
            for i in range(4):ad.generate({'model':'chatterbox','text':'hi','reference':str(ref),'models_dir':str(d),'output':str(d/f'o{i}.wav'),'options':{'device':'cpu'}})
            s.assertEqual(calls['prepare'],1);s.assertEqual(len(calls['generate']),4)
            ref2=d/'r2.wav';ref2.write_bytes(b'y')
            ad.generate({'model':'chatterbox','text':'hi','reference':str(ref2),'models_dir':str(d),'output':str(d/'o9.wav'),'options':{'device':'cpu'}})
            s.assertEqual(calls['prepare'],2)
        finally:
            for k,v in saved.items():
                if v is None:sys.modules.pop(k,None)
                else:sys.modules[k]=v
            sys.path.pop(0)

class V07(unittest.TestCase):
    def test_limit_never_above_90(s):
        from studio.resource_policy import limits
        class St:
            def setting(self,k,v=None):return 100
        s.assertTrue(all(x<=90 for x in limits(St()).values()))
    def test_warm_minutes_clamped(s):
        from studio import warm_worker
        warm_worker.set_idle(30);s.assertEqual(warm_worker._IDLE,120);warm_worker.set_idle(9999);s.assertEqual(warm_worker._IDLE,360);warm_worker.set_idle(300);s.assertEqual(warm_worker._IDLE,300)
    def test_library_filters(s):
        from studio import favorites
        class St:
            d={}
            def setting(self,k,v=None):return self.d.get(k,v)
        rows=favorites.all_voices(St(),lambda m:m=='kokoro')
        s.assertTrue(any(r['gender']=='Female' and r['family']=='Japanese' for r in rows))
        jf=[r for r in rows if favorites.matches(r,gender='Female',family='Japanese')];s.assertTrue(jf and all(r['gender']=='Female' and r['family']=='Japanese' for r in jf))
        s.assertTrue(all(r['ready'] for r in rows if favorites.matches(r,availability='ready')) and any(not r['ready'] for r in rows))
        s.assertTrue(all(r['model']=='kokoro' for r in rows if favorites.matches(r,model='kokoro')))
        s.assertEqual(favorites.language_family('English (UK)'),'English');s.assertEqual(favorites.language_family('Mandarin Chinese'),'Chinese')
    def test_volume_gain_limited(s):
        from studio import audio_fx
        if not audio_fx.ffmpeg_path():s.skipTest('no ffmpeg')
        import array
        p=Path(tempfile.mkdtemp())/'v.wav';tone(p,2,12000)
        def peak(path):
            with wave.open(str(path),'rb') as w:a=array.array('h');a.frombytes(w.readframes(w.getnframes()))
            return max(abs(x) for x in a)/32768
        before=peak(p);audio_fx.apply_polish(p,volume_pct=50);half=peak(p)
        s.assertLess(half,before*0.6);audio_fx.apply_polish(p,volume_pct=200);loud=peak(p);s.assertLessEqual(loud,0.98);s.assertGreater(loud,half*1.5)
    def test_window_rules(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            w.access.set_password('abcd');w.access.lock_for_users();w.apply_access();app.processEvents()
            s.assertTrue(w.nav_buttons[9].isHidden());s.assertFalse(w.nav_buttons[3].isHidden())   # sources hidden, AI Setup open
            s.assertTrue(all(c['attach'].isHidden() for c in w.setup_cards.values()))
            s.assertTrue(all(f.isHidden() for f in w.gates['set.backup']))
            w.access.enter_owner('abcd');w.apply_access()
            s.assertFalse(w.nav_buttons[9].isHidden());s.assertTrue(all(not f.isHidden() for f in w.gates['set.backup']))
            s.assertEqual(w.limit_sliders['cpu_limit'].maximum(),90);s.assertEqual((w.warm_minutes.minimum(),w.warm_minutes.maximum()),(2,6))
        finally:w.close()

class V08(unittest.TestCase):
    def _store(s):
        from studio import core
        return core.Store()
    def test_long_script_modes(s):
        st=s._store();text=('This is a sentence that goes on for a while. '*30)+'\n\n'+('Another paragraph here. '*20)
        def parts(mode,**extra):
            j=st.new_job('t',text,'chatterbox','v','','',{'segment_mode':mode,**extra});return st.sections(j),st.job(j)
        a,ja=parts('auto');w,jw=parts('whole');p,jp=parts('paragraph');se,js=parts('sentence')
        s.assertGreater(len(a),3);s.assertTrue(json.loads(ja['options']).get('auto_chunk'))
        s.assertEqual(len(w),1);s.assertTrue(json.loads(jw['options']).get('exact_parts'));s.assertGreater(len(w[0]['body']),1000)
        s.assertEqual(len(p),2);s.assertEqual(len(se),30+20)
    def test_exact_modes_not_rechunked_for_chatterbox(s):
        import inspect
        from studio import services
        src=inspect.getsource(services._process_job);s.assertIn("not opts.get('exact_parts')",src)
    def test_script_tools(s):
        from studio import scripttools as t
        s.assertEqual(t.clean('  Hello   \u201cworld\u201d \u2014 ok\u00a0here.  \n\n\n\n  Next   line \r\n'),'Hello "world" - ok here.\n\nNext line')
        s.assertEqual(t.counts('one two  three\nfour'),(4,19))
        items=[{'word':'GPU','say':'G P U'},{'word':'Dr.','say':'Doctor'},{'word':'AI Voice','say':'A I Voice'}]
        s.assertEqual(t.apply('The gpu and GPUs. Dr. Smith uses AI Voice.',items),'The G P U and GPUs. Doctor Smith uses A I Voice.')
        class St:
            d={}
            def setting(self,k,v=None):return self.d.get(k,v)
            def set_setting(self,k,v):self.d[k]=v
        st=St();t.save_entries(st,[{'word':'a','say':'b'},{'word':'A','say':'c'},{'word':'','say':'x'}]);s.assertEqual(t.entries(st),[{'word':'a','say':'b'}])
    def test_design_chips(s):
        from studio import design
        text=design.compose_from_chips({'Age':'Senior','Gender':'Male','Accent':'British','Tone':'Deep','Pace':'Slow','Mood':'Authoritative'},'documentary narration')
        s.assertEqual(text,'A 65-year-old British male, deep voice, slow documentary narration, calm and authoritative.')
        for name,(choices,use) in design.TEMPLATES.items():
            s.assertTrue(design.compose_from_chips(choices,use).endswith('.'),name)
        s.assertEqual(design.compose_from_chips({}),'')
    def test_auto_mp3_option_wired(s):
        import inspect
        from studio import services
        s.assertIn('auto_mp3',inspect.getsource(services._process_job))
    def test_window_features(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        w=StudioWindow()
        try:
            s.assertEqual([w.segment_mode.itemText(i) for i in range(5)],['Automatic','One go','Paragraphs','Paragraph groups','Sentences'])
            w.script.setPlainText('One two three. Four five.');app.processEvents()
            s.assertEqual(w.script_counts.text(),'5 words · 25 characters')
            w.segment_mode.setCurrentIndex(w.segment_mode.findData('whole'));o=w.options();s.assertEqual((o['segment_mode'],o['auto_chunk'],o['exact_parts']),('whole',False,True))
            labels=[r[0].text() for r in w.rows.values()];s.assertIn('Model',labels);s.assertNotIn('Engine',labels)
            order=[k for k in w.rows if k in ('engine','vlang','gender','voice')];s.assertEqual(order,['engine','vlang','gender','voice'])
            j=w.store.new_job('Doc job','Alpha beta. Gamma.','kokoro','af_heart','','',{'segment_mode':'paragraph','source_type':'DOCX'})
            w.refresh_queue();s.assertEqual(w.queue_table.columnCount(),10)
            row=[w.queue_table.item(0,c).text() for c in range(10) if w.queue_table.item(0,c)];s.assertIn('DOCX',row);s.assertIn('Paragraphs',row);s.assertIn('WAV (pending)',row)
            w.design_template('News anchor');s.assertTrue(w.design_text.toPlainText().startswith('An adult') or 'news reading' in w.design_text.toPlainText())
            w.design_clear();s.assertEqual(w.design_text.toPlainText(),'')
        finally:w.close()

class V09(unittest.TestCase):
    def _store(s):
        from studio import core
        return core.Store()
    def test_pause_tags_split_and_gaps(s):
        from studio import core
        s.assertEqual(core.split_pauses('Hello there. [pause 1s] Next part [pause 2.5 seconds][pause 0.5s] last.'),[('Hello there.',1000),('Next part',3000),('last.',0)])
        s.assertEqual(core.split_pauses('No tags here.'),[('No tags here.',0)])
        s.assertEqual(core.split_pauses('[pause 99s] only')[0][1] if False else core.split_pauses('A [pause 99s] B'),[('A',10000),('B',0)])
        s.assertEqual(core.strip_pause_tags('A [pause 1s] B').split(),['A','B'])
        st=s._store();j=st.new_job('p','First sentence here. [pause 2s] Second sentence here.','kokoro','af_heart','','',{'segment_mode':'auto'})
        secs=st.sections(j);s.assertEqual(len(secs),2);s.assertTrue(all('pause' not in x['body'] for x in secs))
        s.assertEqual(json.loads(st.job(j)['options'])['gaps'],[2000,0])
    def test_join_uses_gaps(s):
        from studio import core
        d=Path(tempfile.mkdtemp());files=[]
        for i in range(3):
            f=d/f'{i}.wav';tone(f,0.1,5000,16000);files.append(f)
        out=d/'o.wav';core.join_wave(files,out,100,[1000,0,0])
        with wave.open(str(out),'rb') as w:secs=w.getnframes()/w.getframerate()
        s.assertAlmostEqual(secs,0.3+1.0+0.1,places=1)
        core.join_wave(files,out,100,[1000]);  # wrong length is ignored
        with wave.open(str(out),'rb') as w:s.assertAlmostEqual(w.getnframes()/w.getframerate(),0.3+0.2,places=1)
    def test_delete_job(s):
        st=s._store();j=st.new_job('d','One. Two.','kokoro','af_heart','','',{'segment_mode':'sentence'})
        st.delete_job(j);s.assertIsNone(st.job(j));s.assertEqual(st.sections(j),[])
    def test_recorder_writes_wav(s):
        from studio.recorder import Recorder
        import array
        r=Recorder();p=Path(tempfile.mkdtemp())/'rec.wav';r.open(p,24000)
        data=array.array('h',[int(8000*math.sin(i/6)) for i in range(24000)]).tobytes();r.feed(data);r.feed(b'')
        s.assertAlmostEqual(r.seconds,1.0,places=2);s.assertGreater(r.level,0.2)
        r.feed(data[:3]);r.close()
        with wave.open(str(p),'rb') as w:s.assertEqual((w.getnchannels(),w.getsampwidth(),w.getframerate()),(1,2,24000))
    def test_denoise_keeps_audio(s):
        from studio import audio_fx
        if not audio_fx.ffmpeg_path():s.skipTest('no ffmpeg')
        p=Path(tempfile.mkdtemp())/'n.wav';tone(p,1.5,6000,24000);audio_fx.denoise(p);s.assertTrue(core_is_audio(p))
    def test_widgets(s):
        from PySide6.QtWidgets import QApplication,QDoubleSpinBox,QSpinBox,QLabel
        app=QApplication.instance() or QApplication([])
        from studio.widgets import SliderRow,CardGrid,DropZone
        sp=QDoubleSpinBox();sp.setRange(.5,2);sp.setValue(1.0);row=SliderRow(sp,'Slower','Faster',100)
        row.slider.setValue(150);s.assertAlmostEqual(sp.value(),1.5);sp.setValue(0.75);s.assertEqual(row.slider.value(),75)
        iv=QSpinBox();iv.setRange(25,200);iv.setValue(100);r2=SliderRow(iv,'','',1);r2.slider.setValue(140);s.assertEqual(iv.value(),140)
        g=CardGrid(100);g.resize(450,300);cards=[QLabel(str(i)) for i in range(7)];g.set_cards(cards)
        s.assertEqual(g.cols,4);g.resize(220,300);g._arrange();s.assertEqual(g.cols,2)
    def test_window_new_screens(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        from studio import clones
        w=StudioWindow()
        try:
            w.access.enter_owner('abcd');w.apply_access()
            s.assertTrue(w.type_buttons['tts'].isChecked());w.type_buttons['clone'].click();s.assertEqual(w.voice_type.currentData(),'clone');w.type_buttons['tts'].click()
            s.assertEqual(w.panel_stack.count(),2);w.show_panel(1);s.assertEqual(w.panel_stack.currentIndex(),1);w.show_panel(0)
            s.assertEqual(w.mode_badge.text(),'Owner');s.assertEqual([w.settings_tabs.tabText(i) for i in range(5)],['General','Performance','Backup','Updates','Tools'])
            w.access.set_password('abcd');w.access.lock_for_users();w.apply_access();s.assertEqual(w.mode_badge.text(),'Member');s.assertEqual(w.settings_tabs.count(),5);s.assertFalse(w.settings_tabs.isTabVisible(2));s.assertTrue(w.settings_tabs.isTabVisible(3));w.access.enter_owner('abcd');w.apply_access();s.assertTrue(w.settings_tabs.isTabVisible(2))
            w.script.setPlainText('Hello world. [pause 2s] Bye now.');app.processEvents();s.assertIn('1 pause',w.script_counts.text());s.assertIn('4 words',w.script_counts.text())
            w.insert_tag('[pause 1s]');s.assertIn('[pause 1s]',w.script.toPlainText())
            w.output_speed.setValue(1.4);w.volume_spin.setValue(130);w.reset_values();s.assertEqual((w.output_speed.value(),w.volume_spin.value()),(1.0,100))
            o=w.options();s.assertIn('export_transcript',o)
            w.toggle_sidebar();s.assertEqual(w.side_frame.width(),70);w.toggle_sidebar();s.assertEqual(w.side_frame.width(),232)
            before=w.store.setting('ui_theme','dark');w.toggle_theme();s.assertNotEqual(w.store.setting('ui_theme','dark'),before)
            f=Path(tempfile.mkdtemp())/'r.wav';tone(f,8,5000,24000);clones.save_profile(w.store,'Marso','chatterbox',f,'',True,{});w.refresh_library()
            s.assertEqual(len(w.clone_grid.cards),len(clones.profiles(w.store)));s.assertGreaterEqual(len(w.clone_grid.cards),1);w.refresh_voice_library();s.assertGreater(len(w.lib_grid.cards),10)
            j=w.store.new_job('Hist','History text here.','kokoro','af_heart','','',{'segment_mode':'auto'});w.refresh_history();s.assertTrue(w.history_box.count()>=1)
            w.delete_history.__func__  # exists
        finally:w.close()
def core_is_audio(p):
    from studio.core import is_audio
    return is_audio(p)

class V091(unittest.TestCase):
    def test_diagnosis(s):
        from studio import autofix
        d=autofix.diagnose('IndexError: index out of range in self ... text_pos_emb','chatterbox');s.assertEqual((d.code,d.action),('too_long','split'))
        s.assertEqual(autofix.diagnose('CUDA out of memory. Tried to allocate').code,'memory')
        s.assertEqual(autofix.diagnose('RuntimeError: CUDA error: device-side assert triggered').action,'cpu')
        s.assertEqual(autofix.diagnose("ModuleNotFoundError: No module named 'torch'").action,'repair')
        s.assertEqual(autofix.diagnose('Exact transcript required for this model.').action,'transcript')
        s.assertEqual(autofix.diagnose('something strange').action,'health')
        s.assertTrue(all(len(x.advice)>20 for _n,x in autofix.RULES))
    def test_limits_table(s):
        from studio import capabilities as c
        s.assertEqual(c.max_chars('chatterbox'),600);s.assertEqual(c.max_chars('unknown-model'),1500)
        from studio.core import models
        s.assertTrue(all(c.max_chars(k)>=300 for k in models()))
    def test_long_part_is_split_and_joined(s):
        from studio import services
        calls=[]
        def fake(model,voice,text,output,options=None,reference='',ref_text='',log=lambda m:None):
            calls.append(len(text));tone(Path(output),0.2,4000,24000)
        saved=services.invoke_engine;services.invoke_engine=fake
        try:
            out=Path(tempfile.mkdtemp())/'part.wav';text=('This sentence is about forty characters. '*40).strip()
            logs=[];services.render_section('chatterbox','',text,out,{'pause_ms':200},'','',logs.append)
            s.assertGreater(len(calls),3);s.assertTrue(all(n<=350 for n in calls));s.assertTrue(core_is_audio(out));s.assertTrue(any('Splitting' in l for l in logs))
            s.assertFalse(list(out.parent.glob('*.p*.wav')))
            calls.clear();services.render_section('kokoro','',text[:500],out,{},'','',lambda m:None);s.assertEqual(calls,[len(text[:500])])
            calls.clear();services.render_section('chatterbox','',text,out,{},'','',lambda m:None,0,False);s.assertEqual(calls,[len(text)])   # auto-fix off: sent as is
        finally:services.invoke_engine=saved
    def test_failed_part_is_retried_then_split(s):
        from studio import services
        state={'n':0,'lens':[]}
        def flaky(model,voice,text,output,options=None,reference='',ref_text='',log=lambda m:None):
            state['lens'].append(len(text))
            if len(text)>200:raise RuntimeError('IndexError: index out of range in self')
            tone(Path(output),0.2,4000,24000)
        def always(model,voice,text,output,options=None,reference='',ref_text='',log=lambda m:None):
            state['n']+=1;raise RuntimeError('boom')
        saved=services.invoke_engine;orig_sleep=services.time.sleep;services.time.sleep=lambda x:None
        try:
            services.invoke_engine=flaky;out=Path(tempfile.mkdtemp())/'p.wav'
            services.render_section('kokoro','','Short enough sentence here. '*14,out,{'pause_ms':100},'','',lambda m:None,1,True)   # 400 chars, within kokoro limit but engine rejects it
            s.assertTrue(core_is_audio(out));s.assertGreater(len(state['lens']),2)
            services.invoke_engine=always
            with s.assertRaises(RuntimeError):services.render_section('kokoro','','hello there',out,{},'','',lambda m:None,2,True)
            s.assertEqual(state['n'],3)
        finally:services.invoke_engine=saved;services.time.sleep=orig_sleep
    def test_saved_transcript_and_job_options_and_threads(s):
        from studio import clones,resource_policy as rp
        class St:
            d={'clone_profiles':[{'id':'a','name':'A','transcript':''}]}
            def setting(self,k,v=None):return self.d.get(k,v)
            def set_setting(self,k,v):self.d[k]=v
        st=St();s.assertEqual(clones.set_transcript(st,'a','  hello   world '),'hello world');s.assertEqual(clones.profiles(st)[0]['transcript'],'hello world')
        with s.assertRaises(ValueError):clones.set_transcript(st,'a','  ')
        with s.assertRaises(ValueError):clones.set_transcript(st,'zzz','x')
        store=core_store();j=store.new_job('t','Hello there.','kokoro','af_heart','','',{'segment_mode':'whole'})
        store.set_job_options(j,{'segment_mode':'auto','auto_chunk':True});s.assertTrue(json.loads(store.job(j)['options'])['auto_chunk'])
        rp.set_threads_override(0);s.assertEqual(rp.thread_budget(50,8),4);rp.set_threads_override(3);s.assertEqual(rp.thread_budget(50,8),3);s.assertEqual(rp.thread_budget(50,2),2);rp.set_threads_override(0)
    def test_window_numbers_and_clone_words(s):
        from PySide6.QtWidgets import QApplication
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        try:import qtstub
        except Exception:s.skipTest('no audio stub')
        os.environ['AVS_NO_PROMPTS']='1'
        app=QApplication.instance() or QApplication([])
        from studio.ui_v5 import StudioWindow
        from studio import clones
        w=StudioWindow()
        try:
            for spin in (w.output_speed,w.pitch,w.temperature,w.volume_spin,w.pause,w.warm_minutes,w.parallel,w.down_limit,w.text_spin,w.script_spin):s.assertTrue(spin.isReadOnly())
            s.assertGreaterEqual(len(w.slider_rows),9)
            w.slider_rows['output_speed'].slider.setValue(130);s.assertAlmostEqual(w.output_speed.value(),1.3)
            w.parallel.setEnabled(False);s.assertFalse(w.parallel.choice_box.isEnabled());w.parallel.setEnabled(True);s.assertTrue(w.parallel.choice_box.isEnabled())
            box=w.parallel.choice_box;s.assertEqual([box.itemData(i) for i in range(box.count())],[1,2,3,4]);box.setCurrentIndex(2);s.assertEqual(w.parallel.value(),3)
            for name in ('threads_combo','retry_combo','rest_combo','autofix_check','release_check'):s.assertTrue(hasattr(w,name))
            f=Path(tempfile.mkdtemp())/'r.wav';tone(f,8,5000,24000)
            p=clones.save_profile(w.store,'NoWords','chatterbox',f,'',True,{})
            with s.assertRaises(ValueError):w.resolve_voice('f5',p['id'])          # no window pops up in tests; the message explains
            clones.set_transcript(w.store,p['id'],'Words spoken here.')
            v=w.resolve_voice('f5',p['id']);s.assertEqual(v[2],'Words spoken here.')
            s.assertEqual(w.resolve_voice('chatterbox',p['id'])[1],p['reference'])
        finally:w.close()
def core_store():
    from studio import core
    return core.Store()
