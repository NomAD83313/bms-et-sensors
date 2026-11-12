import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.cameras import dashboard
from app.cameras.preview_quality import DecodeGuard, decoder_error_kind


class DecodeGuardTests(unittest.TestCase):
    def test_decoder_errors_are_classified_without_benign_warning_false_alarms(self):
        for text in ['[h264] error while decoding MB 1 2', 'corrupt decoded frame in stream 0',
                     '[h264] non-existing PPS 0 referenced', '[h264] concealing 25 DC errors']:
            self.assertEqual(decoder_error_kind(text), 'decode_error')
        self.assertEqual(decoder_error_kind('Connection timed out'), 'stream_error')
        self.assertIsNone(decoder_error_kind('deprecated pixel format used, make sure you did set range correctly'))

    def test_queued_and_recovery_frames_are_excluded_and_tickets_change(self):
        guard = DecodeGuard()
        original = guard.token
        self.assertIsNone(guard.feed(b'first', 10))
        self.assertEqual(guard.feed(b'second', 10.1), (b'first', 10, original))
        guard.note_error('decode_error', 10.15, 1000)
        self.assertIsNone(guard.feed(b'damaged', 10.2))
        self.assertIsNone(guard.feed(b'concealed', 11.1))
        self.assertIsNone(guard.feed(b'clean', 11.2))
        self.assertEqual(guard.feed(b'next', 11.3), (b'clean', 11.2, guard.token))
        self.assertNotEqual(guard.token, original)
        self.assertEqual(guard.errors, 1)
        self.assertEqual(guard.discarded, 3)

    def test_every_new_error_extends_recovery(self):
        guard = DecodeGuard()
        guard.note_error('decode_error', 10, 100)
        guard.note_error('decode_error', 10.8, 100.8)
        self.assertTrue(guard.recovering(11.5))
        self.assertFalse(guard.recovering(11.9))

    def test_timed_admission_does_not_wait_for_the_next_low_rate_frame(self):
        guard = DecodeGuard()
        self.assertIsNone(guard.feed(b'first', 10))
        self.assertIsNone(guard.release(10.01))
        self.assertEqual(guard.release(10.03), (b'first', 10, guard.token))
        self.assertIsNone(guard.release(10.04), 'A settled frame is admitted only once')

    def test_decoder_error_cancels_pending_timed_admission(self):
        guard = DecodeGuard()
        guard.feed(b'damaged', 10)
        guard.note_error('decode_error', 10.01, 100)
        self.assertIsNone(guard.release(10.03))
        self.assertIsNone(guard.release(11.5))

    def test_separate_dashboard_instances_never_accept_each_others_tickets(self):
        self.assertNotEqual(DecodeGuard().token, DecodeGuard().token)


class PreviewQualityTests(unittest.TestCase):
    def test_pi_preview_publishes_a_settled_frame_before_the_next_frame_arrives(self):
        preview = dashboard.LivePreview()
        release = threading.Event()
        process = Mock()
        process.stderr.readline.return_value = b''

        def frames(_):
            yield b'native-jpeg'
            release.wait(3)

        with patch.dict(os.environ, {'CAMERA_URL': 'http://127.0.0.1:8084/stream.ts'}), \
                patch.object(dashboard.source, 'capture_settings', return_value={'capture_mode': 'video', 'width': 1280, 'height': 720}), \
                patch.object(dashboard.source, 'capture_signature', return_value='video'), \
                patch.object(dashboard, 'jpeg_frames', side_effect=frames), \
                patch.object(dashboard.subprocess, 'Popen', return_value=process), \
                patch.object(dashboard, 'stop_process'), patch.object(preview, 'decoder_error'):
            try:
                self.assertEqual(preview.get(wait_seconds=1, full_resolution=True), b'native-jpeg')
                self.assertIsNone(preview.guard.pending)
            finally:
                release.set()
                if preview.worker is not None:
                    preview.worker.join(timeout=3)
                self.assertFalse(preview.worker.is_alive())

    def test_errors_invalidate_cached_frame_and_old_analysis_ticket(self):
        preview = dashboard.LivePreview()
        preview.worker = Mock()
        preview.worker.is_alive.return_value = True
        preview.frame = b'old'
        preview.frame_token = preview.guard.token
        preview.updated = 100
        original = preview.guard.token
        with patch.object(dashboard.time, 'monotonic', return_value=100):
            self.assertTrue(preview.validate(original))
            with self.assertLogs('camera-preview', level='WARNING'):
                preview.decoder_error('decode_error')
            self.assertEqual(preview.get(wait_seconds=0, with_token=True), (None, None))
            self.assertEqual(preview.quality()['state'], 'recovering')
        with patch.object(dashboard.time, 'monotonic', return_value=102):
            preview.frame, preview.updated, preview.frame_token = b'clean', 102, preview.guard.token
            self.assertFalse(preview.validate(original))
            self.assertTrue(preview.validate(preview.guard.token))

    def test_diagnostics_never_log_camera_credentials_and_are_rate_limited(self):
        preview = dashboard.LivePreview()
        sensitive = 'rtsp://private-user:private-password@10.0.0.1/stream'
        stream = io.BytesIO((sensitive + ': error while decoding MB 3 4\n' + sensitive + ': packet corrupt\n').encode())
        with self.assertLogs('camera-preview', level='WARNING') as captured:
            preview.read_errors(stream)
        self.assertEqual(preview.guard.errors, 2)
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn(sensitive, repr(captured.output) + repr(preview.quality()))
        self.assertNotIn('private-password', repr(captured.output))

    def test_reading_quality_does_not_start_a_decoder(self):
        preview = dashboard.LivePreview()
        with patch.object(dashboard.subprocess, 'Popen') as spawn:
            self.assertEqual(preview.quality()['state'], 'idle')
            self.assertFalse(preview.validate('old'))
            spawn.assert_not_called()


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg in the camera-dashboard image')
class PiPreviewRateTests(unittest.TestCase):
    def test_fast_jpeg_entropy_coding_preserves_decoded_pixels(self):
        try:
            from PIL import Image, ImageChops
        except ImportError:
            self.skipTest('Requires Pillow in the camera-dashboard image')
        images = []
        for options in [['-threads', '1'], ['-threads', '2', '-thread_type', 'frame', '-huffman', 'default']]:
            result = subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=1',
                '-frames:v', '1', '-c:v', 'mjpeg', '-q:v', '3', *options,
                '-f', 'image2pipe', 'pipe:1'], capture_output=True, check=True, timeout=10)
            images.append(Image.open(io.BytesIO(result.stdout)).convert('RGB'))
        self.assertIsNone(ImageChops.difference(*images).getbbox())

    def test_pi_frame_selection_caps_rate_without_duplicating_slow_input(self):
        for browser in (False,):
            args, sized = dashboard.decoder_command('http://127.0.0.1:8084/stream.ts', browser)
            frame_filter = args[args.index('-vf') + 1]
            self.assertFalse(sized)
            self.assertEqual(args[args.index('-fps_mode') + 1], 'vfr')
            for fps in (1, 5, 10, 15, 30):
                with self.subTest(fps=fps, browser=browser):
                    result = subprocess.run([
                        'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', f'testsrc2=size=64x48:rate={fps}:duration=2',
                        '-vf', frame_filter, '-fps_mode', 'vfr', '-threads', '1',
                        '-f', 'framemd5', 'pipe:1'], capture_output=True, check=True, timeout=10)
                    frames = [line for line in result.stdout.splitlines() if line and not line.startswith(b'#')]
                    self.assertEqual(len(frames), min(fps, 10) * 2)
                    header = b'#dimensions 0: 960x540' if browser else b'#dimensions 0: 64x48'
                    self.assertIn(header, result.stdout)


