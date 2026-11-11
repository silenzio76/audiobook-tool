"""Recoverable publication of matching audio and index files."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import wave

from dev_commit import atomic_bytes
from library_updates import profile_lock
from pipeline_audio import ReadingCancelled


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def index_path(output):
    output = Path(output)
    return output.with_suffix('.mp4.indice.json' if output.suffix.lower() == '.mp4' else '.indice.json')


def journal_path(output):
    output = Path(output)
    return output.with_name(output.name + '.publish.json')


def lock_home(output):
    output = Path(output)
    return output.parent / '.audiobook-publication' / hashlib.sha256(output.name.encode('utf-8')).hexdigest()[:20]


def cancelled(event):
    if event is not None and event.is_set():
        raise ReadingCancelled('Audio publication cancelled; previous delivery retained.')


def validate_pair(audio, data):
    if not Path(audio).is_file() or not Path(audio).stat().st_size:
        raise ValueError('Audio delivery is empty or missing.')
    if data.get('audio_sha256') != digest(audio):
        raise ValueError('Audio/index checksum mismatch.')
    duration = data.get('duration')
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid delivery duration.')
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments:
        raise ValueError('Delivery requires segment timings.')
    for segment in segments:
        if not isinstance(segment, dict) or type(segment.get('frames')) is not int or segment['frames'] <= 0:
            raise ValueError('Invalid segment frame count.')
        start = segment.get('seconds')
        if not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0:
            raise ValueError('Invalid segment start time.')
    total_frames = sum(segment['frames'] for segment in segments)
    offset = 0
    for segment in segments:
        if abs(segment['seconds'] - offset * duration / total_frames) > .002:
            raise ValueError('Segment start times are not contiguous.')
        offset += segment['frames']
    # A staged audio file keeps the real format suffix, even before publication.
    if Path(audio).suffix.lower() == '.wav':
        with wave.open(str(audio), 'rb') as stream:
            frames = stream.getnframes()
            if frames != sum(segment['frames'] for segment in segments):
                raise ValueError('Index frame counts do not cover the WAV.')
            if abs(frames / stream.getframerate() - duration) > .01:
                raise ValueError('Index duration does not match the WAV.')
            remaining = frames
            while remaining:
                count = min(remaining, stream.getframerate() * 10)
                payload = stream.readframes(count)
                if len(payload) != count * stream.getnchannels() * stream.getsampwidth():
                    raise ValueError('Truncated WAV delivery.')
                remaining -= count
    elif Path(audio).suffix.lower() == '.mp4':
        from converti_audio import conversion_tools, probe
        actual = probe(conversion_tools()[1], audio)
        if abs(actual - duration) > max(1.0, duration * .001):
            raise ValueError('Index duration does not match the converted audio.')
    else:
        raise ValueError('Paired publication supports WAV and MP4 deliveries.')


def staged_copy(source, output):
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.' + output.name + '.publish-',
                                     suffix=output.suffix, delete=False) as handle:
        staged = Path(handle.name)
    try:
        shutil.copyfile(source, staged)
        if digest(staged) != digest(source):
            raise ValueError('Staged audio copy did not validate.')
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def backup(path):
    if not path.exists():
        return None
    checksum = digest(path)
    target = path.with_name(path.name + '.' + checksum[:12] + '.bak')
    if target.exists():
        if digest(target) != checksum:
            raise ValueError('Existing delivery backup has changed.')
    else:
        staged = staged_copy(path, path)
        try:
            os.replace(staged, target)
        finally:
            staged.unlink(missing_ok=True)
    return {'name': target.name, 'sha256': checksum}


def validated_journal(output):
    data = json.loads(journal_path(output).read_text(encoding='utf-8'))
    index = index_path(output)
    required = {'version', 'output', 'index', 'old_audio', 'old_index', 'new_audio_sha256',
                'new_index_sha256', 'staged_audio', 'staged_index'}
    if not isinstance(data, dict) or not required.issubset(data):
        raise ValueError('Incomplete publication journal; recovery stopped.')
    if data.get('version') != 1 or data.get('output') != output.name or data.get('index') != index.name:
        raise ValueError('Publication journal belongs to another delivery.')
    for label, suffix in (('staged_audio', output.suffix), ('staged_index', '.json')):
        name = data[label]
        if (not isinstance(name, str) or Path(name).name != name
                or not name.startswith('.' + output.name + '.publish-') or not name.endswith(suffix)):
            raise ValueError('Invalid publication staging locator.')
    for label, target in (('old_audio', output), ('old_index', index)):
        item = data.get(label)
        if item is None:
            continue
        checksum = item.get('sha256', '')
        if (len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum)
                or item.get('name') != target.name + '.' + checksum[:12] + '.bak'):
            raise ValueError('Invalid recovery backup locator.')
        candidate = output.parent / item['name']
        if candidate.is_symlink() or digest(candidate) != checksum:
            raise ValueError('Recovery backup is missing or altered; preserve the journal.')
    return data


def restore(path, old):
    if old is None:
        path.unlink(missing_ok=True)
        return
    if path.exists() and digest(path) == old['sha256']:
        return
    candidate = path.parent / old['name']
    staged = staged_copy(candidate, path)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def recover_locked(output):
    journal = journal_path(output)
    if not journal.exists():
        return False
    data = validated_journal(output)
    index = index_path(output)
    complete = (output.exists() and index.exists()
                and digest(output) == data.get('new_audio_sha256')
                and digest(index) == data.get('new_index_sha256'))
    if complete:
        validate_pair(output, json.loads(index.read_text(encoding='utf-8')))
    else:
        restore(output, data['old_audio'])
        restore(index, data['old_index'])
    for label, checksum in (('staged_audio', data['new_audio_sha256']), ('staged_index', data['new_index_sha256'])):
        staged = output.parent / data[label]
        if staged.exists():
            if staged.is_symlink() or digest(staged) != checksum:
                raise ValueError('Unexpected staging content; preserve journal for inspection.')
            staged.unlink()
    journal.unlink()
    return True


def recover_audio(output):
    """Recover an interrupted pair before this software reads or republishes it."""
    output = Path(output).absolute()
    if not journal_path(output).exists():
        return False
    with profile_lock(lock_home(output)):
        return recover_locked(output)


def publish_audio(source, output, metadata, cancel_event=None):
    """Validate both files, journal old state, publish and recover on failure."""
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    index = index_path(output)
    if output.is_symlink() or index.is_symlink() or journal_path(output).is_symlink():
        raise ValueError('Linked delivery destinations are not supported.')
    with profile_lock(lock_home(output)):
        recover_locked(output)
        cancelled(cancel_event)
        audio = staged_copy(Path(source), output)
        staged_index = None
        try:
            data = dict(metadata, audio_sha256=digest(audio))
            validate_pair(audio, data)
            payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.' + output.name + '.publish-',
                                             suffix='.json', delete=False) as handle:
                staged_index = Path(handle.name)
            staged_index.write_bytes(payload)
            if json.loads(staged_index.read_bytes()) != data:
                raise ValueError('Staged index did not validate.')
            journal = {'version': 1, 'output': output.name, 'index': index.name,
                       'old_audio': backup(output), 'old_index': backup(index),
                       'staged_audio': audio.name, 'staged_index': staged_index.name,
                       'new_audio_sha256': digest(audio), 'new_index_sha256': digest(staged_index)}
            cancelled(cancel_event)
            atomic_bytes(journal_path(output), json.dumps(journal).encode('utf-8'))
            try:
                os.replace(audio, output)
                cancelled(cancel_event)
                os.replace(staged_index, index)
                cancelled(cancel_event)
                validate_pair(output, json.loads(index.read_text(encoding='utf-8')))
                journal_path(output).unlink()
            except Exception:
                recover_locked(output)
                raise
            return output
        finally:
            audio.unlink(missing_ok=True)
            if staged_index is not None:
                staged_index.unlink(missing_ok=True)
