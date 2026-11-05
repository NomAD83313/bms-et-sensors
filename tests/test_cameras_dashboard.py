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

    def test_combined_routes_start_through_worker_and_stop_even_if_offline(self):
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
                    routes = [('recording/start', 'monitor'), ('recording/stop', 'pause'),
                              ('anomaly/monitor', 'monitor'), ('anomaly/pause', 'pause'),
                              ('anomaly/learn', 'learn'), ('anomaly/configure', 'configure')]
                    for route, expected in routes:
                        dashboard.control.set_enabled(expected != 'monitor')
                        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/{route}', method='POST', headers={'X-Camera-Control': '1'}, data=b'{"multiplier":5.5}')
                        with urllib.request.urlopen(request, timeout=3) as response:
                            self.assertEqual(response.status, 202)
                            result = json.load(response)
                            request_id = result.get('request_id', result.get('id'))
                        command = dashboard.anomaly_control.read_json(anomaly / 'command.json')
                        self.assertEqual((command['action'], command['multiplier'], command['id']), (expected, 5.5, request_id))
                        # Start waits for the worker; stop also works with no worker.
                        self.assertEqual(dashboard.control.enabled(), expected == 'configure')
                    previous_command = (anomaly / 'command.json').read_bytes()
                    with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'continuous'}):
                        for action, enabled in [('stop', False), ('start', True)]:
                            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/recording/{action}', method='POST', headers={'X-Camera-Control': '1'})
                            with urllib.request.urlopen(request, timeout=3) as response:
                                self.assertEqual(response.status, 200)
                            self.assertEqual(dashboard.control.enabled(), enabled)
                        self.assertEqual((anomaly / 'command.json').read_bytes(), previous_command)
                    request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/recording/start', method='POST')
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, 403)
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
    def test_analysis_cannot_receive_a_stale_cached_frame(self):
        preview = dashboard.LivePreview()
        preview.worker = Mock()
        preview.worker.is_alive.return_value = True
        preview.frame = b'jpeg'
        preview.updated = dashboard.time.monotonic() - .4
        self.assertIsNone(preview.get(max_age=.2, wait_seconds=0))
        self.assertEqual(preview.get(max_age=5, wait_seconds=0), b'jpeg')
