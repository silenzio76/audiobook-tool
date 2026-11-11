"""Automatically adopt project-verified library pins in separate runtime slots."""
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from environment_check import HERE, read_pins
from dev_commit import atomic_bytes


def update_home(profile, root=HERE):
    if profile not in ('editor', 'qwen'):
        raise ValueError('Library updates support editor or qwen profiles.')
    root = Path(root).resolve()
    path = root / '.runtime' / 'library_updates' / profile
    for component in (root / '.runtime', path.parent, path):
        if component.is_symlink():
            raise ValueError('Linked update directories are not supported.')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Update directory escaped the project.')
    return path


def state_for(profile, root=HERE):
    path = update_home(profile, root) / 'state.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def runtime_python(profile, root=HERE):
    """Resolve a verified slot, retaining the fixed factory runtime as fallback."""
    home = update_home(profile, root)
    active = state_for(profile, root).get('active')
    if not active:
        return Path(root).resolve() / '.runtime' / profile / 'Scripts' / 'python.exe'
    return slot_python(home, profile, active)


def slot_python(home, profile, active):
    if not re.fullmatch(r'env-[0-9a-f]{32}', active):
        raise ValueError('Invalid active library runtime identifier.')
    slot = home / active
    if slot.is_symlink() or slot.resolve().parent != home.resolve():
        raise ValueError('Active library runtime escaped its profile.')
    verification = json.loads((slot / 'verified.json').read_text(encoding='utf-8'))
    lock = slot / 'verified-requirements.txt'
    if (verification.get('ok') is not True or verification.get('profile') != profile
            or verification.get('pins_sha256') != hashlib.sha256(lock.read_bytes()).hexdigest()):
        raise ValueError('Active library verification is missing or altered.')
    python = slot / 'Scripts' / 'python.exe'
    if not python.is_file():
        raise ValueError('Active library interpreter is missing.')
    return python


@contextmanager
def profile_lock(home):
    """Use an OS-released lock so a killed updater cannot leave a stale lease."""
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'update.lock').open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute(command, *, timeout=300):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[-3000:])
    return result.stdout


def matches_pins(python, pins):
    if not python.is_file():
        return False
    code = ('import importlib.metadata as m,json,sys; '
            'print(json.dumps({n:m.version(n) for n in sys.argv[1:]}))')
    try:
        actual = json.loads(execute([str(python), '-c', code, *pins], timeout=20))
        return actual == pins
    except (RuntimeError, ValueError, OSError):
        return False


def update_profile(profile, root=HERE, *, force=False):
    root = Path(root).resolve()
    home = update_home(profile, root)
    lock = root / ('requirements-editor.txt' if profile == 'editor' else 'requirements-qwen.txt')
    pins = read_pins(lock)
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()
    with profile_lock(home):
        state = state_for(profile, root)
        now = time.time()
        if state.get('paused') and not force:
            return {'profile': profile, 'status': 'paused-after-rollback'}
        if not force and state.get('pins_sha256') == fingerprint and now - state.get('checked_at', 0) < 86400:
            return {'profile': profile, 'status': 'already-checked'}
        previous_python = runtime_python(profile, root)
        if matches_pins(previous_python, pins):
            state.update(checked_at=now, pins_sha256=fingerprint, paused=False)
            atomic_bytes(home / 'state.json', json.dumps(state).encode('utf-8'))
            return {'profile': profile, 'status': 'current'}
        creator = previous_python if previous_python.exists() else Path(sys.executable)
        if profile == 'qwen' and not previous_python.exists():
            raise RuntimeError('Install the optional Qwen profile with Python 3.12 before automatic updates.')
        slot = home / ('env-' + uuid.uuid4().hex)
        slot.mkdir()
        python = slot / 'Scripts' / 'python.exe'
        try:
            execute([str(creator), '-m', 'venv', str(slot)])
            if profile == 'qwen':
                execute([str(python), '-m', 'pip', 'install', '--only-binary=:all:', '-r',
                         str(root / 'requirements-qwen-torch.txt'), '--index-url', 'https://download.pytorch.org/whl/cu126'])
            execute([str(python), '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(lock)])
            execute([str(python), '-m', 'pip', 'check'])
            execute([str(python), '-X', 'utf8', str(root / 'environment_check.py'),
                     '--profile', profile, '--current', '--packages-only', '--deep', '--root', str(root)])
            if not matches_pins(python, pins):
                raise RuntimeError('Updated runtime does not match the project lock.')
            atomic_bytes(slot / 'verified-requirements.txt', lock.read_bytes())
            verification = {'ok': True, 'profile': profile, 'pins_sha256': fingerprint, 'verified_at': time.time()}
            atomic_bytes(slot / 'verified.json', json.dumps(verification).encode('utf-8'))
            # The source must not change between validation and activation.
            if hashlib.sha256(lock.read_bytes()).hexdigest() != fingerprint:
                raise RuntimeError('Dependency lock changed during update; candidate was not activated.')
            new_state = dict(state, active=slot.name, previous=state.get('active'),
                             checked_at=now, pins_sha256=fingerprint, paused=False)
            atomic_bytes(home / 'state.json', json.dumps(new_state).encode('utf-8'))
            return {'profile': profile, 'status': 'updated', 'runtime': str(python)}
        except Exception as error:
            atomic_bytes(slot / 'failed.txt', str(error).encode('utf-8'))
            raise


def rollback(profile, root=HERE):
    home = update_home(profile, root)
    with profile_lock(home):
        state = state_for(profile, root)
        if not state.get('active'):
            raise ValueError('No activated update to roll back.')
        previous = state.get('previous')
        candidate = dict(state, active=previous, previous=state['active'], checked_at=0, paused=True)
        if previous:
            if not re.fullmatch(r'env-[0-9a-f]{32}', previous):
                raise ValueError('Invalid rollback runtime.')
            slot_python(home, profile, previous)
        elif not (Path(root) / '.runtime' / profile / 'Scripts' / 'python.exe').is_file():
            raise ValueError('Original factory runtime is unavailable.')
        atomic_bytes(home / 'state.json', json.dumps(candidate).encode('utf-8'))
        return {'profile': profile, 'status': 'rolled-back'}


def automatic_updates(root=HERE):
    """Check on desktop startup, at most daily; opt out without editing code."""
    if os.environ.get('AUDIOBOOK_AUTO_UPDATE', '1') == '0':
        return
    for profile in ('editor', 'qwen'):
        try:
            if profile == 'qwen' and not runtime_python(profile, root).exists():
                continue
            result = update_profile(profile, root)
            if result['status'] == 'updated':
                print('External libraries updated: ' + profile, file=sys.stderr)
        except Exception as error:
            print(f'Library update {profile} deferred: {error}', file=sys.stderr)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('editor', 'qwen'), default='editor')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    try:
        result = rollback(args.profile) if args.rollback else update_profile(args.profile, force=True)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Library update failed; previous runtime retained: {error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
