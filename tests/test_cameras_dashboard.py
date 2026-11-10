import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch, Mock
from app.cameras import dashboard


class ClipAccessTests(unittest.TestCase):
    def test_only_completed_clips_inside_recordings_are_accessible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'clip.mkv').write_bytes(b'video')
            (root / 'active.partial').write_bytes(b'partial')
            (root / 'linked.mkv').symlink_to('/etc/passwd')
            with patch.object(dashboard, 'RECORDINGS', root):
                self.assertEqual(dashboard.clip_path('clip.mkv'), root / 'clip.mkv')
                for name in ('../.env', '/etc/passwd', 'active.partial', 'linked.mkv', 'missing.mkv', '../clip.mkv'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        dashboard.clip_path(name)


class StatusTests(unittest.TestCase):
    def test_loading_analysis_status_does_not_require_model_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard.anomaly_control.write_json(root / 'status.json', dict(
                state='loading', message='Loading visual model', updated=dashboard.time.time()))
            with patch.object(dashboard, 'RECORDINGS', root), \
                    patch.object(dashboard.anomaly_control, 'DATA', root), \
                    patch.object(dashboard.LIVE, 'geometry', return_value=(2304, 1296)):
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    with urllib.request.urlopen(
                            f'http://127.0.0.1:{server.server_port}/api/anomaly', timeout=3) as response:
                        result = json.load(response)
                    self.assertEqual(result['state'], 'loading')
                    self.assertEqual((result['effective_input_width'], result['effective_input_height']),
                                     (768, 432))
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_system_health_distinguishes_paused_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dashboard, 'RECORDINGS', root), patch.object(dashboard.anomaly_control, 'DATA', root / 'anomaly'), patch.object(dashboard.control, 'CONTROL', root / 'control'):
                dashboard.control.set_enabled(False)
                dashboard.control.heartbeat('paused')
                dashboard.anomaly_control.write_json(root / 'anomaly/status.json', dict(
                    state='paused', updated=dashboard.time.time(), baseline_ready=True, monitoring_enabled=False))
                state = dashboard.status()
                self.assertEqual((state['service'], state['analysis_state']), ('paused', 'paused'))
                self.assertTrue(state['baseline_ready'])
                self.assertFalse(state['monitoring_enabled'])
                dashboard.anomaly_control.write_json(root / 'anomaly/status.json', dict(
                    state='normal', updated=dashboard.time.time() - 30, monitoring_enabled=True))
                self.assertEqual(dashboard.status()['analysis_state'], 'offline')

    def test_status_uses_completed_clips_without_systemd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dashboard, 'RECORDINGS', root), patch.object(dashboard.subprocess, 'run') as run, patch.object(dashboard.control, 'enabled', return_value=True), patch.object(dashboard.control, 'recording_state', return_value='recording'):
                self.assertFalse(dashboard.status()['healthy'])
                (root / 'clip.mkv').write_bytes(b'video')
                self.assertTrue(dashboard.status()['healthy'])
                run.assert_not_called()


