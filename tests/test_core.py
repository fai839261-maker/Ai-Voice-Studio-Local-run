import os
import tempfile
import unittest
import wave
from pathlib import Path

from studio.core import Store,split_sentences,join_wave,is_audio


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='avs_test_')
        os.environ['AI_VOICE_STUDIO_HOME']=self.tmp.name
    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop('AI_VOICE_STUDIO_HOME',None)

    def test_sentence_split_preserves_all_text(self):
        source='Dr. Smith spoke. Then we listened. It was good!\n\nAnd more.'
        pieces=split_sentences(source)
        self.assertEqual(len(pieces),4)
        self.assertEqual(pieces[0],'Dr. Smith spoke.')
        self.assertEqual(pieces[2],'It was good!')
        self.assertEqual(split_sentences('One sentence.', 0),['One sentence.'])

    def test_job_survives_new_store(self):
        s=Store()
        jid=s.new_job('Test','Hello world. Second sentence!','piper','en_GB-alan-medium')
        self.assertEqual(len(s.sections(jid)),2)
        t=Store()
        self.assertEqual(t.job(jid)['status'],'pending')
        t.section_state(jid,0,'complete')
        t.change_section(jid,1,'Edited sentence.')
        self.assertEqual(t.sections(jid)[0]['status'],'complete')
        self.assertEqual(t.sections(jid)[1]['status'],'pending')

    def test_audio_assembly(self):
        base=Path(self.tmp.name)
        inputs=[]
        for i in range(2):
            path=base/f'{i}.wav'
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
                f.writeframes(b'\0\0'*1600)
            inputs.append(path)
        dest=base/'merged.wav'
        join_wave(inputs,dest,100)
        self.assertTrue(is_audio(dest))
        with wave.open(str(dest),'rb') as f:
            self.assertEqual(f.getnframes(),1600*2+1600)

if __name__=='__main__':unittest.main()
