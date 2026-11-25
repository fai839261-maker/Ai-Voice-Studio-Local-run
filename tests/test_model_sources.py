import unittest
from pathlib import Path
from studio.core import models,bundled
from studio.model_sources import source_details,extra_packages
from runtime.runner import ADAPTERS

class ModelSourcesTests(unittest.TestCase):
    def test_each_builtin_has_real_bundled_adapter_and_package(self):
        for key,m in models().items():
            if m.get('requires'):continue  # engines this app cannot run have no adapter by design
            with self.subTest(key=key):
                d=source_details(key)
                self.assertEqual(d['adapter'],'runtime/'+ADAPTERS[key].replace('.','/')+'.py')
                self.assertTrue(bundled(d['adapter']).is_file())
                self.assertIn(m['package'],d['requirements'])
                self.assertEqual(d['profile'],m['profile'])
                self.assertIn(m['repo'],[l['url'] for l in d['links']])
                self.assertTrue(all(l['url'].startswith('https://') for l in d['links']))
    def test_weights_and_auxiliary_links(self):
        self.assertIn('https://huggingface.co/rhasspy/piper-voices',[l['url'] for l in source_details('piper')['links']])
        for key,m in models().items():
            urls=[l['url'] for l in source_details(key)['links']]
            if m.get('hf_repo'):self.assertIn('https://huggingface.co/'+m['hf_repo'],urls)
            for f in m.get('files',[])+m.get('extra_files',[]):self.assertIn(f['url'],urls)
    def test_shared_extras(self):
        self.assertIn('soundfile',extra_packages('kokoro'));self.assertIn('misaki-fork[en,ja,zh]==0.9.6',extra_packages('kokoro'))
        self.assertEqual(extra_packages('hf_vits_test'),['soundfile','uroman'])
        self.assertEqual(extra_packages('dia'),['soundfile','librosa'])
