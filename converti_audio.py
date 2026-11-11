"""Convert a local WAV master to MP3, M4B, or audio-only MP4."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Comando audio non riuscito')
    return result.stdout


def probe(binary, path, execute=run):
    info = json.loads(execute([binary, '-v', 'error', '-show_format',
                           '-show_streams', '-of', 'json', str(path)]))
    if not any(s.get('codec_type') == 'audio' for s in info['streams']):
        raise ValueError('Il file non contiene una traccia audio')
    duration = float(info['format']['duration'])
    if duration <= 0:
        raise ValueError('Durata audio non valida')
    return duration


def conversion_tools():
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('FFmpeg e ffprobe devono essere disponibili nel PATH')
    return ffmpeg, ffprobe


def convert(source, output, *, cancel_event=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    suffix = output.suffix.lower()
    if source.suffix.lower() != '.wav' or suffix not in ('.mp3', '.m4b', '.mp4'):
        raise ValueError('Usare un master WAV e una destinazione .mp3, .m4b o .mp4')
    ffmpeg, ffprobe = conversion_tools()
    original_hash = digest(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the same filesystem; publish the final file only after validation.
    with tempfile.TemporaryDirectory(prefix='conversione-', dir=output.parent) as work:
        def execute(command):
            if cancel_event is None:
                return run(command)
            from audiolibro import run_backend
            result = run_backend(command, Path(work), 'ffmpeg', cancel_event)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or 'Comando audio non riuscito')
            return result.stdout

        original_duration = probe(ffprobe, source, execute)
        staged = Path(work) / ('audio' + suffix)
        codec = ['-c:a', 'libmp3lame', '-b:a', '128k'] if suffix == '.mp3' else [
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
            '-f', 'mp4' if suffix == '.mp4' else 'ipod']
        execute([ffmpeg, '-nostdin', '-v', 'error', '-i', str(source),
             '-map', '0:a:0', '-vn', *codec, str(staged)])
        duration = probe(ffprobe, staged, execute)
        if abs(duration - original_duration) > max(1.0, original_duration * .001):
            raise ValueError('La durata convertita non corrisponde al master')
        execute([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(staged),
             '-map', '0:a:0', '-f', 'null', '-'])
        if digest(source) != original_hash:
            raise ValueError('Master cambiato durante la conversione: consegna interrotta')
        if output.exists():
            previous_hash = digest(output)
            backup = output.with_name(output.name + '.' + previous_hash[:12] + '.bak')
            if backup.exists():
                if digest(backup) != previous_hash:
                    raise ValueError('Backup esistente non coerente: destinazione conservata')
            else:
                staged_backup = Path(work) / 'previous.bak'
                shutil.copyfile(output, staged_backup)
                if digest(staged_backup) != previous_hash:
                    raise ValueError('Backup non valido: destinazione conservata')
                os.replace(staged_backup, backup)
        if cancel_event is not None and cancel_event.is_set():
            from pipeline_audio import ReadingCancelled
            raise ReadingCancelled('Conversione interrotta')
        os.replace(staged, output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        print(convert(args.source, args.output))
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        parser.exit(1, str(exc) + '\n')
