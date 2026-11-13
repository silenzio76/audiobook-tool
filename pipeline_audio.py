"""Analysis, synthesis and reading worker without widget access."""
from ui_language import tr
import queue
import threading
import time
import wave


class ReadingCancelled(Exception):
    pass


class AudioPipeline:
    def __init__(self, indices, generate, play, events, buffer_size=2, startup_items=1,
                 preload_all=False, *, analyze=None, batch_size=None, generate_batch=None,
                 on_playback_complete=None):
        if buffer_size < 1 or not 1 <= startup_items <= buffer_size:
            raise ValueError('Il pre-caricamento deve rientrare nella capacità della coda')
        self.indices = tuple(indices)
        self.generate = generate
        self.generate_batch = generate_batch
        self.play = play
        self.on_playback_complete = on_playback_complete
        self.events = events
        self.ready = queue.Queue(maxsize=buffer_size)
        self.analyze = analyze
        self.batch_size = batch_size or max(1, len(self.indices))
        self.work = queue.Queue()  # Indices only, never audio.
        self.startup_items = startup_items
        self.preload_all = preload_all
        self.prepared = []  # Disk paths only, not audio samples in RAM.
        self.stop_event = threading.Event()
        self.playback_stop_event = threading.Event()
        self.available = {}
        self.replay_request = None
        self.replaying = False
        self.current_index = None
        self.producer_done = threading.Event()
        self._error = None
        self._lock = threading.Lock()
        self.threads = (
            threading.Thread(target=self._produce, name=tr('Vocalizzazione'), daemon=True),
            threading.Thread(target=self._consume, name=tr('Lettura'), daemon=True),
        )
        if self.analyze is not None:
            self.threads += (threading.Thread(target=self._analyze, name='Analisi testo', daemon=True),)

    @property
    def error(self):
        with self._lock:
            return self._error

    @property
    def finished(self):
        return all(thread.ident is not None and not thread.is_alive() for thread in self.threads)

    def start(self):
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.playback_stop_event.set()

    def request_replay(self, index):
        """Interrupt playback only; generation and the normal queue stay intact."""
        with self._lock:
            if self.play is None or self.stop_event.is_set() or self.finished or index not in self.available:
                return False
            self.replay_request = index
            self.playback_stop_event.set()
            return True

    def _play_with_replays(self, position, index, path, duration):
        self.current_index = index
        while not self.stop_event.is_set():
            with self._lock:
                if self.stop_event.is_set():
                    return False
                replay = self.replay_request
                self.replay_request = None
                self.playback_stop_event.clear()
                replay_path = self.available[replay] if replay is not None else None
            self.replaying = replay is not None
            if self.replaying:
                self._emit('replaying', index=replay, path=replay_path)
                self.play(replay_path)
                continue
            self._emit('playing', position=position, index=index, path=path,
                       duration=duration, started=time.monotonic())
            keep_reading = self.play(path)
            with self._lock:
                interrupted = self.playback_stop_event.is_set()
            if not interrupted:
                return keep_reading
        return False

    def _emit(self, kind, **fields):
        self.events.put(dict(kind=kind, **fields))

    def _fail(self, error):
        with self._lock:
            if self._error is None:
                self._error = str(error)
        self.stop_event.set()
        self.playback_stop_event.set()
        self._emit('error', message=str(error))

    def _analyze(self):
        try:
            for start in range(0, len(self.indices), self.batch_size):
                batch = self.indices[start:start + self.batch_size]
                estimates = {}
                for index in batch:
                    if self.stop_event.is_set():
                        return
                    estimates[index] = self.analyze(index)
                self._emit('analyzed', estimates=estimates)
                self.work.put(batch)
            self.work.put(None)
        except Exception as error:
            if not self.stop_event.is_set():
                self._fail(error)

    def _work_batches(self):
        if self.analyze is None:
            yield self.indices
            return
        while not self.stop_event.is_set():
            try:
                batch = self.work.get(timeout=.1)
            except queue.Empty:
                continue
            if batch is None:
                return
            yield batch

    @staticmethod
    def duration(path):
        try:
            with wave.open(str(path), 'rb') as wav:
                return wav.getnframes() / wav.getframerate()
        except (OSError, wave.Error, EOFError):
            return None

    def _generated_paths(self):
        for batch in self._work_batches():
            if self.generate_batch is None:
                for index in batch:
                    if self.stop_event.is_set():
                        return
                    started = time.monotonic()
                    self._emit('generating', index=index, started=started)
                    path = self.generate(index, self.stop_event)
                    yield index, path, time.monotonic() - started
            else:
                if self.stop_event.is_set():
                    return
                started = time.monotonic()
                self._emit('generating', index=batch[0], started=started, size=len(batch))
                paths = self.generate_batch(batch, self.stop_event)
                seconds = (time.monotonic() - started) / len(batch)
                for index, path in zip(batch, paths, strict=True):
                    yield index, path, seconds

    def _produce(self):
        try:
            for position, (index, path, seconds) in enumerate(self._generated_paths(), 1):
                if self.stop_event.is_set(): break
                with self._lock:
                    self.available[index] = path
                self._emit('generated', position=position, index=index, path=path,
                           synthesis_seconds=seconds, duration=self.duration(path))
                if self.play is None: continue
                if self.preload_all:
                    self.prepared.append((position, index, path))
                    continue
                while not self.stop_event.is_set():
                    try:
                        self.ready.put((position, index, path), timeout=.1)
                        break
                    except queue.Full:
                        continue
        except Exception as error:
            if not self.stop_event.is_set(): self._fail(error)
        finally:
            self.producer_done.set()
            self._emit('generation_finished')

    def _consume(self):
        if self.play is None:
            self._emit('reading_finished')
            return
        try:
            complete = None
            limit_reached = False
            buffering = True
            if self.preload_all:
                self._emit('buffering', message=tr('Preparazione completa prima dell’ascolto'))
                while not self.producer_done.wait(.1):
                    if self.stop_event.is_set(): return
                if self.stop_event.is_set(): return
                complete = iter(self.prepared)
            while not self.stop_event.is_set():
                if complete is None and buffering:
                    self._emit('buffering', message=tr('Pre-caricamento: attendo {} brani o la fine della generazione').format(self.startup_items))
                    while self.ready.qsize() < self.startup_items and not self.producer_done.is_set():
                        if self.stop_event.wait(.1): return
                    buffering = False
                try:
                    position, index, path = next(complete) if complete is not None else self.ready.get(timeout=.1)
                except StopIteration:
                    break
                except queue.Empty:
                    if self.producer_done.is_set(): break
                    buffering = True
                    continue
                if self.stop_event.is_set(): break
                duration = self.duration(path)
                if self.stop_event.is_set(): break
                keep_reading = self._play_with_replays(position, index, path, duration)
                if self.stop_event.is_set(): break
                self._emit('played', position=position, index=index, path=path)
                if keep_reading is False:
                    limit_reached = True
                    break
            if self.on_playback_complete is not None and not self.stop_event.is_set():
                output = self.on_playback_complete()
                if output is not None:
                    self._emit('saved', path=output)
            if limit_reached:
                self.stop()
        except Exception as error:
            if not self.stop_event.is_set(): self._fail(error)
        finally:
            self._emit('reading_finished')
