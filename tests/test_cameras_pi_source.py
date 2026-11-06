import queue
import unittest
from pathlib import Path

from app.cameras.source import input_args
from app.cameras.pi_source import Broadcast
from app.cameras.recorder import command
from app.cameras.event_recording import segment_command


class InputTests(unittest.TestCase):
    def test_rtsp_options_preserved_and_pi_has_read_timeout(self):
        self.assertEqual(input_args('rtsp://camera/live'),
                         ['-rtsp_transport', 'tcp', '-timeout', '15000000', '-i', 'rtsp://camera/live'])
        url = 'http://127.0.0.1:8084/stream.ts'
        for args in (command(url, Path('clip.partial'), 60), segment_command(url, Path('buffer'))):
            self.assertNotIn('-rtsp_transport', args)
            self.assertIn('-rw_timeout', args)
            self.assertEqual(args[args.index('-c:v') + 1], 'copy')
            self.assertEqual(args[args.index('-i') + 1], url)

    def test_unsupported_sources_fail_explicitly(self):
        for url in ('', '/etc/passwd', 'http://camera/stream.ts', 'http://127.0.0.1/private', 'file:///tmp/video'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                input_args(url)


class BroadcastTests(unittest.TestCase):
    def test_multiple_consumers_receive_same_video(self):
        stream = Broadcast()
        first, second = stream.subscribe(), stream.subscribe()
        stream.publish(b'transport packets')
        self.assertEqual(first.get_nowait(), b'transport packets')
        self.assertEqual(second.get_nowait(), b'transport packets')
        stream.remove(first)
        stream.publish(None)
        self.assertIsNone(second.get_nowait())
        self.assertTrue(first.empty())

    def test_slow_consumer_disconnected_without_blocking_other_readers(self):
        stream = Broadcast()
        slow, fast = stream.subscribe(), stream.subscribe()
        for _ in range(65):
            stream.publish(b'chunk')
            self.assertEqual(fast.get_nowait(), b'chunk')
        self.assertIsNone(slow.get_nowait())
        self.assertNotIn(slow, stream.clients)
        self.assertIn(fast, stream.clients)
        with self.assertRaises(queue.Empty):
            slow.get_nowait()

    def test_connection_limit(self):
        stream = Broadcast()
        for _ in range(8):
            self.assertIsNotNone(stream.subscribe())
        self.assertIsNone(stream.subscribe())


class CaptureLifecycleTests(unittest.TestCase):
    def test_idle_source_does_not_open_camera(self):
        from unittest.mock import patch
        stream = Broadcast()
        with patch.object(stream.stop, 'wait', side_effect=lambda _: stream.stop.set()), \
                patch('app.cameras.pi_source.subprocess.Popen') as spawn:
            stream.run()
        spawn.assert_not_called()

    def test_source_terminates_encoder_when_last_consumer_disconnects(self):
        from unittest.mock import Mock, patch
        stream = Broadcast()
        client = stream.subscribe()
        process = Mock()

        def read(_):
            stream.remove(client)
            return b'last transport packets'

        process.stdout.read1.side_effect = read
        with patch.object(stream.stop, 'wait', side_effect=lambda _: stream.stop.set()), \
                patch('app.cameras.pi_source.subprocess.Popen', return_value=process) as spawn:
            stream.run()
        spawn.assert_called_once()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()


class ModeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = patch('app.cameras.pi_source.SETTINGS', Path(directory.name) / 'persistent.json')
        settings.start()
        self.addCleanup(settings.stop)

    def test_runtime_override_controls_output_and_keyframe_interval(self):
        import os
        import tempfile
        from unittest.mock import patch
        from app.cameras.pi_source import command
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode.json'
            path.write_text('{"width":1920,"height":1080,"fps":30}')
            with patch.dict(os.environ, {'PI_CAMERA_MODE_FILE': str(path)}):
                args = command()
            self.assertEqual(args[args.index('--width') + 1], '1920')
            self.assertEqual(args[args.index('--height') + 1], '1080')
            self.assertEqual(args[args.index('--framerate') + 1], '30.0')
            self.assertIn('g=30', args[args.index('--libav-video-codec-opts') + 1])

    def test_invalid_mode_rejected(self):
        import os
        import tempfile
        from unittest.mock import patch
        from app.cameras.pi_source import mode
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode.json'
            path.write_text('{"width":9999,"fps":0}')
            with patch.dict(os.environ, {'PI_CAMERA_MODE_FILE': str(path)}), self.assertRaises(ValueError):
                mode()
