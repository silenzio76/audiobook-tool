"""Install a pinned core/editor/Qwen environment without modifying global Python."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

from environment_check import HERE, read_pins


def install_plan(profile, root=HERE):
    root = Path(root).resolve()
    if profile not in ('core', 'editor', 'qwen'):
        raise ValueError('Unknown installation profile.')
    version = sys.version_info[:2]
    if not (3, 12) <= version < (3, 15) or (profile == 'qwen' and version != (3, 12)):
        raise ValueError('Use Python 3.12 for Qwen, or Python 3.12-3.14 for core/editor.')
    if platform.system() != 'Windows':
        raise ValueError('This installer targets Windows. Core tests remain portable.')
    runtime = root / '.runtime'
    target = runtime / profile
    if runtime.is_symlink() or target.is_symlink() or target.resolve().parent != runtime.resolve():
        raise ValueError('Refusing a linked or external installation directory.')
    python = target / 'Scripts' / 'python.exe'
    commands = []
    if not python.exists():
        creation = [sys.executable, '-m', 'venv']
        if profile == 'core':
            creation.append('--without-pip')
        commands.append(creation + [str(target)])
    else:
        probe = subprocess.run([str(python), '-c',
                                'import json,sys; print(json.dumps([list(sys.version_info[:2]),sys.prefix!=sys.base_prefix]))'],
                               check=True, capture_output=True, text=True)
        installed, isolated = json.loads(probe.stdout)
        if tuple(installed) != version or not isolated:
            raise ValueError('Existing runtime uses another Python or is not isolated; preserve it and select its interpreter.')
    if profile == 'editor':
        lock = root / 'requirements-editor.txt'
        read_pins(lock)
        commands.append([str(python), '-m', 'ensurepip'])
        commands.append([str(python), '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(lock)])
    elif profile == 'qwen':
        torch_lock = root / 'requirements-qwen-torch.txt'
        lock = root / 'requirements-qwen.txt'
        read_pins(torch_lock)
        read_pins(lock)
        commands.append([str(python), '-m', 'ensurepip'])
        commands.append([str(python), '-m', 'pip', 'install', '-r', str(torch_lock),
                         '--index-url', 'https://download.pytorch.org/whl/cu126'])
        commands.append([str(python), '-m', 'pip', 'install', '-r', str(lock)])
    if profile != 'core':
        commands.append([str(python), '-m', 'pip', 'check'])
    commands.append([str(python), '-X', 'utf8', str(root / 'environment_check.py'),
                     '--profile', profile, '--current', '--packages-only', '--root', str(root)])
    return commands


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('core', 'editor', 'qwen'), default='core')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        commands = install_plan(args.profile)
        if args.dry_run:
            print(json.dumps(commands, indent=2))
            return
        for command in commands:
            subprocess.run(command, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Installation failed; existing files are preserved: {error}\nSee INSTALLATION.md.\n')


if __name__ == '__main__':
    main()
