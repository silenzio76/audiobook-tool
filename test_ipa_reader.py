"""Validate explicit phoneme conversion and prevent orthographic reinterpretation."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import audiolibro as audio
from profili_vocali import load
from ipa_reader import phoneme_input
from lingue_audio import speech_parts


class IPATests(unittest.TestCase):
    def test_ipa_filename_sets_document_language_without_guessing_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, expected in [('sample.ipa.md', 'ipa-it'), ('sample.IPA.markdown', 'ipa-it'),
                                   ('sample.md', 'auto')]:
                with self.subTest(name=name):
                    source = root / name
                    source.write_text('/ˈpɛska/\n', encoding='utf-8')
                    plan = audio.prepare(source, root / (name + '.json'))
                    self.assertEqual(plan.get('language', 'auto'), expected)

    def test_legacy_ipa_plan_recovers_language_preserving_assignments_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample.ipa.md'
            source.write_text('/ˈpɛska/\n\n/ɬa/\n', encoding='utf-8')
            path = root / 'plan.json'
            plan = audio.prepare(source, path)
            plan.pop('language')
            plan['segments'][0].update(tone='Neutro', pause_ms=420)
            audio.save(path, plan)
            original = path.read_bytes()
            loaded = audio.read_plan(path)
            self.assertEqual(loaded['language'], 'ipa-it')
            self.assertEqual(loaded['segments'], plan['segments'])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(audio.segment_speech_parts(loaded, loaded['segments'][0]),
                             [{'text': '/ˈpɛska/', 'language': 'ipa-it'}])
            with self.assertRaisesRegex(ValueError, 'simbolo non supportato'):
                audio.segment_speech_parts(loaded, loaded['segments'][1])
            plan['language'] = 'auto'
            audio.save(path, plan)
            self.assertEqual(audio.read_plan(path)['language'], 'ipa-it')
            plan['language'] = 'en'
            plan['segments'][0]['language'] = 'it'
            audio.save(path, plan)
            loaded = audio.read_plan(path)
            self.assertEqual(loaded['language'], 'en')
            from lingue_audio import segment_language
            self.assertEqual(segment_language(loaded, loaded['segments'][0]), 'it')

    def test_windows_profile_routes_ipa_to_native_runtime_before_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('profili_vocali.CONFIG', root / 'absent.json'):
                profiles = load()
            profiles['characters']['Narratore']['ipa_delivery'] = {
                'rate_wpm': 140, 'pitch': 35, 'range': 95, 'warmth_db': -6}
            source = root / 'source.md'
            source.write_text('/ˈpɛska/\n', encoding='utf-8')
            path = root / 'plan.json'
            plan = audio.prepare(source, path)
            plan['language'] = 'ipa-it'
            audio.save(path, plan)
            with patch('audiolibro.load_profiles', return_value=profiles), \
                    patch('environment_check.ensure_generation_ready', side_effect=RuntimeError('captured')) as ready:
                with self.assertRaisesRegex(RuntimeError, 'captured'):
                    audio.render(path, root / 'out.wav')
            self.assertEqual(ready.call_args.args[0], 'qwen3')
            job = ready.call_args.args[1][0]
            self.assertEqual(job['parts'], [{'text':'/ˈpɛska/', 'language':'ipa-it'}])
            self.assertEqual(job['character_pitch'], 0)
            self.assertEqual(job['segment_id'], 1)
            self.assertEqual(job['source_line'], 1)
            self.assertEqual(job['ipa_delivery'], profiles['characters']['Narratore']['ipa_delivery'])

    def test_vowel_quality_and_stress(self):
        self.assertEqual(phoneme_input('/ˈpɛs.ka/'), "[[p'Eska]]")
        self.assertEqual(phoneme_input('/ˈpes.ka/'), "[[p'eska]]")
        self.assertEqual(phoneme_input('anˈkora'), "[[ank'oRa]]")

    def test_length_affricates_and_punctuation(self):
        self.assertEqual(phoneme_input('ˈpalːa, ˈt͡ʃɛna.'), "[[p'al:a]], [[tS'Ena]].")
        self.assertNotEqual(phoneme_input('ˈpala'), phoneme_input('ˈpalla'))

    def test_ipa_bypasses_italian_normalization(self):
        text = '/ˈpɛska/'
        self.assertEqual(speech_parts(text, 'ipa-it'), [{'text':text,'language':'ipa-it'}])

    def test_multilingual_phones_use_explicit_resources(self):
        self.assertEqual(phoneme_input('/ʁa ða θa ha/'), '[[Q"a]] [[Da]] [[Ta]] [[ha]]')
        self.assertEqual(phoneme_input('/ra ɾa ɹa/'), '[[Ra]] [[*a]] [[ra]]')
        for text, code in [('/ħa/', 'Ha'), ('/ʕa/', 'Aa'), ('/s̪a/', 's[a')]:
            with self.subTest(text=text):
                self.assertEqual(phoneme_input(text), f'<voice name="ar"> [[{code}]] </voice> ')
                self.assertIn('name="ar" gender="female" variant="3"', phoneme_input(text, 'femmina'))
                self.assertEqual(speech_parts(text, 'ipa-it'), [{'text': text, 'language': 'ipa-it'}])
        self.assertEqual(phoneme_input('/ha ħa ha/'),
                         '[[ha]] <voice name="ar"> [[Ha]] </voice>  [[ha]]')

    def test_new_ipa_policy_invalidates_previous_audio(self):
        current = audio.language_policy()
        self.assertTrue(current.startswith('multilingue-7-native-session:'))
        self.assertFalse(audio.cached_pronunciation_matches(
            {'language_policy': current.replace('multilingue-7-native-session', 'multilingue-6-ipa-delivery')},
            {'language': 'ipa-it'}, {'id': 1, 'text': '/ha/', 'role': 'Narratore'}))

    def test_unsupported_or_incomplete_input_fails(self):
        for text in ('', 'ˈ', 'ˈp ', 'ːa', 'ã', 'ɬa', '[[a]]', '<speak>a</speak>', 'ħɛ', 'ħɣa'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                phoneme_input(text)

    def test_delivery_controls_reject_invalid_or_unbounded_values(self):
        from sintesi_lingue import validate_ipa_delivery
        settings = {'rate_wpm': 140, 'pitch': 35, 'range': 95, 'warmth_db': -6}
        self.assertEqual(validate_ipa_delivery(settings), settings)
        for value in ([], None, {'rate_wpm': True}, {'pitch': float('nan')}, {'range': 101},
                      {'warmth_db': -10}, {'unknown': 1}, {'rate_wpm': 140.5}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_ipa_delivery(value)


class NativeIPATests(unittest.TestCase):
    def test_engine_emits_multilingual_phones_without_spelling_markup(self):
        try:
            import numpy as np
            from espeakng_loader import load_library
        except ImportError:
            self.skipTest('Native IPA runtime packages are optional.')
        import ctypes
        import sintesi_lingue as native
        native._close_native_session()
        from sintesi_lingue import synthesize_native

        class Identifier(ctypes.Union):
            _fields_ = [('number', ctypes.c_int), ('name', ctypes.c_void_p), ('string', ctypes.c_char * 8)]

        class Event(ctypes.Structure):
            _fields_ = [('type', ctypes.c_int), ('uid', ctypes.c_uint), ('text_position', ctypes.c_int),
                        ('length', ctypes.c_int), ('audio_position', ctypes.c_int), ('sample', ctypes.c_int),
                        ('user_data', ctypes.c_void_p), ('id', Identifier)]

        engine = load_library()
        initialize = engine.espeak_Initialize
        initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        set_callback = engine.espeak_SetSynthCallback
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int,
                                         ctypes.c_void_p)
        callbacks, emitted = [], []
        self.addCleanup(lambda: (native._close_native_session(), callbacks.clear()))

        def record_callback(original):
            def receive(samples, count, pointer):
                events = ctypes.cast(pointer, ctypes.POINTER(Event))
                index = 0
                while events and events[index].type:
                    if events[index].type == 7:
                        emitted.append(bytes(events[index].id.string).decode('utf-8'))
                    index += 1
                return original(samples, count, pointer)
            callback = callback_type(receive)
            callbacks.append(callback)
            return set_callback(callback)

        with patch('espeakng_loader.load_library', return_value=engine), \
                patch.object(engine, 'espeak_Initialize',
                             side_effect=lambda mode, size, path, options: initialize(mode, size, path, options | 3)) as initialized, \
                patch.object(engine, 'espeak_SetSynthCallback', side_effect=record_callback):
            for gender, settings in [('maschio', {}), ('femmina', {}),
                                     ('maschio', {'rate_wpm': 140, 'pitch': 35, 'range': 95, 'warmth_db': -6}),
                                     ('maschio', {})]:
                emitted.clear()
                samples, rate = synthesize_native('/ra ɾa ɹa ʁa ha ða θa ħa ʕa s̪a ha ʔa xa χa ɣa/',
                                                   'ipa-it', gender, settings)
                self.assertGreater(rate, 0)
                self.assertTrue(samples.size and np.isfinite(samples).all() and np.any(samples))
                # Every consonant must reach synthesis exactly once, including
                # return to the default table after an Arabic-resource word.
                consonants = [p for p in emitted if p and not any(v in p for v in 'aeiouˈˌ')]
                self.assertEqual(consonants, ['r', 'ɾ', 'ɹ', 'ʁ', 'h', 'ð', 'θ', 'ħ', 'ʕ', 's̪', 'h',
                                              'ʔ', 'x', 'χ', 'ɣ'])
                self.assertEqual(engine.espeak_GetParameter(1, 1), settings.get('rate_wpm', 175))
                self.assertEqual(engine.espeak_GetParameter(3, 1), settings.get('pitch', 50))
                self.assertEqual(engine.espeak_GetParameter(4, 1), settings.get('range', 50))
            self.assertEqual(initialized.call_count, 1)

    def test_silence_recovery_is_bounded_and_keeps_exact_input(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest('Signal packages are optional.')
        from unittest.mock import Mock
        import sintesi_lingue as native
        native._close_native_session()
        for silent in (np.array([], dtype=np.float32), np.zeros(200), np.full(200, 1e-8)):
            failed, recovered = Mock(), Mock()
            failed.synthesize.return_value = (silent, 22050)
            recovered.synthesize.return_value = (np.full(200, .1), 22050)
            with patch.object(native, '_SESSION', None), \
                    patch.object(native, '_NativeSession', side_effect=[failed, recovered]) as create:
                result, rate = native.synthesize_native('/ˈpa/', 'ipa-it')
                self.assertEqual(create.call_count, 2)
                failed.close.assert_called_once()
                self.assertEqual(failed.synthesize.call_args, recovered.synthesize.call_args)
                self.assertGreater(float(np.max(abs(result))), .00001)
                self.assertEqual(rate, 22050)
        failed = Mock()
        failed.synthesize.return_value = (np.zeros(200), 22050)
        with patch.object(native, '_SESSION', None), \
                patch.object(native, '_NativeSession', return_value=failed) as create:
            with self.assertRaisesRegex(RuntimeError, '2 tentativi'):
                native.synthesize_native('/ˈpa/', 'ipa-it')
            self.assertEqual(create.call_count, 2)
            self.assertEqual(failed.close.call_count, 2)
            self.assertIsNone(native._SESSION)

    def test_warmth_filter_reduces_high_band_without_duration_change(self):
        try:
            import numpy as np
            import scipy.signal
        except ImportError:
            self.skipTest('Signal packages are optional.')
        from sintesi_lingue import soften_ipa
        rate = 22050
        t = np.arange(rate) / rate
        source = .2 * np.sin(2*np.pi*100*t) + .2 * np.sin(2*np.pi*3000*t)
        result = soften_ipa(source, rate, -6)
        spectrum = np.abs(np.fft.rfft(result[rate//10:]))
        frequencies = np.fft.rfftfreq(len(result[rate//10:]), 1/rate)
        low = spectrum[np.argmin(abs(frequencies-100))]
        high = spectrum[np.argmin(abs(frequencies-3000))]
        self.assertEqual(len(result), len(source))
        self.assertTrue(np.isfinite(result).all())
        self.assertLess(high/low, .6)
        self.assertGreater(low / (len(result[rate//10:])*.1), .9)


if __name__ == '__main__':
    unittest.main()
