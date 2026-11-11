"""Offline prerequisite checks without downloading models or voice resources."""
import argparse
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROFILES = ('core', 'editor', 'windows', 'qwen', 'diagnostics')
PROBE_MARKER = 'AUDIOBOOK_ENV_REPORT:'
MODELS = {'base': 'Qwen/Qwen3-TTS-12Hz-0.6B-Base',
          'design': 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'}


def read_pins(path):
    pins = {}
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9.+-]+', line):
            raise ValueError('Unpinned dependency in ' + Path(path).name + ': ' + line)
        name, version = line.split('==')
        if name in pins:
            raise ValueError('Duplicate dependency: ' + name)
        pins[name] = version
    return pins


def windows_voices():
    import winreg
    path = r'SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return {winreg.EnumKey(key, index) for index in range(winreg.QueryInfoKey(key)[0])}


def check_current(profile='core', root=HERE, *, deep=False, packages_only=False, model='base', tokens=None):
    root = Path(root).resolve()
    if profile not in PROFILES:
        raise ValueError('Unknown environment profile.')
    checks = []

    def add(name, ok, detail, fix=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail,
                       'fix': '' if ok else fix})

    version = sys.version_info[:2]
    expected_python = version == (3, 12) if profile == 'qwen' else (3, 12) <= version < (3, 15)
    add('python', expected_python, platform.python_version(),
        'Use Python 3.12 for Qwen; use Python 3.12-3.14 for the core/editor.')
    if profile in ('editor', 'windows', 'qwen'):
        add('windows-x64', platform.system() == 'Windows' and struct.calcsize('P') == 8,
            platform.system() + ' / ' + str(struct.calcsize('P') * 8) + ' bit',
            'Use the documented Windows x64 reference environment.')
    if profile in ('editor', 'qwen'):
        lock = root / ('requirements-editor.txt' if profile == 'editor' else 'requirements-qwen.txt')
        for name, expected in read_pins(lock).items():
            try:
                actual = metadata.version(name)
            except metadata.PackageNotFoundError:
                actual = 'missing'
            add(name, actual == expected, actual + ' (expected ' + expected + ')',
                'Run install_environment.py --profile ' + profile + ' using the documented Python.')
    if profile == 'windows':
        add('powershell', shutil.which('powershell.exe') is not None, 'Windows PowerShell executable',
            'Enable Windows PowerShell; do not substitute PowerShell Core for the SAPI helper.')
        try:
            voices = windows_voices() if platform.system() == 'Windows' else set()
        except (OSError, ImportError):
            voices = set()
        for token in sorted(tokens or {'MSTTS_V110_itIT_CosimoM', 'MSTTS_V110_itIT_ElsaM'}):
            add(token, token in voices, 'Installed Windows OneCore voice token',
                'Install the Italian speech voices in Windows language/speech settings.')
    if profile == 'diagnostics':
        ffmpeg = shutil.which('ffmpeg')
        actual = 'missing'
        if ffmpeg:
            try:
                result = subprocess.run([ffmpeg, '-version'], capture_output=True, text=True, timeout=10)
                actual = result.stdout.splitlines()[0] if result.returncode == 0 else 'unusable'
            except (OSError, subprocess.TimeoutExpired, IndexError):
                actual = 'unusable'
        add('ffmpeg', bool(re.search(r'ffmpeg version (?:n)?9\.0\.2(?:[ -]|$)', actual)), actual,
            'Install the documented FFmpeg 9.0.2 build and put its bin directory on PATH.')
    if profile == 'qwen' and not packages_only:
        cache = Path(os.environ.get('HF_HOME', root / '.runtime' / 'huggingface')) / 'hub'
        snapshots = cache / ('models--' + MODELS[model].replace('/', '--')) / 'snapshots'
        candidates = list(snapshots.glob('*/config.json'))
        available = any(list(config.parent.glob('*.safetensors')) for config in candidates)
        add('model-cache', available, MODELS[model],
            'Explicitly download the model with qwen_backend.py download; models are never bundled.')
    if deep and profile in ('editor', 'qwen'):
        try:
            if profile == 'editor':
                from PySide6 import QtWidgets, QtMultimedia, QtSvg
                detail = 'Qt widget, multimedia and SVG modules import successfully.'
            else:
                import torch
                import torchaudio
                from qwen_tts import Qwen3TTSModel
                from espeakng_loader import load_library
                if not torch.cuda.is_available():
                    raise RuntimeError('CUDA GPU is unavailable.')
                if load_library() is None:
                    raise RuntimeError('eSpeak NG native library is unavailable.')
                detail = 'Qwen, torch, torchaudio, CUDA and eSpeak NG are available; no model loaded.'
            add('native-imports', True, detail)
        except Exception as error:
            add('native-imports', False, str(error), 'See INSTALLATION.md and repair this isolated environment.')
    return {'profile': profile, 'scope': 'packages-only' if packages_only else 'prerequisites',
            'ok': all(check['ok'] for check in checks), 'checks': checks}


