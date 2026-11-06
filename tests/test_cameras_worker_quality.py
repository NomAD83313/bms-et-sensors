import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

from app.cameras import anomaly_control, control
from tests.isolated_import import load_isolated_module


class StopLoop(BaseException):
    pass


class WorkerFrameQualityTests(unittest.TestCase):
    def test_error_arriving_during_inference_cannot_create_event_or_learning_sample(self):
        for learning in [False, True]:
            with self.subTest(learning=learning), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'baseline.pt').write_bytes(b'existing-baseline')
                anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=1))
                if learning:
                    anomaly_control.write_json(root / 'command.json', dict(id='learn', action='learn', multiplier=1))
                calls = []

                class Model:
                    memory = None
                    baseline_frames = 60
                    threshold = 1
                    def load(self, path): self.memory = True
                    def features(self, jpeg): calls.append('features'); return 'patches'
                    def score(self, features): return 10, None
                    def fit(self, samples): raise AssertionError('Rejected frames must not train a baseline')

                model_module = types.ModuleType('patchcore_lite')
                model_module.PatchCoreLite, model_module.overlay = Model, Mock()
                pil = types.ModuleType('PIL')
                pil.Image = Mock()
                dependencies = {'control': control, 'anomaly_control': anomaly_control,
                                'patchcore_lite': model_module, 'PIL': pil}
                with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly', 'CAMERA_ANOMALY_CONFIRM_FRAMES': '1'}), patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                    worker = load_isolated_module('app.cameras.anomaly_worker', dependencies)

                    def fetch(url, **kwargs):
                        if '?fresh=1' in url:
                            response = io.BytesIO(b'jpeg')
                            response.headers = {'X-Camera-Frame-Token': 'original-token'}
                            return response
                        self.assertIn('?check=original-token', url)
                        calls.append('validate')
                        raise urllib.error.HTTPError(url, 503, 'invalidated', {'X-Camera-Stream-Error': '1'}, None)

                    with patch.object(worker.urllib.request, 'urlopen', side_effect=fetch), patch.object(worker.time, 'sleep', side_effect=StopLoop), self.assertLogs('camera-anomaly', level='WARNING'):
                        with self.assertRaises(StopLoop):
                            worker.main()
                    self.assertEqual(calls, ['features', 'validate'])
                    state = json.loads((root / 'status.json').read_text())
                    self.assertEqual(state['state'], 'unavailable')
                    self.assertIn('Video stream error', state['message'])
                    self.assertEqual(state['collected'], 0)
                    self.assertNotIn('score', state)
                    self.assertEqual(anomaly_control.read_json(root / 'video-trigger.json', {}), {})
                    self.assertEqual(list((root / 'events').glob('*.json')), [])
                    self.assertEqual((root / 'baseline.pt').read_bytes(), b'existing-baseline')


class WorkerResizeTests(unittest.TestCase):
    def test_size_change_requires_new_learning_and_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'old-baseline')
            anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=8.5))
            anomaly_control.write_json(root / 'command.json', dict(id='resize-1', action='resize', input_size='1024x576'))
            loads = []

            class Model:
                def __init__(self):
                    self.memory = None
                    self.baseline_frames = 0
                    self.input_size = 224
                def load(self, path):
                    loads.append(path)
                    self.memory = True
                def set_input_size(self, size):
                    self.input_size = size
                    self.memory = None
                    self.baseline_frames = 0
                def features(self, jpeg):
                    raise AssertionError('Invalid baseline must not be used')

            model_module = types.ModuleType('patchcore_lite')
            model_module.PatchCoreLite, model_module.overlay = Model, Mock()
            pil = types.ModuleType('PIL')
            pil.Image = Mock()
            dependencies = {'control': control, 'anomaly_control': anomaly_control,
                            'patchcore_lite': model_module, 'PIL': pil}
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                for _ in range(2):
                    worker = load_isolated_module('app.cameras.anomaly_worker', dependencies)
                    with patch.object(worker.time, 'sleep', side_effect=StopLoop), patch.object(worker.urllib.request, 'urlopen') as fetch:
                        with self.assertRaises(StopLoop):
                            worker.main()
                    fetch.assert_not_called()
                    state = json.loads((root / 'status.json').read_text())
                    self.assertEqual((state['input_size'], state['heatmap_width'], state['heatmap_height']), ('1024x576', 128, 72))
                    self.assertTrue(state['retraining_required'])
                    self.assertFalse(state['baseline_ready'])
                    self.assertFalse(state['monitoring_enabled'])
                    self.assertEqual(state['multiplier'], 8.5)
                    self.assertFalse(control.enabled())
                self.assertEqual(len(loads), 1)
                self.assertEqual((root / 'baseline.pt').read_bytes(), b'old-baseline')


