"""Replays interrupt playback without losing synthesis or normal reading order."""
import queue
import threading
import unittest
from pipeline_audio import AudioPipeline


class NavigationTests(unittest.TestCase):
    def test_replay_then_resume_keeps_generation_and_normal_order(self):
        entered = threading.Event()
        seen = []
        pipeline = None

        def play(path):
            seen.append((path, pipeline.replaying))
            if path == 'clip1' and len(seen) == 2:
                entered.set()
                self.assertTrue(pipeline.playback_stop_event.wait(3))

        events = queue.Queue()
        pipeline = AudioPipeline(range(3), lambda index, cancel: f'clip{index}', play, events,
                                 preload_all=True)
        pipeline.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertTrue(pipeline.request_replay(0))
            for thread in pipeline.threads:
                thread.join(3)
            self.assertTrue(pipeline.finished)
            self.assertFalse(pipeline.stop_event.is_set())
            self.assertEqual(seen, [('clip0',False),('clip1',False),('clip0',True),('clip1',False),('clip2',False)])
            messages = []
            while not events.empty():
                messages.append(events.get_nowait())
            self.assertEqual([m['index'] for m in messages if m['kind']=='played'], [0,1,2])
        finally:
            pipeline.stop()
            for thread in pipeline.threads:
                thread.join(3)

    def test_unready_replay_does_not_cancel_synthesis(self):
        pipeline = AudioPipeline([0], lambda i,c:'clip0', lambda p:None, queue.Queue())
        self.assertFalse(pipeline.request_replay(99))
        self.assertFalse(pipeline.stop_event.is_set())
        pipeline.stop()
        self.assertTrue(pipeline.playback_stop_event.is_set())


if __name__ == '__main__':
    unittest.main()
