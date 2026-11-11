"""Inject publication failures and crashes to verify coherent recovery."""
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import wave

import audio_publication as publication
from pipeline_audio import ReadingCancelled


class PublicationTests(unittest.TestCase):
    def audio(self, path, value):
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(bytes([value, 0]) * 800)
        return path

    def fixture(self, directory):
        root = Path(directory)
        old = self.audio(root / 'old.wav', 1)
        new = self.audio(root / 'new.wav', 2)
        output = root / 'delivery.wav'
        metadata = {'duration': .1, 'segments': [{'seconds': 0, 'frames': 800}]}
        publication.publish_audio(old, output, metadata)
        return new, output, metadata, output.read_bytes(), publication.index_path(output).read_bytes()

    def test_success_delivers_matching_hashes_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, previous, _ = self.fixture(directory)
            publication.publish_audio(new, output, metadata)
            index = json.loads(publication.index_path(output).read_text(encoding='utf-8'))
            publication.validate_pair(output, index)
            self.assertEqual(output.read_bytes(), new.read_bytes())
            self.assertTrue(any(path.read_bytes() == previous for path in output.parent.glob('delivery.wav.*.bak')))
            self.assertFalse(publication.journal_path(output).exists())

    def test_invalid_frames_preserve_existing_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, old_audio, old_index = self.fixture(directory)
            metadata['segments'][0]['frames'] = 799
            with self.assertRaisesRegex(ValueError, 'frame counts'):
                publication.publish_audio(new, output, metadata)
            self.assertEqual(output.read_bytes(), old_audio)
            self.assertEqual(publication.index_path(output).read_bytes(), old_index)

    def fail_index(self, output, crash=False, event=None):
        real_replace = publication.os.replace

        def replace(source, destination):
            source, destination = Path(source), Path(destination)
            if destination == publication.index_path(output) and '.publish-' in source.name:
                if event is not None:
                    event.set()
                if crash:
                    raise KeyboardInterrupt('simulated process loss')
                raise OSError('simulated index failure')
            return real_replace(source, destination)

        return patch.object(publication.os, 'replace', side_effect=replace)

    def test_index_failure_restores_previous_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, old_audio, old_index = self.fixture(directory)
            with self.fail_index(output), self.assertRaisesRegex(OSError, 'index failure'):
                publication.publish_audio(new, output, metadata)
            self.assertEqual(output.read_bytes(), old_audio)
            self.assertEqual(publication.index_path(output).read_bytes(), old_index)
            self.assertFalse(publication.journal_path(output).exists())

    def test_interrupted_process_is_recovered_on_next_read(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, old_audio, old_index = self.fixture(directory)
            with self.fail_index(output, crash=True), self.assertRaises(KeyboardInterrupt):
                publication.publish_audio(new, output, metadata)
            self.assertTrue(publication.journal_path(output).exists())
            journal = json.loads(publication.journal_path(output).read_text(encoding='utf-8'))
            # Recreate a staged file left by an OS/process loss without finally.
            stage = output.parent / journal['staged_index']
            stage.write_bytes(json.dumps(dict(metadata, audio_sha256=publication.digest(new)),
                                         ensure_ascii=False, indent=2).encode('utf-8'))
            self.assertTrue(publication.recover_audio(output))
            self.assertFalse(stage.exists())
            self.assertEqual(output.read_bytes(), old_audio)
            self.assertEqual(publication.index_path(output).read_bytes(), old_index)

    def test_complete_pair_is_kept_if_process_dies_before_journal_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, _, _ = self.fixture(directory)
            real_validate = publication.validate_pair

            def validate(path, data):
                if Path(path) == output:
                    raise KeyboardInterrupt('simulated loss after both replacements')
                return real_validate(path, data)

            with patch.object(publication, 'validate_pair', side_effect=validate), self.assertRaises(KeyboardInterrupt):
                publication.publish_audio(new, output, metadata)
            self.assertTrue(publication.recover_audio(output))
            self.assertEqual(output.read_bytes(), new.read_bytes())
            publication.validate_pair(output, json.loads(publication.index_path(output).read_text(encoding='utf-8')))

    def test_new_delivery_is_removed_when_index_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'delivery.wav'
            new = self.audio(Path(directory) / 'new.wav', 2)
            metadata = {'duration': .1, 'segments': [{'seconds': 0, 'frames': 800}]}
            with self.fail_index(output), self.assertRaises(OSError):
                publication.publish_audio(new, output, metadata)
            self.assertFalse(output.exists())
            self.assertFalse(publication.index_path(output).exists())

    def test_cancellation_between_replacements_restores_previous_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, old_audio, old_index = self.fixture(directory)
            event = Event()
            real_replace = publication.os.replace

            def replace(source, destination):
                result = real_replace(source, destination)
                if Path(destination) == output:
                    event.set()
                return result

            with patch.object(publication.os, 'replace', side_effect=replace), self.assertRaises(ReadingCancelled):
                publication.publish_audio(new, output, metadata, event)
            self.assertEqual(output.read_bytes(), old_audio)
            self.assertEqual(publication.index_path(output).read_bytes(), old_index)

    def test_altered_backup_blocks_recovery_without_discarding_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            new, output, metadata, _, _ = self.fixture(directory)
            with self.fail_index(output, crash=True), self.assertRaises(KeyboardInterrupt):
                publication.publish_audio(new, output, metadata)
            journal = json.loads(publication.journal_path(output).read_text(encoding='utf-8'))
            (output.parent / journal['old_audio']['name']).write_bytes(b'altered backup')
            with self.assertRaisesRegex(ValueError, 'missing or altered'):
                publication.recover_audio(output)
            self.assertTrue(publication.journal_path(output).exists())


if __name__ == '__main__':
    unittest.main()
