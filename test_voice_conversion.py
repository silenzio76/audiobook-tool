"""Neutral regressions for the pronunciation/conversion/playback boundary."""
from pathlib import Path
import json
import queue
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

import audiolibro as audio
from audio_publication import publish_audio
from pipeline_audio import AudioPipeline, ReadingCancelled
from voice_conversion import VoiceStage, SeedWorker, document_model, segment_model, export_prepared, digest


def write_wav(path, value=4000, frames=2400):
    with wave.open(str(path),'wb') as stream:
        stream.setparams((1,2,22050,0,'NONE','not compressed'))
        stream.writeframes(int(value).to_bytes(2,'little',signed=True)*frames)


class ReferenceChoiceTests(unittest.TestCase):
    def test_active_reference_is_selectable_and_technical_aliases_are_deduplicated(self):
        from gestione_voci import narrator_reference_choices
        first={'engine':'qwen3','reference':'current.json'}
        second={'engine':'qwen3','reference':'other.json'}
        profiles={'characters':{'Narratore':first,'Da assegnare':dict(first),
                                'Altro':dict(first),'Another':second}}
        def reference(path):
            current=path=='current.json'
            return ({'audio_sha256':'first' if current else 'second',
                     'character':'Approved reference' if current else 'Other'},
                    Path('reference.wav' if current else 'other.wav'))
        with patch('profili_vocali.load',return_value=profiles), \
                patch('qwen_backend.reference',side_effect=reference):
            choices=narrator_reference_choices()
        self.assertEqual(len(choices),2)
        self.assertEqual(choices[0]['profile'],'Narratore')
        self.assertTrue(choices[0]['current'])
        self.assertIn('reference.wav',choices[0]['label'])
        self.assertFalse(choices[1]['current'])


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        source=self.root/'sample.md'
        source.write_text('Una finestra aperta.\n',encoding='utf-8')
        self.path=self.root/'plan.json'
        self.plan=audio.prepare(source,self.path)
        self.plan['voice_model']='seed-vc'
        audio.save(self.path,self.plan)
        self.reference=self.root/'reference.wav'
        write_wav(self.reference)
        self.raw=self.root/'source.wav'
        staged=self.root/'staged.wav'
        write_wav(staged)
        profiles=audio.load_profiles()
        metadata={'source_sha256':self.plan['sha256'],'profiles':profiles,
                  'language_policy':audio.language_policy(),
                  'voice_references':audio.reference_hashes(profiles),
                  'segments':[dict(self.plan['segments'][0],frames=2400,pause_frames=400,
                                   audio_key=audio.segment_audio_key(self.plan,self.plan['segments'][0]),seconds=0)],
                  'duration':2400/22050,'language_parts':[],'spoken_forms':[]}
        publish_audio(staged,self.raw,metadata)
        self.identify=patch('voice_conversion.installation_identity',return_value={
            'models':'model-hash','source_revision':'source-revision','files':{},'settings':{}})
        self.refs=patch('voice_conversion.reference_for',return_value=self.reference)
        self.identify.start(); self.refs.start()
        self.addCleanup(self.identify.stop); self.addCleanup(self.refs.stop)

    def worker(self):
        class Worker:
            calls=0
            def convert(inner,source,reference,output,cancel):
                inner.calls+=1
                if cancel is not None and cancel.is_set(): raise ReadingCancelled('cancelled')
                shutil.copyfile(source,output)
            def close(inner): pass
        return Worker()

    def test_cache_tracks_audio_reference_and_preserves_pause_and_pair(self):
        worker=self.worker()
        stage=VoiceStage(self.plan,worker=worker)
        first=stage.convert(0,self.raw)
        self.assertEqual(worker.calls,1)
        self.assertEqual(stage.convert(0,self.raw),first)
        self.assertEqual(worker.calls,1)
        metadata=json.loads(first.with_suffix('.indice.json').read_text())
        self.assertEqual(metadata['audio_sha256'],digest(first))
        self.assertEqual(metadata['segments'][0]['pause_frames'],400)
        with wave.open(str(first)) as stream:
            self.assertEqual(stream.getnframes(),2400)
        old=digest(first)
        write_wav(self.reference,value=2000)
        second=stage.convert(0,self.raw)
        self.assertNotEqual(first,second)
        self.assertEqual(worker.calls,2)
        self.assertEqual(digest(first),old)

    def test_cache_only_never_launches_conversion(self):
        worker=self.worker()
        stage=VoiceStage(self.plan,worker=worker)
        self.assertIsNone(stage.convert(0,self.raw,cache_only=True))
        self.assertEqual(worker.calls,0)

    def test_forced_regeneration_reaches_worker_even_with_identical_output(self):
        worker=self.worker()
        normal=VoiceStage(self.plan,worker=worker)
        first=normal.convert(0,self.raw)
        normal.convert(0,self.raw)
        self.assertEqual(normal.statistics,{'generated':1,'cached':1,'source':0})
        previous=digest(first)
        forced=VoiceStage(self.plan,worker=worker,regenerate=True)
        result=forced.convert(0,self.raw)
        self.assertEqual(worker.calls,2)
        self.assertEqual(digest(result),previous)
        self.assertEqual(forced.statistics,{'generated':1,'cached':0,'source':0})

    def test_cancel_preserves_source_and_does_not_publish_final(self):
        worker=self.worker()
        stage=VoiceStage(self.plan,worker=worker)
        event=threading.Event(); event.set()
        original=digest(self.raw)
        with self.assertRaises(ReadingCancelled): stage.convert(0,self.raw,event)
        self.assertEqual(digest(self.raw),original)
        self.assertEqual(list(self.root.glob('seed-vc/*.wav')),[])

    def test_export_multiple_final_segments_keeps_order_and_index(self):
        first=self.plan['segments'][0]
        second=dict(first,id=2,line=2,text='Una porta chiusa.')
        self.plan['segments'].append(second)
        audio.save(self.path,self.plan)
        stage=VoiceStage(self.plan,worker=self.worker())
        one=stage.convert(0,self.raw)
        raw2=self.root/'source2.wav'
        staged=self.root/'staged2.wav'; write_wav(staged,value=2000)
        data=json.loads(self.raw.with_suffix('.indice.json').read_text())
        data['segments']=[dict(data['segments'][0],id=2,line=2,audio_key=audio.segment_audio_key(self.plan,second))]
        publish_audio(staged,raw2,data)
        two=stage.convert(1,raw2)
        target=self.root/'combined.wav'
        export_prepared(self.path,[one,two],target)
        result=json.loads(target.with_suffix('.indice.json').read_text())
        self.assertEqual([s['id'] for s in result['segments']],[1,2])
        with wave.open(str(target)) as stream: self.assertEqual(stream.getnframes(),4800)

    def test_model_overrides_preserve_language_and_export_requires_final(self):
        self.assertEqual(document_model(self.plan),'seed-vc')
        self.assertEqual(segment_model(self.plan,{'voice_model':'source'}),'source')
        self.assertEqual(segment_model(self.plan,{}),'seed-vc')
        output=self.root/'out.wav'
        with self.assertRaisesRegex(ValueError,'Conversione vocale mancante'):
            export_prepared(self.path,[self.raw],output)
        self.assertFalse(output.exists())
        final=VoiceStage(self.plan,worker=self.worker()).convert(0,self.raw)
        export_prepared(self.path,[final],output)
        self.assertTrue(output.exists())
        self.assertTrue(json.loads(output.with_suffix('.indice.json').read_text())['voice_conversions'][0])


