"""Original neutral fixtures for validating an isolated source candidate."""
from pathlib import Path
import tempfile
import unittest

import audiolibro as audio
from lingue_audio import lexicon
from profili_vocali import load, validate
from version_info import __version__


class StandaloneTests(unittest.TestCase):
    def test_authoritative_version(self):
        self.assertEqual(__version__, Path(__file__).with_name('VERSION').read_text(encoding='ascii').strip())

    def test_neutral_document_roundtrip_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample.md'
            text = '# Sample\n\nLa finestra era aperta.\n\n«Arrivi?» domandò la custode.\n'
            source.write_text(text, encoding='utf-8')
            plan = root / 'sample.json'
            prepared = audio.prepare(source, plan)
            self.assertEqual(audio.read_plan(plan), prepared)
            self.assertEqual([s['role'] for s in prepared['segments']],
                             ['Narratore', 'Da assegnare', 'Narratore'])
            self.assertEqual(source.read_text(encoding='utf-8'), text)
            with self.assertRaises(ValueError):
                audio.prepare(source, plan)

    def test_profiles_and_lexicon_are_valid_without_personal_files(self):
        # In the extracted candidate these files are absent by design.
        self.assertEqual(validate(load())['version'], 1)
        self.assertIsInstance(lexicon(), dict)

    def test_explicit_pause_overrides_automatic_timing(self):
        segment = {'id': 1, 'line': 1, 'text': '«Arrivi?»', 'pause_ms': 875}
        following = {'id': 2, 'line': 1, 'text': 'domandò la custode.'}
        self.assertEqual(audio.segment_pause_ms(segment, following), 875)
        segment.pop('pause_ms')
        self.assertEqual(audio.segment_pause_ms(segment, following), 240)


if __name__ == '__main__':
    unittest.main()
