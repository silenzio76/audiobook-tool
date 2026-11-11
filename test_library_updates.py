"""Check isolated activation, failure preservation, daily checks and rollback."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import library_updates as updates


class LibraryUpdateTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        (root / 'requirements-editor.txt').write_text('PySide6==6.11.2\n', encoding='utf-8')
        factory = root / '.runtime' / 'editor' / 'Scripts' / 'python.exe'
        factory.parent.mkdir(parents=True)
        factory.write_bytes(b'old interpreter')
        return root, factory

    def test_matching_pins_do_not_install_and_daily_check_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root, factory = self.fixture(directory)
            with patch.object(updates, 'matches_pins', return_value=True), patch.object(updates, 'execute') as execute:
                self.assertEqual(updates.update_profile('editor', root)['status'], 'current')
                self.assertEqual(updates.update_profile('editor', root)['status'], 'already-checked')
                execute.assert_not_called()
            self.assertEqual(factory.read_bytes(), b'old interpreter')

    def test_failed_install_preserves_active_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root, factory = self.fixture(directory)
            with patch.object(updates, 'matches_pins', return_value=False), \
                    patch.object(updates, 'execute', side_effect=RuntimeError('fixture download failure')):
                with self.assertRaisesRegex(RuntimeError, 'download failure'):
                    updates.update_profile('editor', root)
            self.assertEqual(updates.runtime_python('editor', root), factory)
            self.assertEqual(factory.read_bytes(), b'old interpreter')
            self.assertTrue(list(updates.update_home('editor', root).glob('env-*/failed.txt')))

    def test_successful_activation_and_rollback_do_not_move_environments(self):
        with tempfile.TemporaryDirectory() as directory:
            root, factory = self.fixture(directory)

            def execute(command, **kwargs):
                if '-m' in command and 'venv' in command:
                    python = Path(command[-1]) / 'Scripts' / 'python.exe'
                    python.parent.mkdir()
                    python.write_bytes(b'new interpreter')
                return ''

            with patch.object(updates, 'matches_pins', side_effect=[False, True]), patch.object(updates, 'execute', side_effect=execute):
                result = updates.update_profile('editor', root)
            self.assertEqual(result['status'], 'updated')
            active = updates.runtime_python('editor', root)
            self.assertEqual(active.read_bytes(), b'new interpreter')
            self.assertEqual(updates.rollback('editor', root)['status'], 'rolled-back')
            self.assertEqual(updates.runtime_python('editor', root), factory)
            self.assertTrue(active.exists())
            self.assertEqual(updates.update_profile('editor', root)['status'], 'paused-after-rollback')
            with patch.object(updates, 'matches_pins', return_value=True):
                self.assertEqual(updates.update_profile('editor', root, force=True)['status'], 'current')
            self.assertFalse(updates.state_for('editor', root)['paused'])

    def test_tampered_runtime_lock_and_path_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.fixture(directory)
            home = updates.update_home('editor', root)
            home.mkdir(parents=True)
            (home / 'state.json').write_text(json.dumps({'active': '../outside'}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'identifier'):
                updates.runtime_python('editor', root)


if __name__ == '__main__':
    unittest.main()
