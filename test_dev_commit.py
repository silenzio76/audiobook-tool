"""Validate build policy and preservation during atomic build updates."""
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch
import dev_commit
import source_export
from dev_commit import atomic_bytes, build_number, needs_build


class BuildPolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'), 'Git is required for commit preservation checks')
    def test_failed_tests_restore_counter_without_committing_or_losing_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in source_export.EXPORT_FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_export.HERE / name, root / name)

            def git(*args):
                return subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test Author',
                                       '-c', 'user.email=test@example.invalid',
                                       '-c', 'commit.gpgsign=false', *args],
                                      check=True, capture_output=True, text=True).stdout.strip()

            git('init', '--initial-branch=main')
            git('add', '--', *source_export.EXPORT_FILES)
            git('commit', '-m', 'Neutral build-policy fixture')
            head = git('rev-parse', 'HEAD')
            previous = (root / 'BUILD').read_bytes()
            target = root / 'version_info.py'
            modified = target.read_bytes() + b'\n# Neutral pending-change fixture.\n'
            target.write_bytes(modified)
            real_run = subprocess.run

            def fail_tests(command, *args, **kwargs):
                if '-I' in command:
                    raise subprocess.CalledProcessError(1, command)
                return real_run(command, *args, **kwargs)

            with patch.object(dev_commit.subprocess, 'run', side_effect=fail_tests):
                with self.assertRaises(subprocess.CalledProcessError):
                    dev_commit.commit_change('Must not commit', root)
            self.assertEqual(git('rev-parse', 'HEAD'), head)
            self.assertEqual(git('diff', '--cached', '--name-only'), '')
            self.assertEqual((root / 'BUILD').read_bytes(), previous)
            self.assertEqual(target.read_bytes(), modified)

    def test_code_and_dependencies_require_builds_but_docs_do_not(self):
        for name in ('audiolibro.py', 'voce_windows.ps1', 'VERSION', 'requirements-editor.txt', '.github/dependabot.yml'):
            self.assertTrue(needs_build({name}))
        self.assertFalse(needs_build({'README.md', 'ROADMAP.md'}))

    def test_build_counter_is_strict(self):
        self.assertEqual(build_number(b'2\n'), 2)
        for invalid in (b'0', b'-1', b'02', b'1.1', b'build.2'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_number(invalid)

    def test_complete_replacement_keeps_original_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'BUILD'
            path.write_bytes(b'1\n')
            atomic_bytes(path, b'2\n')
            self.assertEqual(path.read_bytes(), b'2\n')
            self.assertEqual(next(Path(directory).glob('*.bak')).read_bytes(), b'1\n')
            self.assertFalse(list(Path(directory).glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
