import queue
import unittest
from pathlib import Path

from app.cameras.source import browser_stream_url, input_args, preview_input_args
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

    def test_rtsp_preview_uses_bounded_low_latency_input(self):
        url = 'rtsp://camera/live'
        args = preview_input_args(url)
        self.assertEqual(args[args.index('-i') + 1], url)
        self.assertEqual(args[args.index('-fflags') + 1], '+discardcorrupt+nobuffer')
        self.assertEqual(args[args.index('-flags') + 1], 'low_delay')
        self.assertEqual(args[args.index('-analyzeduration') + 1], '0')
        self.assertEqual(args[args.index('-max_delay') + 1], '0')
        self.assertEqual(args[args.index('-reorder_queue_size') + 1], '0')

    def test_browser_preview_can_use_non_strict_udp_without_changing_recording(self):
        args = preview_input_args('rtsp://camera/ch1', transport='udp', strict=False)
        self.assertEqual(args[args.index('-rtsp_transport') + 1], 'udp')
        self.assertNotIn('-err_detect', args)
        self.assertEqual(input_args('rtsp://camera/ch0')[1], 'tcp')

    def test_pi_preview_keeps_buffered_loopback_input(self):
        url = 'http://127.0.0.1:8084/stream.ts'
        args = preview_input_args(url)
        self.assertEqual(args[args.index('-fflags') + 1], '+discardcorrupt')
        self.assertNotIn('-max_delay', args)
        # A short probe keeps first-frame latency below the preview watchdog timeout.
        self.assertEqual(args[args.index('-analyzeduration') + 1], '0')
        self.assertEqual(args[args.index('-probesize') + 1], '32768')
        self.assertEqual(args[args.index('-i') + 1], url)

    def test_browser_substream_preserves_rtsp_authority(self):
        from unittest.mock import patch
        url = 'rtsp://user:password@camera:554/ch0'
        with patch.dict('os.environ', {'CAMERA_PREVIEW_PATH': '/ch1'}):
            self.assertEqual(browser_stream_url(url), 'rtsp://user:password@camera:554/ch1')
            self.assertEqual(browser_stream_url('http://127.0.0.1:8084/stream.ts'),
                             'http://127.0.0.1:8084/stream.ts')

    def test_invalid_browser_substream_path_is_rejected(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'CAMERA_PREVIEW_PATH': 'ch1'}), self.assertRaises(ValueError):
            browser_stream_url('rtsp://camera/ch0')

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
                patch('app.cameras.pi_source.mode', return_value={
                    'capture_mode': 'video', 'width': 3280, 'height': 2464,
                    'fps': 10, 'bitrate': 2000000, 'shutter_us': 0}), \
                patch('app.cameras.pi_source.subprocess.Popen', return_value=process) as spawn:
            stream.run()
        spawn.assert_called_once()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        process.stdout.close.assert_called_once()

    def test_padded_source_stops_both_children_after_disconnect(self):
        from unittest.mock import Mock, patch
        stream = Broadcast()
        client = stream.subscribe()
        capture, encoder = Mock(), Mock()

        def read(_):
            stream.remove(client)
            return b'last transport packets'

        encoder.stdout.read1.side_effect = read
        with patch.object(stream.stop, 'wait', side_effect=lambda _: stream.stop.set()), \
                patch('app.cameras.pi_source.mode', return_value={
                    'capture_mode': 'video', 'width': 1280, 'height': 720,
                    'fps': 10, 'bitrate': 2000000, 'shutter_us': 0}), \
                patch('app.cameras.pi_source.subprocess.Popen', side_effect=[capture, encoder]):
            stream.run()
        for child in (capture, encoder):
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=3)
        self.assertEqual(encoder.stdout.read1.call_count, 1)


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
            path.write_text('{"width":1920,"height":1080,"fps":15}')
            with patch.dict(os.environ, {'PI_CAMERA_MODE_FILE': str(path)}):
                args = command()
            self.assertEqual(args[args.index('--width') + 1], '1438')
            self.assertEqual(args[args.index('--height') + 1], '1080')
            self.assertEqual(args[args.index('--framerate') + 1], '15.0')
            self.assertEqual(args[args.index('--libav-video-codec') + 1], 'rawvideo')
            self.assertEqual(args[args.index('--mode') + 1], '3280:2464:10:P')
            from app.cameras.pi_source import padding_command
            encoded = padding_command(dict(width=1920, height=1080, fps=15,
                                            capture_mode='video', bitrate=2000000))
            self.assertEqual(encoded[encoded.index('-g') + 1], '15')

    def test_all_output_sizes_keep_one_sensor_mode_and_fit_without_cropping(self):
        from app.cameras.capture_settings import validate
        from app.cameras.pi_source import command, image_size, padding_command
        for width, height in ((640, 480), (1280, 720), (1920, 1080),
                              (1640, 1232), (3280, 2464)):
            settings = validate(dict(width=width, height=height))
            inner_width, inner_height = image_size(settings)
            with self.subTest(size=(width, height)):
                self.assertLessEqual(inner_width, width)
                self.assertLessEqual(inner_height, height)
                self.assertEqual(inner_width % 2, 0)
                self.assertEqual(inner_height % 2, 0)
                self.assertLess(abs(inner_width - inner_height * 3280 / 2464), 2)
                args = command(settings)
                self.assertEqual(args[args.index('--mode') + 1], '3280:2464:10:P')
                self.assertEqual(args[args.index('--roi') + 1], '0,0,1,1')
        self.assertIsNone(padding_command(validate(dict(width=3280, height=2464))))

    def test_padding_encoder_keeps_edges_and_returns_requested_snapshot_size(self):
        import shutil
        import subprocess
        from app.cameras.capture_settings import validate
        from app.cameras.pi_source import image_size, padding_command
        if not shutil.which('ffmpeg'):
            self.skipTest('Requires FFmpeg')
        settings = validate(dict(capture_mode='snapshots', width=1280, height=720))
        width, height = image_size(settings)
        # A bright, complete image must survive between the black side bars.
        raw = bytes([200]) * (width * height) + bytes([128]) * (width * height // 2)
        framed = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo',
                                 '-pixel_format', 'yuv420p', '-video_size', f'{width}x{height}',
                                 '-i', 'pipe:0', '-c:v', 'rawvideo', '-f', 'nut', 'pipe:1'],
                                input=raw, capture_output=True, timeout=15, check=True).stdout
        output = subprocess.run(padding_command(settings), input=framed, capture_output=True,
                                timeout=15, check=True).stdout
        rgb = subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                             input=output, capture_output=True, timeout=15, check=True).stdout
        self.assertEqual(len(rgb), 1280 * 720 * 3)
        def pixel(x, y):
            offset = (1280 * y + x) * 3
            return rgb[offset:offset+3]
        self.assertLess(max(pixel(0, 360)), 10)
        self.assertLess(max(pixel(1279, 360)), 10)
        self.assertGreater(min(pixel(164, 0)), 180)
        self.assertGreater(min(pixel(1114, 719)), 180)

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