class RecordingControlTests(unittest.TestCase):
    def test_learning_route_accepts_only_supported_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dashboard.control, 'CONTROL', root / 'control'), \
                    patch.object(dashboard.anomaly_control, 'DATA', root / 'anomaly'):
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/api/anomaly/learn'
                    def request(seconds):
                        return urllib.request.urlopen(urllib.request.Request(url,
                            data=json.dumps(dict(multiplier=1.3, learning_seconds=seconds)).encode(),
                            headers={'X-Camera-Control': '1', 'Content-Type': 'application/json'}), timeout=3)
                    with request(240) as response:
                        command = json.load(response)
                    self.assertEqual(command['learning_seconds'], 240)
                    saved = (root / 'anomaly/command.json').read_bytes()
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request(90)
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual((root / 'anomaly/command.json').read_bytes(), saved)
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_model_route_validates_selection_and_stops_event_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dashboard, 'RECORDINGS', root), \
                    patch.object(dashboard.control, 'CONTROL', root / 'control'), \
                    patch.object(dashboard.anomaly_control, 'DATA', root / 'anomaly'), \
                    patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}):
                dashboard.control.set_enabled(True)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/api/anomaly/model'
                    def request(key):
                        return urllib.request.urlopen(urllib.request.Request(url,
                            data=json.dumps(dict(model_key=key)).encode(),
                            headers={'X-Camera-Control': '1', 'Content-Type': 'application/json'}), timeout=3)
                    with request('mobilenetv3') as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)['model_key'], 'mobilenetv3')
                    self.assertFalse(dashboard.control.enabled())
                    saved = (root / 'anomaly/command.json').read_bytes()
                    with self.assertRaises(urllib.error.HTTPError) as error: request('unknown')
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual((root / 'anomaly/command.json').read_bytes(), saved)
                    with self.assertRaises(urllib.error.HTTPError) as error: request('resnet-int8')
                    self.assertEqual(error.exception.code, 400)
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_capture_get_and_post_pause_detection(self):
        from app.cameras.capture_settings import validate
        settings=validate(dict(capture_mode='snapshots',shutter_us=10000))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(dashboard.source,'capture_url',return_value='http://127.0.0.1:8084'), patch.object(dashboard.source,'capture_settings',return_value=validate({})), patch.object(dashboard.source,'configure_capture',return_value=settings), patch.object(dashboard.control,'CONTROL',root/'control'), patch.object(dashboard.anomaly_control,'DATA',root/'anomaly'), patch.dict(os.environ,{'CAMERA_RECORDING_MODE':'anomaly'}):
                dashboard.control.set_enabled(True)
                server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.Handler)
                worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                try:
                    url=f'http://127.0.0.1:{server.server_port}/api/capture'
                    with urllib.request.urlopen(url,timeout=3) as response:
                        self.assertTrue(json.load(response)['available'])
                    request=urllib.request.Request(url,method='POST',headers={'X-Camera-Control':'1'},data=b'{"capture_mode":"snapshots","snapshot_interval_ms":200,"shutter_us":10000}')
                    with urllib.request.urlopen(request,timeout=3) as response:
                        self.assertEqual(json.load(response)['settings'],settings)
                    self.assertFalse(dashboard.control.enabled())
                    self.assertEqual(dashboard.anomaly_control.read_json(root/'anomaly/command.json')['action'],'pause')
                    video = dict(capture_mode='video', width=1920, height=1080, fps=15, shutter_us=0)
                    dashboard.source.configure_capture.return_value = validate(video)
                    request=urllib.request.Request(url,method='POST',headers={'X-Camera-Control':'1'},data=json.dumps(video).encode())
                    with urllib.request.urlopen(request,timeout=3) as response:
                        self.assertEqual(response.status,200)
                    dashboard.source.configure_capture.assert_called_with(video)
                    self.assertEqual(dashboard.anomaly_control.read_json(root/'anomaly/command.json')['action'], 'invalidate')
                    invalid = dict(video, width=3280, height=2464, fps=30)
                    request=urllib.request.Request(url,method='POST',headers={'X-Camera-Control':'1'},data=json.dumps(invalid).encode())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request,timeout=3)
                    self.assertEqual(error.exception.code,400)

                finally:
                    server.shutdown();server.server_close();worker.join(timeout=3)

    def test_confirmation_change_does_not_pause_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), patch.object(dashboard.control, 'CONTROL', root / 'control'), patch.object(dashboard.anomaly_control, 'DATA', root / 'anomaly'):
                dashboard.control.set_enabled(True)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/api/anomaly/confirmation'
                    request = urllib.request.Request(url, method='POST', headers={'X-Camera-Control': '1'}, data=b'{"confirmation_frames":1}')
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)['confirmation_frames'], 1)
                    self.assertTrue(dashboard.control.enabled())
                    request = urllib.request.Request(url, method='POST', headers={'X-Camera-Control': '1'}, data=b'{"confirmation_frames":0}')
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, 400)
                    self.assertTrue(dashboard.control.enabled())
                finally:
                    server.shutdown(); server.server_close(); worker.join(timeout=3)

    def test_input_resize_is_validated_and_pauses_event_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), patch.object(dashboard.control, 'CONTROL', root / 'control'), patch.object(dashboard.anomaly_control, 'DATA', root / 'anomaly'):
                dashboard.control.set_enabled(True)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    url = f'http://127.0.0.1:{server.server_port}/api/anomaly/resize'
                    for size in ('768x432', '1024x576', '1280x720', '1536x864', '1920x1080'):
                        request = urllib.request.Request(url, method='POST', headers={'X-Camera-Control': '1'}, data=json.dumps({'input_size':size}).encode())
                        with urllib.request.urlopen(request, timeout=3) as response:
                            self.assertEqual(response.status, 202)
                            command = json.load(response)
                        self.assertEqual(command['input_size'], size)
                    self.assertFalse(dashboard.control.enabled())
                    request = urllib.request.Request(url, method='POST', headers={'X-Camera-Control': '1'}, data=b'{"input_size":999}')
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual(dashboard.anomaly_control.read_json(root / 'anomaly/command.json'), command)
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)

    def test_detection_owns_recording_only_in_anomaly_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard.control, 'CONTROL', Path(directory)):
            dashboard.control.follow_detection(False, 'anomaly')
            self.assertFalse(dashboard.control.enabled())
            dashboard.control.follow_detection(True, 'anomaly')
            self.assertTrue(dashboard.control.enabled())
            with patch.object(dashboard.control, 'set_enabled') as write:
                dashboard.control.follow_detection(True, 'anomaly')
                write.assert_not_called()
            dashboard.control.follow_detection(False, 'continuous')
            self.assertTrue(dashboard.control.enabled())
            dashboard.control.set_enabled(False)
            dashboard.control.follow_detection(True, 'continuous')
            self.assertFalse(dashboard.control.enabled())

    def test_anomaly_routes_and_removed_recording_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anomaly = root / 'anomaly'
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), patch.object(dashboard, 'RECORDINGS', root), patch.object(dashboard.control, 'CONTROL', root / 'control'), patch.object(dashboard.anomaly_control, 'DATA', anomaly):
                dashboard.anomaly_control.write_json(anomaly / 'session.json', dict(multiplier=5.5, monitoring=False))
                dashboard.control.set_enabled(False)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    routes = [('anomaly/monitor', 'monitor'), ('anomaly/pause', 'pause'),
                              ('anomaly/learn', 'learn'), ('anomaly/configure', 'configure')]
                    for route, expected in routes:
                        dashboard.control.set_enabled(expected != 'monitor')
                        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/{route}', method='POST', headers={'X-Camera-Control': '1'}, data=b'{"multiplier":5.5}')
                        with urllib.request.urlopen(request, timeout=3) as response:
                            self.assertEqual(response.status, 202)
                            result = json.load(response)
                        command = dashboard.anomaly_control.read_json(anomaly / 'command.json')
                        self.assertEqual((command['action'], command['multiplier'], command['id']),
                                         (expected, 5.5, result['id']))
                        # Start waits for the worker; stop also works with no worker.
                        self.assertEqual(dashboard.control.enabled(), expected == 'configure')
                    for mode in ('anomaly', 'continuous'):
                        os.environ['CAMERA_RECORDING_MODE'] = mode
                        for action in ('start', 'stop'):
                            request = urllib.request.Request(
                                f'http://127.0.0.1:{server.server_port}/api/recording/{action}',
                                method='POST', headers={'X-Camera-Control': '1'})
                            with self.assertRaises(urllib.error.HTTPError) as error:
                                urllib.request.urlopen(request, timeout=3)
                            self.assertEqual(error.exception.code, 404)
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join(timeout=3)

    def test_pause_persists_and_start_clears_pause(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard.control, 'CONTROL', Path(directory)):
            self.assertTrue(dashboard.control.enabled())
            dashboard.control.set_enabled(False)
            self.assertFalse(dashboard.control.enabled())
            dashboard.control.heartbeat('paused')
            self.assertEqual(dashboard.control.recording_state(), 'paused')
            dashboard.control.set_enabled(True)
            self.assertTrue(dashboard.control.enabled())

    def test_stale_heartbeat_is_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(dashboard.control, 'CONTROL', Path(directory)):
            self.assertEqual(dashboard.control.recording_state(), 'offline')
            dashboard.control.heartbeat('recording')
            with patch.object(dashboard.control.time, 'time', return_value=dashboard.time.time()+20):
                self.assertEqual(dashboard.control.recording_state(), 'offline')


