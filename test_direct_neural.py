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


    def test_activate_preset_validates_then_replaces_route_and_overrides(self):
        from gestione_voci import activate_direct_preset
        config=self.root/'profiles.json'
        audio.save(config,self.profiles)
        original_source=self.source.read_bytes()
        self.plan['voice_model']='seed-vc'
        self.plan['segments'][0]['voice_model']='source'
        audio.save(self.path,self.plan)
        with patch('profili_vocali.CONFIG',config):
            updated,info=activate_direct_preset(self.path,self.plan,'Narratore')
        self.assertEqual(updated['voice_model'],'qwen-direct')
        self.assertEqual(updated['segments'][0]['voice_model'],'inherit')
        self.assertEqual(info,{'preset':'A','native_segments':0,'segments':1})
        self.assertEqual(audio.read_plan(self.path),updated)
        self.assertEqual(self.source.read_bytes(),original_source)

    def test_invalid_mapping_does_not_change_narrator_or_plan(self):
        from gestione_voci import activate_direct_preset
        self.mapping.unlink()
        previous=self.path.read_bytes()
        with patch('gestione_voci.set_narrator_reference') as select:
            with self.assertRaisesRegex(ValueError,'manca il rapporto'):
                activate_direct_preset(self.path,self.plan,'Narratore')
        select.assert_not_called()
        self.assertEqual(self.path.read_bytes(),previous)

    def test_reapplying_current_preset_preserves_valid_direct_cache(self):
        from gestione_voci import activate_direct_preset
        config=self.root/'profiles.json'
        audio.save(config,self.profiles)
        with patch('audiolibro.run_backend',side_effect=self.backend), \
                patch('environment_check.ensure_generation_ready'), \
                patch('library_updates.runtime_python',return_value=Path(__file__)), \
                patch('profili_vocali.CONFIG',config), \
                patch('voice_conversion.SeedWorker.convert',side_effect=AssertionError('Seed must not run')):
            output=audio.preview(self.path,0)
            previous=output.read_bytes()
            activate_direct_preset(self.path,self.plan,'Narratore')
            self.assertEqual(audio.preview(self.path,0,cache_only=True),output)
        self.assertEqual(len(self.jobs),1)
        self.assertEqual(output.read_bytes(),previous)

    def test_plan_write_failure_restores_previous_narrator_config(self):
        from gestione_voci import activate_direct_preset
        config=self.root/'profiles.json'
        self.profiles['characters']['Selected A']=copy.deepcopy(self.profiles['characters']['Narratore'])
        self.profiles['characters']['Narratore']['reference']=str(self.root/'original.json')
        audio.save(config,self.profiles)
        previous=config.read_bytes(); old_plan=self.path.read_bytes()
        real_save=audio.save
        def persist(path,data):
            if Path(path)==self.path: raise OSError('Simulated plan write failure')
            real_save(path,data)
        with patch('profili_vocali.CONFIG',config), patch('audiolibro.save',side_effect=persist):
            with self.assertRaisesRegex(OSError,'Simulated'):
                activate_direct_preset(self.path,self.plan,'Selected A')
        self.assertEqual(config.read_bytes(),previous)
        self.assertEqual(self.path.read_bytes(),old_plan)

    def test_preset_catalog_preserves_distinct_recipes_for_identical_sample_wavs(self):
        from gestione_voci import direct_preset_choices
        other=self.root/'full-prompt.json'
        transcript=self.root/'transcript.json'
        audio.save(transcript,{'reference_sha256':sha(self.original),'transcript':'Reference words.'})
        data=json.loads(self.imported_meta.read_text())
        data['original_generation_recipe'].update(x_vector_only_mode=False,reference_transcript_file=str(transcript))
        audio.save(other,data)
        self.profiles['characters']['B fixture']=dict(self.profiles['characters']['Narratore'],reference=str(other))
        choices=direct_preset_choices()
        self.assertEqual([x['preset'] for x in choices],['A','B'])
        self.assertTrue(choices[0]['current'])
        self.assertFalse(choices[1]['current'])


class RecoveryTests(unittest.TestCase):
    def test_short_utterance_budget_retains_approved_long_sample_budget(self):
        from qwen_backend import direct_token_budget
        self.assertEqual(direct_token_budget(65,1024),1024)
        self.assertEqual(direct_token_budget(55,1024),1024)
        self.assertEqual(direct_token_budget(4,1024),160)
        self.assertEqual(direct_token_budget(1,1024),128)

    def test_valid_first_output_is_returned_unchanged_without_retry(self):
        from qwen_backend import recover_direct_generation
        value=object(); seeds=[]
        def generate(seed): seeds.append(seed); return value
        result,attempts=recover_direct_generation(generate,lambda result:(None,{'seconds':1.2}),1729,'Segmento 50')
        self.assertIs(result,value)
        self.assertEqual(seeds,[1729])
        self.assertEqual(len(attempts),1)

    def test_rejected_output_is_retried_with_same_recipe_and_next_seed(self):
        from qwen_backend import recover_direct_generation
        seeds=[]
        def generate(seed): seeds.append(seed); return seed
        def validate(value):
            return ('audio vuoto' if value==1729 else None),{'seconds':0 if value==1729 else 1.1}
        result,attempts=recover_direct_generation(generate,validate,1729,'Segmento 50')
        self.assertEqual(result,1730)
        self.assertEqual(seeds,[1729,1730])
        self.assertEqual(attempts[0]['issue'],'audio vuoto')
        self.assertIsNone(attempts[1]['issue'])

    def test_repeated_failure_is_bounded_and_identifies_segment_and_problem(self):
        from qwen_backend import recover_direct_generation
        seeds=[]
        def generate(seed): seeds.append(seed); return None
        with self.assertRaisesRegex(ValueError,'Segmento 50 · riga 201.*3 tentativi.*durata'):
            recover_direct_generation(generate,lambda value:('durata 0.040 s',{'seconds':.04}),1729,
                                      'Segmento 50 · riga 201')
        self.assertEqual(seeds,[1729,1730,1731])


if __name__=='__main__': unittest.main()