def inspect_environment(profile='core', root=HERE, *, runtime_python=None, deep=False,
                        packages_only=False, model='base', tokens=None):
    root = Path(root).resolve()
    if profile != 'qwen':
        return check_current(profile, root, deep=deep, packages_only=packages_only, tokens=tokens)
    from library_updates import runtime_python as selected_runtime
    python = Path(runtime_python) if runtime_python else selected_runtime('qwen', root)
    if not python.is_file():
        return {'profile': 'qwen', 'scope': 'prerequisites', 'ok': False, 'checks': [{
            'name': 'qwen-python', 'ok': False, 'detail': 'Isolated Qwen interpreter is missing.',
            'fix': 'Run Python 3.12 install_environment.py --profile qwen. See INSTALLATION.md.'}]}
    command = [str(python), '-X', 'utf8', str(HERE / 'environment_check.py'),
               '--profile', 'qwen', '--current', '--probe-json', '--root', str(root), '--model', model]
    if deep:
        command.append('--deep')
    if packages_only:
        command.append('--packages-only')
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=90 if deep else 20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Native libraries can write console-encoded warnings to either stream.
        # Parse only the explicit protocol line, never library chatter.
        reports = [line[len(PROBE_MARKER):] for line in result.stdout.splitlines()
                   if line.startswith(PROBE_MARKER)]
        if len(reports) != 1:
            raise ValueError('Qwen probe did not return exactly one diagnostic report.')
        report = json.loads(reports[0])
        if not isinstance(report, dict) or type(report.get('ok')) is not bool or not isinstance(report.get('checks'), list):
            raise ValueError('Invalid runtime diagnostic report.')
        if result.returncode != (0 if report['ok'] else 1):
            raise ValueError('Qwen probe exit status disagrees with its diagnostic report.')
        return report
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {'profile': 'qwen', 'scope': 'prerequisites', 'ok': False, 'checks': [{
            'name': 'qwen-probe', 'ok': False, 'detail': str(error),
            'fix': 'Repair the isolated Qwen environment; see INSTALLATION.md.'}]}


def ensure_generation_ready(engine, jobs, root=HERE):
    from lingue_audio import QWEN
    profile = 'qwen' if engine == 'qwen3' else 'windows'
    model = 'design' if any(job.get('design_instruct') for job in jobs) else 'base'
    def uses_model(job):
        parts = job.get('parts') or [{'language': 'auto'}]
        return len({part['language'] for part in parts}) > 1 or any(
            not part.get('audio') and (part['language'] == 'auto' or part['language'] in QWEN)
            for part in parts)
    report = inspect_environment(profile, root, model=model, packages_only=not any(uses_model(job) for job in jobs),
                                 tokens={job['token'] for job in jobs} if profile == 'windows' else None)
    if not report['ok']:
        problems = [item['name'] + ': ' + item['detail'] + ' ' + item['fix']
                    for item in report['checks'] if not item['ok']]
        raise RuntimeError('Synthesis prerequisites failed.\n' + '\n'.join(problems))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, default='core')
    parser.add_argument('--root', type=Path, default=HERE)
    parser.add_argument('--runtime-python', type=Path)
    parser.add_argument('--deep', action='store_true')
    parser.add_argument('--packages-only', action='store_true')
    parser.add_argument('--model', choices=MODELS, default='base')
    parser.add_argument('--current', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--probe-json', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.current:
        result = check_current(args.profile, args.root, deep=args.deep,
                               packages_only=args.packages_only, model=args.model)
    else:
        result = inspect_environment(args.profile, args.root, runtime_python=args.runtime_python,
                                     deep=args.deep, packages_only=args.packages_only, model=args.model)
    if args.probe_json:
        print(PROBE_MARKER + json.dumps(result, ensure_ascii=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
