"""Qt transport regressions, also runnable in the optional editor runtime."""
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import wave
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6 import QtCore, QtWidgets
    from editor_audiolibro import Editor
except ModuleNotFoundError:
    Editor = None

import audiolibro as audio


@unittest.skipIf(Editor is None, 'Requires the optional PySide6 editor runtime')
class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.callback_errors = []
        self.hook = patch('sys.excepthook', side_effect=lambda *args: self.callback_errors.append(args))
        self.hook.start()
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        source = root / 'sample.md'
        source.write_text('La finestra era aperta.\n\nIl vento era freddo.\n', encoding='utf-8')
        self.window = Editor(source, audio, settings=QtCore.QSettings(
            str(root / 'settings.ini'), QtCore.QSettings.Format.IniFormat))
        self.window.poll_timer.stop()
        self.window.active_request = (0, False, True, True, '00:00', True, False, False, False)
        self.window.pipeline = SimpleNamespace(stop_event=threading.Event(), error=None)
        self.window.pipeline.stop = self.window.pipeline.stop_event.set
        self.commands = queue.Queue()
        self.window.transport = dict(paused=True, commands=self.commands)

    def tearDown(self):
        self.window.pipeline = None
        self.window.close()
        self.directory.cleanup()
        self.hook.stop()
        self.assertEqual(self.callback_errors, [])

    def test_public_brand_models_file_tab_and_three_stage_progress(self):
        self.window.pipeline=None
        self.assertIn("will-o'-the-wisp",self.window.windowTitle())
        self.assertEqual([b.accessibleName() for b in (self.window.gen_bar,self.window.voice_bar,self.window.read_bar)],
                         ['Pronuncia','Voce','Ascolto'])
        self.assertIn('File',self.window.ribbon_tabs.tabText(self.window.files_tab_index))
        self.assertFalse(hasattr(self.window,'document_language'))
        self.assertFalse(hasattr(self.window,'language'))
        self.assertEqual(self.window.document_model.count(),2)
        self.window.plan['segments'][0]['language']='it'
        audio.save(self.window.path,self.window.plan)
        self.window.pick(0)
        self.window.segment_model.setCurrentIndex(self.window.segment_model.findData('source'))
        self.window.assign()
        updated=audio.read_plan(self.window.path)
        self.assertEqual(updated['segments'][0]['language'],'it')
        self.assertEqual(updated['segments'][0]['voice_model'],'source')

    def test_pause_button_and_listen_both_resume_paused_player(self):
        self.window.update_transport()
        self.assertTrue(self.window.pause_button.isEnabled())
        self.assertEqual(self.window.pause_button.text(), 'Riprendi')
        self.window.pause_button.click()
        self.assertEqual(self.commands.get_nowait(), ('toggle', 0))
        self.window.segment_play.click()
        self.assertEqual(self.commands.get_nowait(), ('toggle', 0))

    def test_stop_then_resume_keeps_segment_and_continuous_mode(self):
        self.window.select_segment(1)
        self.window.stop()
        self.assertTrue(self.window.pipeline.stop_event.is_set())
        self.window.finish()
        self.window.update_transport()
        self.assertTrue(self.window.pause_button.isEnabled())
        self.assertEqual(self.window.pause_button.text(), 'Riprendi')
        with patch.object(self.window, 'start') as start:
            self.window.pause_button.click()
            start.assert_called_once_with(quick=True, continuous=True)
        self.assertEqual(self.window.listing.currentRow(), 1)

    def test_opening_another_document_clears_resume(self):
        self.window.stop()
        self.window.pipeline = None
        source = Path(self.directory.name) / 'other.md'
        source.write_text('Una porta chiusa.\n', encoding='utf-8')
        self.window.load_document(source)
        self.assertIsNone(self.window.resume_request)
        self.assertFalse(self.window.pause_button.isEnabled())

    def test_browsing_while_reading_requests_selected_replay(self):
        self.window.playing_index = 0
        self.window.pipeline.request_replay = lambda index: index == 1
        self.window.step_segment(1)
        self.assertEqual(self.window.listing.currentRow(), 1)
        self.assertFalse(self.window.follow_playback)
        self.window.update_transport()
        self.assertTrue(self.window.segment_play.isEnabled())
        self.window.segment_play.click()
        self.assertIn('Riascolto', self.window.status.text())

    def test_real_player_pause_resume_stop_and_reopen(self):
        path = Path(self.directory.name) / 'silence.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x00\x00' * 22050 * 3)
        for attempt in range(2):
            stop = threading.Event()
            events = queue.Queue()
            observed = []

            def control():
                deadline = time.monotonic() + 5
                stage = 0
                while time.monotonic() < deadline:
                    try:
                        event = events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event['kind'] != 'transport':
                        continue
                    if stage == 0:
                        event['commands'].put(('toggle', 0))
                        stage = 1
                    elif stage == 1 and event['paused']:
                        observed.append('paused')
                        event['commands'].put(('toggle', 0))
                        stage = 2
                    elif stage == 2 and not event['paused']:
                        observed.append('resumed')
                        stop.set()
                        return
                stop.set()

            worker = threading.Thread(target=control)
            worker.start()
            try:
                audio._play_wav(path, stop, events)
            finally:
                stop.set()
                worker.join(6)
            with self.subTest(attempt=attempt):
                self.assertEqual(observed, ['paused', 'resumed'])

    def test_replay_does_not_duplicate_saved_reading(self):
        root = Path(self.directory.name)
        clips = []
        for index in range(2):
            clip = root / f'clip{index}.wav'
            with wave.open(str(clip), 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\x10\x00' * 800)
            clips.append(clip)
        entered = threading.Event()
        calls = []

        def play(path, *, stop_event, events, **options):
            calls.append(path)
            if path == clips[1] and len(calls) == 2:
                entered.set()
                if not stop_event.wait(3):
                    raise RuntimeError('Replay did not interrupt playback')

        self.window.pipeline = None
        self.window.select_segment(0)
        self.window.preload.setChecked(True)
        output = root / 'reading.wav'
        with patch.object(self.window, 'choose_audio_file', return_value=output), \
                patch.object(audio, 'preview', side_effect=lambda path,index,**kw: clips[index]), \
                patch.object(audio, 'play_audio', side_effect=play):
            self.window.start(continuous=True)
            deadline = time.monotonic() + 5
            requested = False
            while self.window.busy and time.monotonic() < deadline:
                self.app.processEvents()
                self.window.poll()
                if entered.is_set() and not requested:
                    self.assertTrue(self.window.pipeline.request_replay(0))
                    requested = True
                time.sleep(.005)
            self.assertFalse(self.window.busy)
        self.assertTrue(requested)
        self.assertEqual(calls, [clips[0],clips[1],clips[0],clips[1]])
        with wave.open(str(output), 'rb') as stream:
            self.assertEqual(stream.getnframes(), 1600)


if __name__ == '__main__':
    unittest.main()
