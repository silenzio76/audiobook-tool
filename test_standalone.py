"""Original neutral fixtures for validating an isolated source candidate."""
from pathlib import Path
import json
import queue
import threading
import wave
import tempfile
import unittest
from unittest.mock import patch

import audiolibro as audio
from lingue_audio import lexicon
from profili_vocali import load, validate
from version_info import __version__, __build__, __build_version__


class StandaloneTests(unittest.TestCase):
    def test_timed_playback_uses_extracted_player_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\x00\x00' * 8000)
            original = path.read_bytes()

            def inspect_clip(clip, stop, events):
                with wave.open(str(clip), 'rb') as stream:
                    self.assertEqual(stream.getnframes(), 2000)

            with patch('audio_playback._play_wav', side_effect=inspect_clip) as player:
                audio.play_audio(path, stop_event=threading.Event(), events=queue.Queue(), limit_ms=250)
            player.assert_called_once()
            self.assertFalse(player.call_args.args[0].exists())
            self.assertEqual(path.read_bytes(), original)

    def test_core_entry_points_reexport_extracted_services(self):
        import audio_playback
        import project_storage
        for name in ('atomic', 'save'):
            self.assertIs(getattr(audio, name), getattr(project_storage, name))
        for name in ('write_reading', 'write_junction', 'save_reading', 'play_audio', '_play_wav'):
            self.assertIs(getattr(audio, name), getattr(audio_playback, name))

    def test_recorded_narrator_pitch_reaches_backend_in_both_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / 'reference.json'
            reference.write_text(json.dumps({'origin': 'recorded-reference'}), encoding='utf-8')
            with patch('profili_vocali.CONFIG', root / 'absent.json'):
                profiles = load()
            profiles['characters']['Narratore'].update(
                engine='qwen3', reference=str(reference), pitch_shift_st=-4)
            source = root / 'source.md'
            source.write_text('La finestra era aperta.\n', encoding='utf-8')
            plan = root / 'plan.json'
            audio.prepare(source, plan)
            for mode in ('narratore', 'cast'):
                with self.subTest(mode=mode), patch('audiolibro.load_profiles', return_value=profiles), \
                        patch('audiolibro.reference_hashes', return_value={}), \
                        patch('environment_check.ensure_generation_ready', side_effect=RuntimeError('captured')) as ready:
                    with self.assertRaisesRegex(RuntimeError, 'captured'):
                        audio.render(plan, root / 'out.wav', voice_mode=mode)
                    self.assertEqual(ready.call_args.args[1][0]['character_pitch'], -4)

    def test_qwen_pitch_shift_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / 'reference.json'
            reference.write_text('{}', encoding='utf-8')
            with patch('profili_vocali.CONFIG', Path(directory) / 'absent.json'):
                profiles = load()
            narrator = profiles['characters']['Narratore']
            narrator.update(engine='qwen3', reference=str(reference), pitch_shift_st=-4)
            self.assertEqual(validate(profiles)['characters']['Narratore']['pitch_shift_st'], -4)
            for invalid in (True, 'four', float('nan'), float('inf'), -13, 13):
                narrator['pitch_shift_st'] = invalid
                with self.assertRaises(ValueError):
                    validate(profiles)
            narrator.update(engine='windows', pitch_shift_st=-4)
            with self.assertRaises(ValueError):
                validate(profiles)

    def test_authoritative_version(self):
        self.assertEqual(__version__, Path(__file__).with_name('VERSION').read_text(encoding='ascii').strip())
        self.assertEqual(__build_version__, f'{__version__}+build.{__build__}')

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
