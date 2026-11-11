"""Increment a code build, run regressions and commit approved changes."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from source_export import EXPORT_FILES, HERE


def atomic_bytes(path, payload):
    """Validate complete staged bytes and preserve previous content."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as handle:
        staged = Path(handle.name)
    try:
        staged.write_bytes(payload)
        if staged.read_bytes() != payload:
            raise ValueError('Staged content did not validate: ' + path.name)
        if path.exists():
            previous = path.read_bytes()
            backup = path.with_name(path.name + '.' + hashlib.sha256(previous).hexdigest()[:12] + '.bak')
            if not backup.exists():
                backup.write_bytes(previous)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def needs_build(names):
    return any(Path(name).suffix in ('.py', '.ps1', '.cmd', '.sh', '.yml', '.yaml')
               or name in ('VERSION', 'BUILD', '.gitattributes')
               or name.startswith('requirements-') for name in names)


def build_number(payload):
    text = payload.decode('ascii').strip()
    if not text.isdigit() or int(text) < 1 or str(int(text)) != text:
        raise ValueError('BUILD must contain one canonical positive integer.')
    return int(text)


def commit_change(message, root=HERE):
    root = Path(root).resolve()

    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, text=True, encoding='utf-8').stdout.strip()

    if not message.strip():
        raise ValueError('A meaningful commit message is required.')
    if git('diff', '--cached', '--name-only'):
        raise ValueError('Index is already staged; preserve and review it before automatic commit.')
    changed = set(filter(None, git('diff', '--name-only', '-z', 'HEAD').split('\0')))
    changed.update(filter(None, git('ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    if not changed:
        raise ValueError('No changes to commit.')
    if changed - set(EXPORT_FILES):
        raise ValueError('Unapproved files: ' + ', '.join(sorted(changed - set(EXPORT_FILES))))
    from source_export import collect
    collect(root)
    previous = (root / 'BUILD').read_bytes()
    current = build_number(previous)
    old = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:BUILD'], capture_output=True)
    baseline = build_number(old.stdout) if old.returncode == 0 else 1
    if current != baseline:
        raise ValueError('BUILD was changed manually; reconcile it with HEAD before automatic increment.')
    code_changed = needs_build(changed)
    number = baseline + 1 if code_changed else baseline
    if code_changed:
        atomic_bytes(root / 'BUILD', (str(number) + '\n').encode('ascii'))
        changed.add('BUILD')
    test_code = (
        'import sys,unittest; sys.path.insert(0,"."); '
        'suite=unittest.defaultTestLoader.discover(".",pattern="test_*.py"); '
        'result=unittest.TextTestRunner().run(suite); sys.exit(not result.wasSuccessful())'
    )
    try:
        subprocess.run([sys.executable, '-I', '-S', '-X', 'utf8', '-c', test_code], cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError):
        if code_changed:
            atomic_bytes(root / 'BUILD', previous)
        raise
    git('add', '--', *sorted(changed))
    staged = set(filter(None, git('diff', '--cached', '--name-only', '-z').split('\0')))
    if staged != changed:
        raise ValueError('Staged files differ from verified changes; no commit created.')
    title = message.strip() + (f' [build {number}]' if code_changed else '')
    git('commit', '-m', title)
    return {'commit': git('rev-parse', 'HEAD'), 'build': number, 'files': len(changed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message', required=True)
    args = parser.parse_args()
    try:
        result = commit_change(args.message)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Automatic commit failed: {error}\n')
    print(f"Committed {result['commit']} at build {result['build']} ({result['files']} files).")


if __name__ == '__main__':
    main()