@unittest.skipUnless(shutil.which('ffmpeg'), 'Requires FFmpeg in the camera-dashboard image')
class CorruptedStreamTests(unittest.TestCase):
    def test_real_h264_error_blocks_analysis_and_recovers_on_clean_video(self):
        self.check_corrupt_stream('rtsp://camera/fixture')

    def test_pi_timed_admission_blocks_corruption_and_recovers_on_clean_video(self):
        self.check_corrupt_stream('http://127.0.0.1:8084/stream.ts')

    def check_corrupt_stream(self, url):
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / 'clean.h264'
            damaged = Path(directory) / 'damaged.h264'
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25', '-t', '4',
                            '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                            '-tune', 'zerolatency', '-g', '25', '-keyint_min', '25',
                            '-sc_threshold', '0', '-f', 'h264', str(clean)], check=True, timeout=15)
            content = bytearray(clean.read_bytes())
            markers = list(re.finditer(b'\x00\x00\x00\x01|\x00\x00\x01', content))
            slices = 0
            for index, marker in enumerate(markers[:-1]):
                if content[marker.end()] & 31 != 1:
                    continue
                slices += 1
                if 10 <= slices <= 12:
                    start = marker.end() + 8
                    end = min(markers[index + 1].start(), start + 128)
                    content[start:end] = b'\xff' * (end - start)
            damaged.write_bytes(content)
            preview = dashboard.LivePreview()
            original_spawn = subprocess.Popen

            def spawn(args, **kwargs):
                # Reuse the production decoder path against a paced local fixture.
                args = list(args)
                # Network-only no-buffer probing can consume a short raw fixture
                # before FFmpeg has exposed its first decoded frame.
                for option in ['-fflags', '-flags', '-err_detect', '-analyzeduration',
                               '-probesize', '-max_delay', '-reorder_queue_size',
                               '-rtsp_transport', '-timeout', '-rw_timeout']:
                    if option in args:
                        index = args.index(option)
                        del args[index:index + 2]
                args[args.index('-i') + 1] = str(damaged)
                args.insert(args.index('-i'), '-re')
                args[args.index('-i'):args.index('-i')] = ['-fflags', '+discardcorrupt',
                                                          '-err_detect', 'explode']
                return original_spawn(args, **kwargs)

            preview.last_request = time.monotonic()
            preview.worker = threading.Thread(target=preview.run, daemon=True)
            saw_clean = saw_error = recovered = False
            with patch.dict(os.environ, {'CAMERA_URL': url}), patch.object(dashboard.subprocess, 'Popen', side_effect=spawn), self.assertLogs('camera-preview', level='WARNING'):
                preview.worker.start()
                deadline = time.monotonic() + 10
                while preview.worker.is_alive() and time.monotonic() < deadline:
                    with preview.condition:
                        quality = preview.quality()
                        if quality['state'] == 'recovering':
                            self.assertIsNone(preview.frame)
                    if quality['last_error'] == 'decode_error':
                        saw_error = True
                    if quality['state'] == 'ok':
                        saw_clean = True
                        recovered = recovered or saw_error
                    time.sleep(.01)
                preview.worker.join(timeout=3)
            self.assertFalse(preview.worker.is_alive())
            self.assertTrue(saw_clean)
            self.assertTrue(saw_error)
            self.assertTrue(recovered)
            self.assertGreater(preview.guard.discarded, 0)
