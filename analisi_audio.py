"""Read-only WAV diagnostics; findings are listening cues, not speech recognition."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import wave


def analyze(path):
    path = Path(path).resolve()
    executable = shutil.which('ffmpeg')
    if not executable:
        raise RuntimeError('FFmpeg richiesto per analizzare volume e silenzi.')
    digest = hashlib.file_digest
    with path.open('rb') as stream:
        audio_hash = digest(stream, 'sha256').hexdigest()
    with wave.open(str(path), 'rb') as stream:
        duration = stream.getnframes() / stream.getframerate()
        fmt = {'channels': stream.getnchannels(), 'sample_rate': stream.getframerate(),
               'sample_width': stream.getsampwidth()}
    result = subprocess.run([executable, '-nostdin', '-hide_banner', '-i', str(path),
                             '-af', 'volumedetect,silencedetect=noise=-45dB:d=1',
                             '-f', 'null', '-'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    report = {'file': str(path), 'audio_sha256': audio_hash, 'duration': duration,
              'format': fmt, 'method': 'FFmpeg; silence <= -45 dBFS for >= 1 second',
              'scope': 'Signal analysis only; pronunciation and intent require listening.',
              'silences': [], 'index_status': 'missing'}
    for label in ('mean_volume', 'max_volume'):
        match = re.search(label + r':\s+(-?[\d.]+|-inf) dB', result.stderr)
        report[label + '_dbfs'] = float(match[1]) if match and match[1] != '-inf' else None
    index = path.with_suffix('.indice.json')
    segments = []
    if index.exists():
        try:
            data = json.loads(index.read_text(encoding='utf-8-sig'))
            report['index_status'] = 'verified' if data.get('audio_sha256') == audio_hash else 'hash_mismatch'
            if report['index_status'] == 'verified':
                segments = data.get('segments', [])
        except (ValueError, OSError):
            report['index_status'] = 'invalid'
    for match in re.finditer(r'silence_end:\s*([\d.]+)\s*\| silence_duration:\s*([\d.]+)', result.stderr):
        end, length = map(float, match.groups())
        start = max(0, end - length)
        entry = {'start': round(start, 3), 'end': round(end, 3), 'duration': length,
                 'review': 'long_pause' if length >= 2 else 'pause'}
        # Use only a hash-verified index and report both sides of a junction.
        entry['segments'] = [s['id'] for i, s in enumerate(segments)
                             if s['seconds'] <= end and
                             (segments[i + 1]['seconds'] if i + 1 < len(segments) else duration) >= start]
        report['silences'].append(entry)
    with path.open('rb') as stream:
        if digest(stream, 'sha256').hexdigest() != audio_hash:
            raise ValueError('Audio modificato durante l’analisi: ripetere la prova.')
    report['long_pause_count'] = sum(s['duration'] >= 2 for s in report['silences'])
    return report


def analyze_directory(directory):
    paths = sorted(Path(directory).glob('*.wav'))
    if not paths:
        raise ValueError('Nessun WAV nella cartella selezionata.')
    return {'version': 1, 'files': [analyze(path) for path in paths]}
