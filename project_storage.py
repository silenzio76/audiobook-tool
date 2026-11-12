"""Validated atomic persistence for project files."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.tmp') as f:
        staged = Path(f.name)
        f.write(data)
    try:
        assert staged.read_bytes() == data
        if path.exists():
            previous = path.read_bytes()
            copy = path.with_name(path.name + '.' + hashlib.sha256(previous).hexdigest()[:12] + '.bak')
            if not copy.exists():
                copy.write_bytes(previous)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)

def save(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    assert json.loads(payload) == data
    atomic(path, payload)
