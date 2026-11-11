"""Validate build policy and preservation during atomic build updates."""
from pathlib import Path
import tempfile
import unittest
from dev_commit import atomic_bytes, build_number, needs_build


class BuildPolicyTests(unittest.TestCase):
    def test_code_and_dependencies_require_builds_but_docs_do_not(self):
        for name in ('audiolibro.py', 'voce_windows.ps1', 'VERSION', 'requirements-editor.txt'):
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