class WorkerMigrationTests(unittest.TestCase):
    def test_encoder_change_preserves_settings_and_requires_new_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'previous-encoder-baseline')
            anomaly_control.write_json(root / 'session.json', dict(input_size='1280x720',
                monitoring=True, multiplier=1.1, confirmation_frames=1, last_request='learn-previous'))
            anomaly_control.write_json(root / 'command.json', dict(id='learn-previous', action='learn'))
            anomaly_control.write_json(root / 'video-trigger.json', dict(id='previous-event'))
            loads = []
            class Model:
                memory = None
                baseline_frames = 0
                def load(self, path):
                    loads.append(path)
                    raise ValueError('Previous encoder')
            model_module = types.ModuleType('patchcore_lite')
            model_module.PatchCoreLite, model_module.overlay = Model, Mock()
            pil = types.ModuleType('PIL'); pil.Image = Mock()
            dependencies = {'control': control, 'anomaly_control': anomaly_control,
                            'patchcore_lite': model_module, 'PIL': pil}
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                for _ in range(2):
                    worker = load_isolated_module('app.cameras.anomaly_worker', dependencies)
                    with patch.object(worker.time, 'sleep', side_effect=StopLoop), patch.object(worker.urllib.request, 'urlopen') as fetch:
                        with self.assertRaises(StopLoop): worker.main()
                    fetch.assert_not_called()
                    state = anomaly_control.read_json(root / 'status.json')
                    self.assertEqual((state['input_size'], state['multiplier'], state['confirmation_frames']),
                                     ('1280x720', 1.1, 1))
                    self.assertTrue(state['retraining_required'])
                    self.assertFalse(state['baseline_ready'])
                    self.assertFalse(state['monitoring_enabled'])
                    self.assertFalse(control.enabled())
                self.assertEqual(len(loads), 1)
                self.assertEqual(anomaly_control.read_json(root / 'video-trigger.json'), {})
                self.assertEqual((root / 'baseline.pt').read_bytes(), b'previous-encoder-baseline')

    def test_square_session_migrates_without_using_baseline_or_replaying_old_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'old-square-baseline')
            anomaly_control.write_json(root / 'session.json', dict(input_size=448, monitoring=True,
                multiplier=8.5, confirmation_frames=3, last_request='old'))
            anomaly_control.write_json(root / 'command.json', dict(id='pending', action='learn'))
            anomaly_control.write_json(root / 'video-trigger.json', dict(id='old-event'))
            class Model:
                memory = None
                baseline_frames = 0
                def load(self, path): raise AssertionError('Square baseline must not load')
            model_module = types.ModuleType('patchcore_lite')
            model_module.PatchCoreLite, model_module.overlay = Model, Mock()
            pil = types.ModuleType('PIL'); pil.Image = Mock()
            dependencies = {'control': control, 'anomaly_control': anomaly_control,
                            'patchcore_lite': model_module, 'PIL': pil}
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE':'anomaly'}), patch.object(anomaly_control,'DATA',root), patch.object(control,'CONTROL',root/'control'):
                for _ in range(2):
                    worker = load_isolated_module('app.cameras.anomaly_worker',dependencies)
                    with patch.object(worker.time,'sleep',side_effect=StopLoop), patch.object(worker.urllib.request,'urlopen') as fetch:
                        with self.assertRaises(StopLoop): worker.main()
                    fetch.assert_not_called()
                    state = json.loads((root/'status.json').read_text())
                    self.assertEqual((state['input_size'],state['input_width'],state['input_height']),('768x432',768,432))
                    self.assertEqual((state['heatmap_width'],state['heatmap_height']),(96,54))
                    self.assertEqual((state['multiplier'],state['confirmation_frames']),(8.5,3))
                    self.assertTrue(state['retraining_required'])
                    self.assertFalse(state['baseline_ready'])
                    self.assertFalse(control.enabled())
                self.assertEqual(anomaly_control.read_json(root/'video-trigger.json'),{})
                self.assertEqual((root/'baseline.pt').read_bytes(),b'old-square-baseline')


class WorkerConfirmationTests(unittest.TestCase):
    def test_confirmation_applies_and_survives_restart_without_invalidating_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'existing-baseline')
            anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=8.5))
            anomaly_control.write_json(root / 'command.json', dict(id='confirm-1', action='confirmation', confirmation_frames=1))

            class Model:
                memory = None
                baseline_frames = 60
                threshold = 1
                def load(self, path): self.memory = True
                def features(self, jpeg): return 'features'
                def score(self, features): return 0, None

            model_module = types.ModuleType('patchcore_lite')
            model_module.PatchCoreLite, model_module.overlay = Model, Mock()
            pil = types.ModuleType('PIL')
            pil.Image = Mock()
            dependencies = {'control': control, 'anomaly_control': anomaly_control,
                            'patchcore_lite': model_module, 'PIL': pil}
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly', 'CAMERA_ANOMALY_CONFIRM_FRAMES': '2'}), patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                for _ in range(2):
                    worker = load_isolated_module('app.cameras.anomaly_worker', dependencies)
                    response = io.BytesIO(b'jpeg')
                    response.headers = {}
                    with patch.object(worker.time, 'sleep', side_effect=StopLoop), patch.object(worker.urllib.request, 'urlopen', return_value=response):
                        with self.assertRaises(StopLoop): worker.main()
                    state = json.loads((root / 'status.json').read_text())
                    self.assertEqual((state['confirmation_frames'], state['multiplier']), (1, 8.5))
                    self.assertTrue(state['baseline_ready'])
                    self.assertTrue(state['monitoring_enabled'])
                    self.assertFalse(state['retraining_required'])
                    self.assertTrue(control.enabled())
                    self.assertEqual((root / 'baseline.pt').read_bytes(), b'existing-baseline')