class PipelineStageTests(unittest.TestCase):
    def test_worker_cancellation_terminates_owned_process_and_pipe_readers(self):
        original=subprocess.Popen
        processes=[]
        def spawn(command,**kwargs):
            process=original([sys.executable,'-u','-c',
                              'import sys,time; sys.stdin.readline(); time.sleep(30)'],**kwargs)
            processes.append(process)
            return process
        with tempfile.TemporaryDirectory() as directory,patch('voice_conversion.subprocess.Popen',side_effect=spawn):
            worker=SeedWorker(directory)
            cancel=threading.Event()
            timer=threading.Timer(.2,cancel.set)
            timer.start()
            try:
                with self.assertRaises(ReadingCancelled): worker.convert('source','reference','output',cancel)
            finally:
                timer.cancel(); worker.close(force=True)
            self.assertIsNone(worker.process)
            self.assertIsNotNone(processes[0].poll())
            self.assertTrue(all(not t.is_alive() for t in worker.readers))

    def wait(self,pipeline):
        for thread in pipeline.threads: thread.join(4)
        self.assertTrue(pipeline.finished)

    def test_playback_never_sees_raw_and_replay_uses_final_audio(self):
        events=queue.Queue()
        heard=[]
        pipeline=AudioPipeline(range(3),lambda i,c:f'raw{i}',heard.append,events,
                               convert=lambda i,p,c:f'final{i}',preload_all=True)
        pipeline.start(); self.wait(pipeline)
        self.assertIsNone(pipeline.error)
        self.assertEqual(heard,['final0','final1','final2'])
        self.assertEqual(pipeline.available,{0:'final0',1:'final1',2:'final2'})
        kinds=[item['kind'] for item in list(events.queue)]
        self.assertEqual(kinds.count('generated'),3)
        self.assertEqual(kinds.count('converted'),3)
        self.assertEqual([t.name for t in pipeline.threads],['Pronuncia','Voce','Ascolto'])

    def test_conversion_failure_stops_every_worker_without_raw_fallback(self):
        closed=[]; heard=[]
        def fail(i,p,c): raise RuntimeError('conversion failed')
        pipeline=AudioPipeline(range(20),lambda i,c:f'raw{i}',heard.append,queue.Queue(),
                               buffer_size=1,convert=fail,on_conversion_finished=lambda:closed.append(True))
        pipeline.start(); self.wait(pipeline)
        self.assertIn('Voce · segmento 1',pipeline.error)
        self.assertEqual(heard,[])
        self.assertEqual(closed,[True])

    def test_cancel_drains_blocked_stage_threads(self):
        entered=threading.Event()
        def convert(i,p,c): entered.set(); c.wait(2); raise ReadingCancelled('stopped')
        pipeline=AudioPipeline(range(20),lambda i,c:f'raw{i}',lambda p:None,queue.Queue(),
                               buffer_size=1,convert=convert)
        pipeline.start(); self.assertTrue(entered.wait(2)); pipeline.stop(); self.wait(pipeline)
        self.assertIsNone(pipeline.error)


if __name__=='__main__': unittest.main()
