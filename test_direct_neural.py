"""Neutral checks for direct presets, IPA mappings and cache/export boundaries."""
from pathlib import Path
import copy
import hashlib
import json
import tempfile
import unittest
import wave
from unittest.mock import patch

import audiolibro as audio
from direct_neural import MODEL,operational_input,recipe
from voice_conversion import VoiceStage,export_prepared
from profili_vocali import load


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def wav(path):
    with wave.open(str(path),'wb') as stream:
        stream.setparams((1,2,22050,0,'NONE','not compressed'))
        stream.writeframes(b'\x00\x10'*22050)


class DirectTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.source=self.root/'sample.ipa.md'
        self.source.write_text('# Test\n\n/qˈaːsim/\n',encoding='utf-8')
        self.path=self.root/'plan.json'
        self.plan=audio.prepare(self.source,self.path)
        self.plan['voice_model']='qwen-direct'; audio.save(self.path,self.plan)
        original=self.root/'original.wav'; wav(original)
        self.original=original
        original_meta=self.root/'original.json'
        audio.save(original_meta,{'origin':'recorded-reference','provenance':'Neutral fixture',
                                'text':'','audio':original.name,'audio_sha256':sha(original)})
        imported=self.root/'imported.wav'; wav(imported)
        self.imported_meta=self.root/'imported.json'
        self.value={'model':MODEL,'revision':'fixture-revision','voice_reference':str(original_meta),
                    'voice_reference_sha256':sha(original),'x_vector_only_mode':True,'seed':1729,
                    'settings':{'do_sample':True,'temperature':.8,'top_p':.95,'top_k':50,
                                'repetition_penalty':1.05,'max_new_tokens':1024,'non_streaming_mode':True}}
        audio.save(self.imported_meta,{'origin':'recorded-reference','provenance':'Synthetic fixture',
                    'text':'','audio':imported.name,'audio_sha256':sha(imported),
                    'original_generation_recipe':self.value})
        self.profiles=copy.deepcopy(load())
        self.profiles['characters']['Narratore'].update(engine='qwen3',reference=str(self.imported_meta))
        for target in ('audiolibro.load_profiles','profili_vocali.load'):
            mock=patch(target,return_value=self.profiles); mock.start(); self.addCleanup(mock.stop)
        self.ortho=self.root/'original.md'; self.ortho.write_text('Kasim.',encoding='utf-8')
        self.mapping=self.root/'sample.fonetica.json'
        self.report={'ipa_sha256':sha(self.source),'samples':[{'index':1,'ipa':'/qˈaːsim/',
                 'spoken_text':'Kasim.','spoken_sha256':hashlib.sha256(b'Kasim.').hexdigest(),
                 'source':str(self.ortho),'source_sha256':sha(self.ortho)}]}
        audio.save(self.mapping,self.report)
        self.jobs=[]

    def backend(self,command,*args,**kwargs):
        path=Path(command[-1]); jobs=json.loads(path.read_text(encoding='utf-8-sig'))
        self.jobs.extend(jobs)
        for job in jobs:
            self.assertEqual(job['text'],'Kasim.')
            self.assertEqual(job['parts'],[{'text':'Kasim.','language':'it'}])
            self.assertEqual(Path(job['direct_neural']['reference']),Path(self.value['voice_reference']))
            self.assertTrue(job['direct_neural']['embedding_only'])
            wav(Path(job['output']))
        audio.save(path.with_suffix('.backend.json'),{'model':MODEL,'revision':'fixture-revision',
                   'direct_neural':[{'segment_id':j['segment_id'],**j['direct_neural']} for j in jobs]})
        from subprocess import CompletedProcess
        return CompletedProcess(command,0,'','')

    def test_mapping_preserves_ipa_and_rejects_stale_missing_and_changed_text(self):
        self.assertEqual(operational_input(self.plan,self.plan['segments'][0])[0],
                         [{'text':'Kasim.','language':'it'}])
        self.assertEqual(self.plan['segments'][0]['text'],'/qˈaːsim/')
        with self.assertRaises(ValueError):
            operational_input(self.plan,dict(self.plan['segments'][0],spoken_text='/ˈka/'))
        self.mapping.unlink()
        with self.assertRaisesRegex(ValueError,'manca il rapporto'):
            operational_input(self.plan,self.plan['segments'][0])
        audio.save(self.mapping,dict(self.report,ipa_sha256='changed'))
        with self.assertRaisesRegex(ValueError,'versione del file IPA'):
            operational_input(self.plan,self.plan['segments'][0])

    def test_original_recipe_is_used_and_detects_changed_original_audio(self):
        info=recipe(self.imported_meta)
        self.assertEqual(Path(info['reference']),Path(self.value['voice_reference']))
        self.assertNotEqual(info['reference'],str(self.imported_meta))
        self.assertTrue(info['embedding_only'])
        self.original.write_bytes(self.original.read_bytes()+b'changed')
        with self.assertRaises(ValueError): recipe(self.imported_meta)

    def test_prepared_native_parts_validate_hash_and_do_not_italianize(self):
        self.report['samples'][0]['operational_parts']=[{
            'text':'قاسم','language':'ar','audio':str(self.original),
            'audio_sha256':sha(self.original),'provisional':True}]
        audio.save(self.mapping,self.report)
        parts,_=operational_input(self.plan,self.plan['segments'][0])
        self.assertEqual(parts[0]['language'],'ar')
        self.assertEqual(parts[0]['text'],'قاسم')
        self.assertEqual(Path(parts[0]['audio']),self.original)
        self.original.write_bytes(self.original.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'nativa alterato'):
            operational_input(self.plan,self.plan['segments'][0])

    def test_unsupported_native_language_requires_explicit_audio(self):
        self.report['samples'][0]['operational_parts']=[{'text':'قاسم','language':'ar'}]
        audio.save(self.mapping,self.report)
        with self.assertRaisesRegex(ValueError,'serve un audio nativo'):
            operational_input(self.plan,self.plan['segments'][0])

    def test_malformed_mapping_and_incomplete_recipe_fail_as_validation_errors(self):
        audio.save(self.mapping,{'samples':['invalid']})
        with self.assertRaisesRegex(ValueError,'rapporto fonetico'):
            operational_input(self.plan,self.plan['segments'][0])
        data=json.loads(self.imported_meta.read_text())
        del data['original_generation_recipe']['revision']
        audio.save(self.imported_meta,data)
        with self.assertRaisesRegex(ValueError,'incompleto'):
            recipe(self.imported_meta)

    def test_direct_preview_forces_neural_route_caches_and_exports_without_seed(self):
        with patch('audiolibro.run_backend',side_effect=self.backend), \
                patch('environment_check.ensure_generation_ready'), \
                patch('library_updates.runtime_python',return_value=Path(__file__)), \
                patch('voice_conversion.SeedWorker.convert',side_effect=AssertionError('Seed must not run')):
            output=audio.preview(self.path,0)
            self.assertEqual(len(self.jobs),1)
            self.assertEqual(audio.preview(self.path,0),output)
            self.assertEqual(len(self.jobs),1)
            self.assertEqual(audio.preview(self.path,0,regenerate=True),output)
            self.assertEqual(len(self.jobs),2)
            combined=self.root/'export.wav'
            export_prepared(self.path,[output],combined)
            self.assertTrue(combined.is_file())
            metadata=json.loads(output.with_suffix('.indice.json').read_text())
            self.assertFalse(metadata.get('voice_conversion'))
            self.assertEqual(metadata['backends']['qwen3']['direct_neural'][0]['model'],MODEL)
            metadata['backends']={}; audio.save(output.with_suffix('.indice.json'),metadata)
            with self.assertRaisesRegex(ValueError,'diretta mancante'):
                export_prepared(self.path,[output],self.root/'wrong.wav')

    def test_mapping_and_preset_changes_invalidate_cache_identity(self):
        old=audio.segment_audio_key(self.plan,self.plan['segments'][0])
        self.report['note']='Changed mapping revision'
        audio.save(self.mapping,self.report)
        self.assertNotEqual(old,audio.segment_audio_key(self.plan,self.plan['segments'][0]))
        before=audio.reference_hashes(self.profiles)
        transcript=self.root/'transcript.json'
        audio.save(transcript,{'reference_sha256':sha(self.original),'transcript':'Neutral reference.'})
        data=json.loads(self.imported_meta.read_text())
        data['original_generation_recipe'].update(x_vector_only_mode=False,reference_transcript_file=str(transcript))
        audio.save(self.imported_meta,data)
        self.assertFalse(recipe(self.imported_meta)['embedding_only'])
        self.assertNotEqual(before,audio.reference_hashes(self.profiles))


if __name__=='__main__': unittest.main()
