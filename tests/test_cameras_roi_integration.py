"""ROI control, worker lifecycle and original-event preservation checks."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from app.cameras import anomaly_control, control, dashboard
from app.cameras.roi import ROIGeometryChanged
from tests.isolated_import import load_isolated_module
from tests.test_cameras_worker_quality import StopLoop


ROI = dict(x=.25, y=.25, width=.5, height=.5)


class RoiApiTests(unittest.TestCase):
    def test_status_exposes_actual_grid_and_allows_recovery_from_invalid_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(dashboard, 'RECORDINGS', root), \
                    patch.object(control, 'CONTROL', root / 'control'), \
                    patch.object(anomaly_control, 'DATA', root / 'anomaly'), \
                    patch.object(dashboard.LIVE, 'geometry', return_value=(400, 300)) as geometry:
                state = dict(updated=time.time(), state='untrained', roi=ROI,
                             input_size='1280x720', model_key='mobilenetv3',
                             heatmap_width=80, heatmap_height=45)
                anomaly_control.write_json(root / 'anomaly/status.json', state)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f'http://127.0.0.1:{server.server_port}/api/anomaly'
                try:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        data = json.load(response)
                    self.assertEqual((data['effective_input_width'], data['effective_input_height']), (960, 720))
                    self.assertEqual((data['heatmap_width'], data['heatmap_height']), (60, 45))
                    geometry.return_value = (400, 4000)
                    state['roi'] = dict(x=0, y=0, width=.05, height=1)
                    anomaly_control.write_json(root / 'anomaly/status.json', state)
                    with urllib.request.urlopen(url, timeout=3) as response:
                        data = json.load(response)
                    self.assertEqual(data['state'], 'untrained')
                    self.assertFalse(data['roi_geometry_valid'])
                    self.assertIsNone(data['effective_input_width'])
                    self.assertIsNone(data['heatmap_width'])
                    request = urllib.request.Request(url + '/roi', data=b'{"roi":null}',
                                                     headers={'X-Camera-Control': '1'})
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.assertIsNone(json.load(response)['roi'])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)

    def test_validated_roi_commands_pause_only_event_recording(self):
        for mode in ('anomaly', 'continuous'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(dashboard, 'RECORDINGS', root), \
                        patch.object(control, 'CONTROL', root / 'control'), \
                        patch.object(anomaly_control, 'DATA', root / 'anomaly'), \
                        patch.object(dashboard.LIVE, 'geometry', return_value=(1920, 1080)), \
                        patch.dict(os.environ, {'CAMERA_RECORDING_MODE': mode}):
                    anomaly_control.write_json(root / 'anomaly/session.json', dict(input_size='1536x864'))
                    control.set_enabled(True)
                    server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    url = f'http://127.0.0.1:{server.server_port}/api/anomaly/roi'
                    def post(body, headers=True):
                        request = urllib.request.Request(url, data=json.dumps(body).encode(),
                            headers={'X-Camera-Control': '1'} if headers else {})
                        return urllib.request.urlopen(request, timeout=3)
                    try:
                        with post(dict(roi=ROI, multiplier=1.2)) as response:
                            self.assertEqual(response.status, 202)
                            self.assertEqual(json.load(response)['roi'], ROI)
                        self.assertEqual(control.enabled(), mode == 'continuous')
                        saved = (root / 'anomaly/command.json').read_bytes()
                        control.set_enabled(True)
                        for body in ({}, {'roi': True}, {'roi': dict(ROI, x=.9)},
                                     {'roi': dict(ROI, width=float('nan'))},
                                     {'roi': dict(x=0, y=0, width=.01, height=1)}):
                            with self.assertRaises(urllib.error.HTTPError) as error:
                                post(body)
                            self.assertEqual(error.exception.code, 400)
                            self.assertEqual((root / 'anomaly/command.json').read_bytes(), saved)
                            self.assertTrue(control.enabled())
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            post(dict(roi=ROI), headers=False)
                        self.assertEqual(error.exception.code, 403)
                        with patch.object(dashboard.LIVE, 'geometry', return_value=None):
                            with self.assertRaises(urllib.error.HTTPError): post(dict(roi=ROI))
                            with post(dict(roi=None)) as response:
                                self.assertIsNone(json.load(response)['roi'])
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=3)

    def test_source_geometry_is_available_before_learning_without_pixel_decode(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Requires the camera dashboard Pillow dependency')
        stream = io.BytesIO()
        Image.new('RGB', (400, 300)).save(stream, format='JPEG')
        live = dashboard.LivePreview()
        self.assertIsNone(live.geometry())
        live.original_frame = stream.getvalue()
        with patch.object(Image.Image, 'load', side_effect=AssertionError('Do not decode pixels')):
            self.assertEqual(live.geometry(), (400, 300))


class RoiWorkerTests(unittest.TestCase):
    def run_worker(self, root, model_class, render=None, jpeg=None):
        module = types.ModuleType('patchcore_lite')
        module.PatchCoreLite = model_class
        module.overlay = render or Mock()
        worker = load_isolated_module('app.cameras.anomaly_worker', {
            'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
        def fetch(url, **kwargs):
            if jpeg is None:
                raise AssertionError('Paused ROI must not request analysis frames')
            response = io.BytesIO(jpeg)
            response.headers = {'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
            return response
        with patch.object(worker.urllib.request, 'urlopen', side_effect=fetch), \
                patch.object(worker.time, 'sleep', side_effect=StopLoop):
            with self.assertRaises(StopLoop): worker.main()
        return anomaly_control.read_json(root / 'status.json')

    def test_area_change_pauses_and_persists_without_reusing_or_deleting_old_bank(self):
        calls = []
        class Model:
            memory = None
            baseline_frames = 60
            def load(self, path): self.memory = True
            def set_roi(self, value):
                calls.append(value)
                self.memory = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(anomaly_control, 'DATA', root), \
                    patch.object(control, 'CONTROL', root / 'control'), \
                    patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}):
                (root / 'baseline.pt').write_bytes(b'previous-learned-bank')
                anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=1.2))
                anomaly_control.write_json(root / 'video-trigger.json', dict(id='old'))
                command = anomaly_control.request('roi', 1.2, roi=ROI)
                control.set_enabled(True)
                for _ in range(2):
                    state = self.run_worker(root, Model)
                    self.assertFalse(state['monitoring_enabled'])
                    self.assertFalse(state['baseline_ready'])
                    self.assertTrue(state['retraining_required'])
                    self.assertEqual(state['roi'], ROI)
                    self.assertEqual(state['last_request'], command['id'])
                    self.assertFalse(control.enabled())
                self.assertEqual(calls, [ROI, ROI])
                self.assertEqual((root / 'baseline.pt').read_bytes(), b'previous-learned-bank')
                self.assertEqual(anomaly_control.read_json(root / 'video-trigger.json'), {})
                self.assertEqual(anomaly_control.read_json(root / 'session.json')['roi'], ROI)

    def test_roi_event_keeps_original_jpeg_and_maps_heatmap_to_the_same_area(self):
        jpeg = b'\xff\xd8original-source\xff\xd9'
        class Model:
            memory = None
            baseline_frames = 60
            threshold = 1
            source_size = (1920, 1080)
            effective_input_size = (768, 432)
            def set_roi(self, value): self.roi = value
            def load(self, path): self.memory = True
            def features(self, image):
                if image != jpeg:
                    raise AssertionError('The model must receive the original source JPEG')
                return 'features'
            def score(self, features): return 2, 'heat'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(anomaly_control, 'DATA', root), \
                    patch.object(control, 'CONTROL', root / 'control'), \
                    patch.dict(os.environ, {'CAMERA_ANOMALY_CONFIRM_FRAMES': '1'}):
                (root / 'baseline.pt').write_bytes(b'roi-bank')
                anomaly_control.write_json(root / 'session.json', dict(roi=ROI, monitoring=True, multiplier=1))
                render = Mock(return_value=b'full-size-roi-heatmap')
                self.run_worker(root, Model, render, jpeg)
                event = json.loads(next((root / 'events').glob('*.json')).read_text())
                self.assertEqual((root / 'events' / event['image']).read_bytes(), jpeg)
                self.assertEqual(event['roi'], ROI)
                render.assert_called_once_with(jpeg, 'heat', 1, roi=ROI)

    def test_geometry_change_clears_confirmation_and_requires_learning(self):
        class Model:
            memory = None
            baseline_frames = 60
            def set_roi(self, value): self.roi = value
            def load(self, path): self.memory = True
            def features(self, image):
                self.memory = None
                raise ROIGeometryChanged()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(anomaly_control, 'DATA', root), \
                    patch.object(control, 'CONTROL', root / 'control'), \
                    patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}):
                (root / 'baseline.pt').write_bytes(b'roi-bank')
                anomaly_control.write_json(root / 'session.json', dict(roi=ROI, monitoring=True))
                anomaly_control.write_json(root / 'video-trigger.json', dict(id='old-event'))
                state = self.run_worker(root, Model, jpeg=b'\xff\xd8source\xff\xd9')
                self.assertEqual(state['state'], 'untrained')
                self.assertFalse(state['monitoring_enabled'])
                self.assertTrue(state['retraining_required'])
                self.assertTrue(anomaly_control.read_json(root / 'session.json')['baseline_invalidated'])
                self.assertEqual(anomaly_control.read_json(root / 'video-trigger.json'), {})
                self.assertFalse(list((root / 'events').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
