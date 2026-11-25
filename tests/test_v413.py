import json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from studio.core import catalog
from studio.voice_catalog import piper_rows,refresh_piper,piper_targets,merge,available_kokoro,QWEN_LANG
from runtime.adapters import language

class VoiceCatalogUpdate(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name);self.entries={m['id']:m for m in catalog()['models']}
    def tearDown(self):self.temp.cleanup()
    def test_54_voices_and_nine_language_routes(self):
        voices=self.entries['kokoro']['voices'];self.assertEqual(len(voices),54);self.assertEqual(len({v['id'] for v in voices}),54)
        self.assertEqual({language.kokoro_language(v['id']) for v in voices},set(language.KOKORO_LANG.values()))
        self.assertEqual(sum(v['gender']=='Female' for v in voices),29)
    def test_local_voice_bank(self):
        p=self.home/'voices-v1.0.bin'
        with zipfile.ZipFile(p,'w') as z:z.writestr('bf_emma.npy',b'test');z.writestr('af_heart.npy',b'test')
        self.assertEqual({v['id'] for v in available_kokoro(p,self.entries['kokoro']['voices'])},{'bf_emma','af_heart'})
    def test_qwen_speaker_discovery(self):
        p=self.home/'models'/'qwen06bvoice';p.mkdir(parents=True);(p/'config.json').write_text(json.dumps({'talker_config':{'spk_id':{'ryan':1,'custom':2}}}))
        self.assertEqual({v['id'] for v in merge(self.entries['qwen06bvoice'],self.home)['voices']},{'Ryan','custom'})
    def test_qwen_languages(self):
        self.assertEqual(QWEN_LANG,language.QWEN_LANG);self.assertEqual(language.qwen_language('japanese'),'Japanese');self.assertEqual(language.qwen_language(None),'Auto')
        with self.assertRaises(ValueError):language.qwen_language('unsupported')
        for mid in ('qwen06bvoice','qwen17bvoice'):self.assertEqual(len(self.entries[mid]['voices']),9)
    def test_piper_refresh_and_selection(self):
        k='fr_FR-test-medium';f='fr/fr_FR/test/medium/'+k+'.onnx';idx={k:{'name':'test','language':{'name_english':'French','code':'fr_FR'},'files':{f:{},f+'.json':{}},'quality':'medium'},'../unsafe':{'files':{}}}
        self.assertEqual(len(piper_rows(idx)),1);self.assertEqual(refresh_piper(self.home,lambda _:idx),1)
        m=merge(self.entries['piper'],self.home);self.assertIn(k,{v['id'] for v in m['voices']});self.assertNotIn(k,piper_targets(self.home,m));self.assertIn(k,piper_targets(self.home,m,k))
        with self.assertRaises(ValueError):refresh_piper(self.home,lambda _: {})
        self.assertIn(k,{v['id'] for v in merge(self.entries['piper'],self.home)['voices']})
    def test_phoneme_dispatch(self):
        for voice,lang in [('bf_emma','en-gb'),('hf_alpha','hi'),('jf_alpha','ja'),('zf_xiaobei','zh')]:
            with patch.object(language,'g2p_for',return_value=lambda text:('phonemes',None)) as engine:
                self.assertEqual(language.phonemes('sample',voice),'phonemes');engine.assert_called_once_with(lang)
    def test_phoneme_failure_is_explicit(self):
        with patch.object(language,'g2p_for',side_effect=ImportError('module unavailable')):
            with self.assertRaisesRegex(RuntimeError,'repair language dependencies'):language.phonemes('sample','jf_alpha')
    def test_clone_models_not_given_fake_presets(self):
        for model in self.entries.values():
            if model['kind']=='clone':self.assertFalse(model['voices']);self.assertIn('language_note',model)
