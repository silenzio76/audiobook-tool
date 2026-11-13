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
    def test_windows_profile_routes_ipa_to_native_runtime_before_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('profili_vocali.CONFIG', root / 'absent.json'):
                profiles = load()
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

    def test_vowel_quality_and_stress(self):
        self.assertEqual(phoneme_input('/ˈpɛs.ka/'), "[[p'Eska]]")
        self.assertEqual(phoneme_input('/ˈpes.ka/'), "[[p'eska]]")
        self.assertEqual(phoneme_input('anˈkora'), "[[ank'ora]]")

    def test_length_affricates_and_punctuation(self):
        self.assertEqual(phoneme_input('ˈpalːa, ˈt͡ʃɛna.'), "[[p'al:a]], [[tS'Ena]].")
        self.assertNotEqual(phoneme_input('ˈpala'), phoneme_input('ˈpalla'))

    def test_ipa_bypasses_italian_normalization(self):
        text = '/ˈpɛska/'
        self.assertEqual(speech_parts(text, 'ipa-it'), [{'text':text,'language':'ipa-it'}])

    def test_unsupported_or_incomplete_input_fails(self):
        for text in ('', 'ˈ', 'ˈp ', 'ːa', 'ã', 'ɬa', '[[a]]', '<speak>a</speak>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                phoneme_input(text)


if __name__ == '__main__':
    unittest.main()
