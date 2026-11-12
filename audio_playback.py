"""WAV assembly, publication and local Qt playback."""
import json
from pathlib import Path
import queue
import tempfile
import wave
from pipeline_audio import ReadingCancelled
from montaggio_audio import VERSION as MOUNT_VERSION, flag_level_jump


def write_reading(staged, paths, stop_event, limit_ms):
    fmt = None
    remaining = None
    offset = 0
    timings = []
    with wave.open(str(staged), 'wb') as dest:
        for path in paths:
            if stop_event.is_set():
                return
            from audio_publication import recover_audio
            recover_audio(path)
            with wave.open(str(path), 'rb') as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if fmt is None:
                    fmt = current
                    dest.setnchannels(fmt[0])
                    dest.setsampwidth(fmt[1])
                    dest.setframerate(fmt[2])
                    if limit_ms is not None:
                        remaining = (limit_ms * fmt[2] + 500) // 1000
                if current != fmt:
                    raise ValueError('Formati vocali incompatibili')
                frames = source.getnframes()
                if remaining is not None:
                    frames = min(frames, remaining)
                    remaining -= frames
                if not frames:
                    break
                metadata = Path(path).with_suffix('.indice.json')
                if not metadata.exists() and '-eq-' in Path(path).stem:
                    metadata = Path(path).with_name(Path(path).stem.rsplit('-eq-', 1)[0] + '.indice.json')
                info = {}
                try:
                    data = json.loads(metadata.read_text(encoding='utf-8'))
                    info = dict(data['segments'][0])
                except (OSError, ValueError, KeyError, IndexError):
                    pass
                timings.append(dict(info, clip=Path(path).name, seconds=round(offset / fmt[2], 3),
                                    frames=frames))
                if len(timings) > 1:
                    flag_level_jump(timings[-2], timings[-1])
                offset += frames
                while frames and not stop_event.is_set():
                    count = min(frames, fmt[2] * 10)
                    chunk = source.readframes(count)
                    if len(chunk) != count * fmt[0] * fmt[1]:
                        raise ValueError('Clip WAV incompleto')
                    dest.writeframesraw(chunk)
                    frames -= count
    if stop_event.is_set():
        return
    with wave.open(str(staged), 'rb') as check:
        if not offset or check.getnframes() != offset:
            raise ValueError('Lettura WAV vuota o incompleta')
    return timings, offset / fmt[2]

def write_junction(output, paths, stop_event):
    """Play the end and start of two clips in one stream."""
    with wave.open(str(output), 'wb') as dest:
        fmt = None
        for index, path in enumerate(paths):
            if stop_event.is_set():
                raise ReadingCancelled('Ascolto del raccordo interrotto')
            with wave.open(str(path), 'rb') as source:
                current = source.getparams()[:3]
                if fmt is None:
                    fmt = current
                    dest.setparams(source.getparams())
                if fmt != current:
                    raise ValueError('Formati vocali incompatibili')
                count = min(source.getnframes(), fmt[2] * 2)
                if index == 0:
                    source.setpos(source.getnframes() - count)
                dest.writeframes(source.readframes(count))
    return output

def save_reading(output, paths, stop_event, limit_ms=None):
    """Stream a reading to a validated temporary master before publishing."""
    from converti_audio import convert
    from audio_publication import publish_audio
    output = Path(output)
    from audio_publication import recover_audio
    recover_audio(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.wav', delete=False) as file:
        staged = Path(file.name)
    try:
        result = write_reading(staged, paths, stop_event, limit_ms)
        if result is None or stop_event.is_set():
            return
        timings, duration = result
        metadata = {'duration': duration, 'segments': timings, 'mounting_version': MOUNT_VERSION}
        if output.suffix.lower() == '.mp4':
            with tempfile.TemporaryDirectory(dir=output.parent) as directory:
                converted = Path(directory) / 'complete.mp4'
                convert(staged, converted, cancel_event=stop_event)
                return publish_audio(converted, output, metadata, stop_event)
        return publish_audio(staged, output, metadata, stop_event)
    finally:
        staged.unlink(missing_ok=True)

def play_audio(path, *, stop_event, events, limit_ms=None):
    """Play a local WAV, trimming only the final segment of a timed reading."""
    if stop_event.is_set():
        return
    path = Path(path).resolve()
    from audio_publication import recover_audio
    recover_audio(path)
    if limit_ms is not None:
        with wave.open(str(path), 'rb') as wav:
            needs_trim = wav.getnframes() * 1000 > limit_ms * wav.getframerate()
        if needs_trim:
            with tempfile.TemporaryDirectory() as directory:
                clip = save_reading(Path(directory) / 'clip.wav', [path], stop_event, limit_ms)
                if clip is not None:
                    _play_wav(clip, stop_event, events)
            return
    _play_wav(path, stop_event, events)

def _play_wav(path, stop_event, events):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

    if stop_event.is_set():
        return
    commands = queue.Queue()
    loop = QEventLoop()
    player = QMediaPlayer()
    output = QAudioOutput()
    player.setAudioOutput(output)
    timer = QTimer(interval=40)

    def update():
        if stop_event.is_set() or player.error() != QMediaPlayer.Error.NoError:
            loop.quit()
            return
        if player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            loop.quit()
            return
        duration = player.duration()
        if duration <= 0:
            return
        paused = player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        position = player.position()
        try:
            action, seconds = commands.get_nowait()
        except queue.Empty:
            pass
        else:
            if action == 'toggle':
                if paused:
                    player.play()
                else:
                    player.pause()
            else:
                position = max(0, min(duration, position + seconds * 1000))
                if position >= duration and not paused:
                    loop.quit()
                    return
                player.setPosition(position)
        events.put(dict(kind='transport', commands=commands,
                        paused=player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                        elapsed=position / 1000, duration=duration / 1000))

    timer.timeout.connect(update)
    try:
        player.setSource(QUrl.fromLocalFile(str(path)))
        player.play()
        timer.start()
        loop.exec()
        if not stop_event.is_set() and player.error() != QMediaPlayer.Error.NoError:
            raise OSError(f'Riproduzione WAV: {player.errorString()}')
    finally:
        timer.stop()
        player.stop()
        player.setSource(QUrl())
