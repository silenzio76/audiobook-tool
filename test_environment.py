"""Exercise prerequisite failures and installation planning without downloads."""
import importlib.metadata
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import audiolibro as audio
import environment_check as check
import install_environment as install


class EnvironmentTests(unittest.TestCase):
    def test_espeak_only_jobs_do_not_require_qwen_model_weights(self):
        jobs = [{'parts': [{'language': 'ar', 'text': 'اختبار'}]}]
        with patch.object(check, 'inspect_environment', return_value={'ok': True}) as probe:
            check.ensure_generation_ready('qwen3', jobs)
            self.assertTrue(probe.call_args.kwargs['packages_only'])
            jobs[0]['parts'].append({'language': 'it', 'text': 'Prova'})
            check.ensure_generation_ready('qwen3', jobs)
            self.assertFalse(probe.call_args.kwargs['packages_only'])

    def test_locks_are_exact_and_ranges_are_rejected(self):
        self.assertEqual(len(check.read_pins(check.HERE / 'requirements-editor.txt')), 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements.txt'
            for bad in ('package>=1', 'package', 'package==1\npackage==2'):
                path.write_text(bad, encoding='utf-8')
                with self.assertRaises(ValueError):
                    check.read_pins(path)

    def test_missing_editor_dependencies_report_repair(self):
        with patch.object(check.metadata, 'version', side_effect=importlib.metadata.PackageNotFoundError):
            report = check.check_current('editor')
        self.assertFalse(report['ok'])
        missing = [item for item in report['checks'] if item['name'] == 'PySide6'][0]
        self.assertIn('install_environment.py', missing['fix'])

    def test_missing_windows_voices_are_reported(self):
        with patch.object(check.platform, 'system', return_value='Windows'), \
                patch.object(check, 'windows_voices', return_value=set()), \
                patch.object(check.shutil, 'which', return_value=None):
            report = check.check_current('windows')
        self.assertFalse(report['ok'])
        self.assertTrue(any(item['name'] == 'MSTTS_V110_itIT_CosimoM' and not item['ok'] for item in report['checks']))

    def test_missing_qwen_does_not_start_a_probe(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(check.subprocess, 'run') as run:
            report = check.inspect_environment('qwen', root=Path(directory))
        self.assertFalse(report['ok'])
        run.assert_not_called()

    def test_invalid_qwen_probe_cannot_certify_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / 'python.exe'
            python.write_bytes(b'neutral fixture')
            for stdout in ('not json', json.dumps({'ok': True, 'checks': []})):
                with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, stdout, 'failure')):
                    self.assertFalse(check.inspect_environment('qwen', runtime_python=python)['ok'])

    def test_native_warning_chatter_does_not_corrupt_probe_report(self):
        with tempfile.TemporaryDirectory() as directory:
            python = Path(directory) / 'python.exe'
            python.write_bytes(b'neutral fixture')
            report = {'profile': 'qwen', 'ok': True, 'checks': [{'name': 'fixture', 'ok': True}]}
            stdout = 'Native library warning\n' + check.PROBE_MARKER + json.dumps(report) + '\n'
            with patch.object(check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, 'native warning')):
                self.assertTrue(check.inspect_environment('qwen', runtime_python=python)['ok'])

    def test_preflight_failure_preserves_existing_wav_and_never_runs_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sample.md'
            source.write_text('Una breve frase di prova.\n', encoding='utf-8')
            plan = root / 'plan.json'
            audio.prepare(source, plan)
            output = root / 'existing.wav'
            output.write_bytes(b'keep original bytes')
            with patch.object(check, 'ensure_generation_ready', side_effect=RuntimeError('missing prerequisite')), \
                    patch.object(audio, 'run_backend') as backend:
                with self.assertRaisesRegex(RuntimeError, 'missing prerequisite'):
                    audio.render(plan, output)
                backend.assert_not_called()
            self.assertEqual(output.read_bytes(), b'keep original bytes')

    def test_install_dry_run_plan_never_creates_runtime_or_downloads(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install.platform, 'system', return_value='Windows'):
            root = Path(directory)
            (root / 'requirements-editor.txt').write_text('PySide6==6.11.2\n', encoding='utf-8')
            commands = install.install_plan('editor', root)
            self.assertTrue(any('--only-binary=:all:' in command for command in commands))
            self.assertFalse((root / '.runtime').exists())
            core = install.install_plan('core', root)
            self.assertIn('--without-pip', core[0])

    def test_qwen_python_version_is_checked_before_installation(self):
        with patch.object(install.sys, 'version_info', (3, 14, 0)):
            with self.assertRaisesRegex(ValueError, 'Python 3.12'):
                install.install_plan('qwen')


if __name__ == '__main__':
    unittest.main()