class PreviewFreshnessTests(unittest.TestCase):
    def test_analysis_route_returns_full_source_with_generation_token(self):
        preview = dashboard.LivePreview()
        preview.worker = Mock()
        preview.worker.is_alive.return_value = True
        preview.frame, preview.original_frame = b'preview', b'full-size-jpeg'
        preview.updated, preview.frame_token = dashboard.time.monotonic(), preview.guard.token
        with patch.object(dashboard, 'LIVE', preview):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}/api/live.jpg'
                with urllib.request.urlopen(base+'?analysis=1', timeout=3) as response:
                    self.assertEqual(response.read(), b'full-size-jpeg')
                    self.assertEqual(response.headers['X-Camera-Frame-Token'], preview.guard.token)
                    self.assertEqual(response.headers['X-Camera-Analysis-Source'], dashboard.ANALYSIS_SOURCE)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_snapshot_preview_is_resized_only_for_browser_and_analysis_requires_original(self):
        preview = dashboard.LivePreview()
        preview.worker = Mock()
        preview.worker.is_alive.return_value = True
        preview.frame = preview.original_frame = b'full-native-snapshot'
        preview.updated, preview.frame_token = dashboard.time.monotonic(), preview.guard.token
        with patch.object(dashboard, 'browser_preview', return_value=b'960x540') as resize:
            self.assertEqual(preview.get(), b'960x540')
            self.assertEqual(preview.get(), b'960x540')
            self.assertEqual(preview.get(full_resolution=True), b'full-native-snapshot')
            resize.assert_called_once_with(b'full-native-snapshot')
        preview.original_frame = None
        self.assertIsNone(preview.get(full_resolution=True))

    def test_jpeg_pair_parser_rejects_partial_or_unaligned_output(self):
        import io
        frames = [b'\xff\xd8one\xff\xd9', b'\xff\xd8two\xff\xd9']
        self.assertEqual(list(dashboard.jpeg_frames(io.BytesIO(b''.join(frames)))), frames)
        for data in [b'\xff\xd8partial', b'junk' + frames[0]]:
            with self.assertRaises(OSError):
                list(dashboard.jpeg_frames(io.BytesIO(data)))

    def test_analysis_cannot_receive_a_stale_cached_frame(self):
        preview = dashboard.LivePreview()
        preview.worker = Mock()
        preview.worker.is_alive.return_value = True
        preview.frame = b'jpeg'
        preview.updated = dashboard.time.monotonic() - .4
        self.assertIsNone(preview.get(max_age=.2, wait_seconds=0))
        self.assertEqual(preview.get(max_age=5, wait_seconds=0), b'jpeg')
