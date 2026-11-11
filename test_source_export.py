"""Check candidate isolation, reproducibility and failure preservation."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import source_export as export


class SourceExportTests(unittest.TestCase):
    def fixture(self, directory):
        source = Path(directory) / 'source'
        source.mkdir()
        for name in export.EXPORT_FILES:
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(export.HERE / name, source / name)
        return source

    def test_only_allowlisted_files_and_no_local_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            sentinel = uuid.uuid4().hex.encode('ascii')
            for name in ('profili_vocali.json', 'pronunce.json', '.env', 'chapter.md', 'secret.py'):
                (source / name).write_bytes(sentinel)
            for folder in ('Libri', 'voci_qwen', '.runtime', '.git'):
                (source / folder).mkdir()
                (source / folder / 'private.txt').write_bytes(sentinel)
            payloads = export.collect(source)
            payload, manifest = export.make_archive(payloads, 'test.1')
            self.assertFalse(sentinel in payload, 'Excluded fixture content leaked into the archive.')
            self.assertFalse(manifest['release'])
            self.assertEqual(manifest['release_version'], payloads['VERSION'].decode('ascii').strip())
            self.assertIsNone(manifest['source_commit'])
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(set(archive.namelist()), set(export.EXPORT_FILES) | {export.MANIFEST})
                self.assertEqual(json.loads(archive.read(export.MANIFEST)), manifest)

    def test_rebuild_is_identical_despite_source_timestamps_and_order(self):
        payloads = export.collect()
        first, manifest = export.make_archive(payloads, 'test.1')
        second, _ = export.make_archive(dict(reversed(list(payloads.items()))), 'test.1')
        self.assertEqual(first, second)
        export.verify_archive(first, payloads, manifest)
        changed, other = export.make_archive(payloads, 'test.2')
        self.assertNotEqual(first, changed)
        self.assertEqual(manifest['source_snapshot_sha256'], other['source_snapshot_sha256'])

    def test_unexported_local_import_and_invalid_python_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            (source / 'private_helper.py').write_text('VALUE = 1\n', encoding='utf-8')
            target = source / 'analisi_audio.py'
            target.write_text('import private_helper\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unexported local dependency'):
                export.collect(source)
            target.write_text('def incomplete(\n', encoding='utf-8')
            with self.assertRaises(SyntaxError):
                export.collect(source)

    def test_missing_file_and_link_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)
            (source / 'README.md').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing approved'):
                export.collect(source)
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaisesRegex(ValueError, 'Linked or external'):
                export.collect()

    def test_tampered_archive_fails_validation(self):
        payloads = export.collect()
        _, manifest = export.make_archive(payloads, 'test')
        altered = dict(payloads)
        altered['README.md'] += b'changed'
        payload, _ = export.make_archive(altered, 'test')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            export.verify_archive(payload, payloads, manifest)

    def test_failure_keeps_existing_artifact_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = export.export_candidate(root, 'good')
            original = Path(result['artifact']).read_bytes()
            with patch.object(export, 'verify_archive', side_effect=ValueError('validation failed')):
                with self.assertRaisesRegex(ValueError, 'validation failed'):
                    export.export_candidate(root, 'next')
            self.assertEqual(Path(result['artifact']).read_bytes(), original)
            self.assertEqual(len(list(root.iterdir())), 1)
            self.assertEqual(export.export_candidate(root, 'good'), result)
            # The same filename with damaged bytes is preserved for investigation.
            Path(result['artifact']).write_bytes(b'damaged')
            with self.assertRaisesRegex(ValueError, 'differs from its checksum'):
                export.export_candidate(root, 'good')
            self.assertEqual(Path(result['artifact']).read_bytes(), b'damaged')

    def test_invalid_build_identifiers_are_rejected(self):
        payloads = export.collect()
        for value in ('', '../outside', 'build with spaces', 'x' * 81, 'é'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.make_archive(payloads, value)

    @unittest.skipUnless(shutil.which('git'), 'Git is required for release provenance tests')
    def test_release_requires_matching_tag_clean_tree_and_exact_tracked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.fixture(directory)

            def git(*args):
                return subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test Author',
                                       '-c', 'user.email=test@example.invalid',
                                       '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
                                       '-c', 'core.autocrlf=false', *args],
                                      check=True, capture_output=True, text=True).stdout.strip()

            with self.assertRaisesRegex(ValueError, 'dedicated Git'):
                export.export_candidate(Path(directory) / 'out', 'release.1', source, release=True)
            git('init', '--initial-branch=main')
            git('add', '--', *export.EXPORT_FILES)
            git('commit', '-m', 'Neutral release fixture')
            with self.assertRaisesRegex(ValueError, 'Git release check failed'):
                export.export_candidate(Path(directory) / 'out', 'release.1', source, release=True)
            version = (source / 'VERSION').read_text(encoding='ascii').strip()
            git('tag', 'v' + version)
            result = export.export_candidate(Path(directory) / 'out', 'release.1', source, release=True)
            self.assertTrue(result['release'])
            self.assertEqual(result['source_commit'], git('rev-parse', 'HEAD'))
            with zipfile.ZipFile(result['artifact']) as archive:
                manifest = json.loads(archive.read(export.MANIFEST))
                self.assertEqual(manifest['source_tag'], 'v' + version)
            target = source / 'README.md'
            original = target.read_bytes()
            target.write_bytes(original + b'\nChanged\n')
            with self.assertRaisesRegex(ValueError, 'must be clean'):
                export.export_candidate(Path(directory) / 'out', 'release.2', source, release=True)
            git('add', 'README.md')
            git('commit', '-m', 'Changed source fixture')
            with self.assertRaisesRegex(ValueError, 'Version tag must point'):
                export.export_candidate(Path(directory) / 'out', 'release.2', source, release=True)
            # Even a committed extra file cannot enter a release by accident.
            (source / 'unapproved.txt').write_text('Neutral extra fixture', encoding='utf-8')
            git('add', 'unapproved.txt')
            git('commit', '-m', 'Extra file fixture')
            with self.assertRaisesRegex(ValueError, 'Tracked files differ'):
                export.export_candidate(Path(directory) / 'out', 'release.3', source, release=True)


if __name__ == '__main__':
    unittest.main()
