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


class FakeModelGeometry:
    input_size = anomaly_control.DEFAULT_INPUT_SIZE
    model_key = anomaly_control.DEFAULT_MODEL

    def set_input_size(self, size):
        self.input_size = size


class WorkerTimingTests(unittest.TestCase):
    def test_iteration_waits_only_for_remaining_detection_or_learning_interval(self):
        for elapsed, learning, expected_wait in ((.4, False, 0), (.05, False, .15), (.4, True, 1.6)):
            with self.subTest(elapsed=elapsed, learning=learning), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'baseline.pt').write_bytes(b'baseline')
                anomaly_control.write_json(root / 'session.json', dict(monitoring=True))
                if learning:
                    anomaly_control.write_json(root / 'command.json', dict(
                        id='learn-timing', action='learn', learning_seconds=180))
                clock = [100.]
                received = []

                class Model(FakeModelGeometry):
                    memory = None
                    baseline_frames = 60
                    threshold = 1
                    def load(self, path): self.memory = True
                    def features(self, jpeg):
                        received.append(jpeg)
                        clock[0] += elapsed
                        return 'features'
                    def score(self, features): return 0, None

                model_module = types.ModuleType('patchcore_lite')
                model_module.PatchCoreLite, model_module.overlay = Model, Mock()
                with patch.dict(os.environ, {'CAMERA_ANOMALY_INTERVAL_SEC': '.2'}), \
                        patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                    worker = load_isolated_module('app.cameras.anomaly_worker', {
                        'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': model_module})
                    jpeg = b'\xff\xd8full-frame\xff\xd9'
                    response = io.BytesIO(jpeg)
                    response.headers = {'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
                    with patch.object(worker.urllib.request, 'urlopen', return_value=response), \
                            patch.object(worker.time, 'monotonic', side_effect=lambda: clock[0]), \
                            patch.object(worker.time, 'sleep', side_effect=StopLoop) as sleep:
                        with self.assertRaises(StopLoop): worker.main()
                    self.assertEqual(received, [jpeg])
                    sleep.assert_called_once()
                    self.assertAlmostEqual(sleep.call_args.args[0], expected_wait, places=9)
                    state = anomaly_control.read_json(root / 'status.json')
                    self.assertEqual(state['state'], 'learning' if learning else 'normal')
                    self.assertEqual(state['inference_ms'], round(elapsed * 1000))
                    self.assertEqual(state['analysis_interval_sec'], 2 if learning else .2)
                    self.assertEqual(state['collected'], 1 if learning else 0)
                    self.assertEqual(state['target'], 90 if learning else 60)
                    self.assertEqual(state['learning_seconds'], 180 if learning else 120)


class WorkerFrameQualityTests(unittest.TestCase):
    def test_learning_receives_full_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anomaly_control.write_json(root/'command.json', dict(id='learn-full', action='learn'))
            jpeg = b'\xff\xd8full-sensor-jpeg\xff\xd9'
            received, fitted = [], []

            class Model(FakeModelGeometry):
                memory = None
                baseline_frames = 0
                threshold = 1
                def features(self, source):
                    received.append(source)
                    return 'features'
                def fit(self, samples):
                    fitted.extend(samples)
                    self.memory, self.baseline_frames = True, len(samples)
                def save(self, path): path.write_bytes(b'new-original-baseline')

            module = types.ModuleType('patchcore_lite')
            module.PatchCoreLite, module.overlay = Model, Mock()
            with patch.dict(os.environ, {'CAMERA_ANOMALY_NORMAL_FRAMES': '20'}), \
                    patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root/'control'):
                worker = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                def fetch(url, **kwargs):
                    self.assertIn('?analysis=1', url)
                    response = io.BytesIO(jpeg)
                    response.headers = {'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
                    return response
                iterations = []
                def sleep(_):
                    iterations.append(True)
                    if len(iterations) == 20: raise StopLoop()
                with patch.object(worker.urllib.request, 'urlopen', side_effect=fetch), \
                        patch.object(worker.time, 'sleep', side_effect=sleep):
                    with self.assertRaises(StopLoop): worker.main()
                self.assertEqual(fitted, ['features'] * 20)
                self.assertEqual(received, [jpeg] * 20)
                self.assertEqual(anomaly_control.read_json(root/'session.json')['analysis_source'], worker.ANALYSIS_SOURCE)

    def test_missing_full_source_header_cannot_train_or_create_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'baseline.pt').write_bytes(b'baseline')
            anomaly_control.write_json(root/'session.json', dict(monitoring=True))
            class Model(FakeModelGeometry):
                memory = None
                baseline_frames = 60
                def load(self, path): self.memory = True
                def features(self, source): raise AssertionError('Never analyze an unverified preview')
            module = types.ModuleType('patchcore_lite')
            module.PatchCoreLite, module.overlay = Model, Mock()
            with patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root/'control'):
                worker = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                response = io.BytesIO(b'\xff\xd8preview\xff\xd9'); response.headers = {}
                with patch.object(worker.urllib.request, 'urlopen', return_value=response), \
                        patch.object(worker.time, 'sleep', side_effect=StopLoop), self.assertLogs('camera-anomaly', level='WARNING'):
                    with self.assertRaises(StopLoop): worker.main()
                self.assertEqual(anomaly_control.read_json(root/'status.json')['state'], 'unavailable')
                self.assertEqual(list((root/'events').glob('*.json')), [])

    def test_event_preserves_source_jpeg_without_reencoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'existing-baseline')
            anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=1))
            jpeg = b'\xff\xd8original-full-resolution-jpeg\xff\xd9'

            class Model(FakeModelGeometry):
                memory = None
                baseline_frames = 60
                threshold = 1
                jpeg_draft = True
                preparation_metrics = dict(total_ms=4.2, decode_ms=1.2, transform_ms=2.0,
                                           tensor_ms=1.0, decoded_width=1640, decoded_height=1232)
                def load(self, path): self.memory = True
                def features(self, source):
                    if source != jpeg:
                        raise AssertionError('Analysis must use the full source JPEG')
                    self.source = source
                    return 'patches'
                def score(self, features): return 10, 'distances'

            model_module = types.ModuleType('patchcore_lite')
            render = Mock(return_value=b'full-resolution-heatmap')
            model_module.PatchCoreLite, model_module.overlay = Model, render
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly', 'CAMERA_ANOMALY_CONFIRM_FRAMES': '1'}), \
                    patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                worker = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': model_module})
                def fetch(url, **kwargs):
                    self.assertIn('?analysis=1', url)
                    response = io.BytesIO(jpeg)
                    response.headers = {'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
                    return response
                with patch.object(worker.urllib.request, 'urlopen', side_effect=fetch), \
                        patch.object(worker.time, 'sleep', side_effect=StopLoop):
                    with self.assertRaises(StopLoop):
                        worker.main()
                metadata = list((root / 'events').glob('*.json'))
                self.assertEqual(len(metadata), 1)
                status = json.loads((root / 'status.json').read_text())
                self.assertEqual(status['preparation_ms'], 4)
                self.assertEqual(status['jpeg_decode_ms'], 1)
                self.assertEqual(status['image_transform_ms'], 2)
                self.assertEqual(status['tensor_ms'], 1)
                self.assertTrue(status['jpeg_draft'])
                self.assertEqual((status['decoded_width'], status['decoded_height']), (1640, 1232))
                self.assertIn('fetch_ms', status)
                self.assertIn('encoder_ms', status)
                event = json.loads(metadata[0].read_text())
                self.assertEqual((root / 'events' / event['image']).read_bytes(), jpeg)
                self.assertEqual((root / 'events' / event['heatmap']).read_bytes(), b'full-resolution-heatmap')
                render.assert_called_once_with(jpeg, 'distances', 1, roi=None)

    def test_error_arriving_during_inference_cannot_create_event_or_learning_sample(self):
        for learning in [False, True]:
            with self.subTest(learning=learning), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'baseline.pt').write_bytes(b'existing-baseline')
                anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=1))
                if learning:
                    anomaly_control.write_json(root / 'command.json', dict(id='learn', action='learn', multiplier=1))
                calls = []

                class Model(FakeModelGeometry):
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
                        if '?analysis=1' in url:
                            response = io.BytesIO(b'\xff\xd8jpeg\xff\xd9')
                            response.headers = {'X-Camera-Frame-Token': 'original-token',
                                                'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
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
    def test_saved_full_frame_input_restores_effective_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anomaly_control.write_json(root / 'session.json', dict(input_size='1920x1080'))

            class Model(FakeModelGeometry):
                memory = None
                baseline_frames = 0
                effective_input_size = (768, 432)

                def set_input_size(self, size):
                    super().set_input_size(size)
                    self.effective_input_size = anomaly_control.input_dimensions(size)

            model_module = types.ModuleType('patchcore_lite')
            model_module.PatchCoreLite, model_module.overlay = Model, Mock()
            with patch.object(anomaly_control, 'DATA', root), \
                    patch.object(control, 'CONTROL', root / 'control'):
                worker = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control,
                    'patchcore_lite': model_module})
                with patch.object(worker.time, 'sleep', side_effect=StopLoop):
                    with self.assertRaises(StopLoop):
                        worker.main()
            state = anomaly_control.read_json(root / 'status.json')
            self.assertEqual(state['input_size'], '1920x1080')
            self.assertEqual((state['effective_input_width'], state['effective_input_height']),
                             (1920, 1080))

    def test_size_change_requires_new_learning_and_persists_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'old-baseline')
            anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=8.5))
            anomaly_control.write_json(root / 'command.json', dict(id='resize-1', action='resize', input_size='1024x576'))
            loads = []

            class Model(FakeModelGeometry):
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
                self.assertFalse((root / 'baseline.pt').exists())


class WorkerConfigurationChangeTests(unittest.TestCase):
    def test_model_command_pauses_and_persists_independent_model_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'previous-baseline')
            anomaly_control.write_json(root / 'session.json', dict(input_size='1024x576',
                monitoring=True, multiplier=2.8, confirmation_frames=3, last_request='old'))
            anomaly_control.write_json(root / 'command.json', dict(id='switch-model',
                action='model', model_key='mobilenetv3', multiplier=1.25))
            anomaly_control.write_json(root / 'video-trigger.json', dict(id='old-event'))
            class Model(FakeModelGeometry):
                model_key = 'resnet-fp32'
                memory = None
                baseline_frames = 60
                def load(self, path): self.memory = True
                def set_model(self, key):
                    self.model_key, self.memory, self.baseline_frames = key, None, 0
            module = types.ModuleType('patchcore_lite')
            module.PatchCoreLite, module.overlay = Model, Mock()
            pil = types.ModuleType('PIL'); pil.Image = Mock()
            with patch.dict(os.environ, {'CAMERA_RECORDING_MODE': 'anomaly'}), \
                    patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                for _ in range(2):
                    worker = load_isolated_module('app.cameras.anomaly_worker', {
                        'control': control, 'anomaly_control': anomaly_control,
                        'patchcore_lite': module, 'PIL': pil})
                    with patch.object(worker.time, 'sleep', side_effect=StopLoop), \
                            patch.object(worker.urllib.request, 'urlopen') as fetch:
                        with self.assertRaises(StopLoop): worker.main()
                    fetch.assert_not_called()
                    status = anomaly_control.read_json(root / 'status.json')
                    self.assertEqual((status['model_key'], status['heatmap_width'], status['heatmap_height']),
                                     ('mobilenetv3', 64, 36))
                    self.assertTrue(status['retraining_required'])
                    self.assertFalse(status['monitoring_enabled'])
                    self.assertFalse(control.enabled())
                    self.assertEqual(status['multiplier'], 2.8)
                    self.assertEqual(status['confirmation_frames'], 3)
                self.assertEqual(anomaly_control.read_json(root / 'session.json')['model_key'], 'mobilenetv3')
                self.assertEqual(anomaly_control.read_json(root / 'video-trigger.json'), {})
                self.assertFalse((root / 'baseline.pt').exists())

    def test_encoder_change_discards_baseline_and_requires_new_learning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'previous-encoder-baseline')
            anomaly_control.write_json(root / 'session.json', dict(input_size='1280x720',
                monitoring=True, multiplier=1.1, confirmation_frames=1, last_request='learn-previous'))
            anomaly_control.write_json(root / 'command.json', dict(id='learn-previous', action='learn'))
            anomaly_control.write_json(root / 'video-trigger.json', dict(id='previous-event'))
            loads = []
            class Model(FakeModelGeometry):
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
                self.assertFalse((root / 'baseline.pt').exists())



class WorkerConfirmationTests(unittest.TestCase):
    def test_confirmation_applies_and_survives_restart_without_invalidating_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'existing-baseline')
            anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=8.5))
            anomaly_control.write_json(root / 'command.json', dict(id='confirm-1', action='confirmation', confirmation_frames=1))

            class Model(FakeModelGeometry):
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
                    response = io.BytesIO(b'\xff\xd8jpeg\xff\xd9')
                    response.headers = {'X-Camera-Analysis-Source': worker.ANALYSIS_SOURCE}
                    with patch.object(worker.time, 'sleep', side_effect=StopLoop), patch.object(worker.urllib.request, 'urlopen', return_value=response):
                        with self.assertRaises(StopLoop): worker.main()
                    state = json.loads((root / 'status.json').read_text())
                    self.assertEqual((state['confirmation_frames'], state['multiplier']), (1, 8.5))
                    self.assertTrue(state['baseline_ready'])
                    self.assertTrue(state['monitoring_enabled'])
                    self.assertFalse(state['retraining_required'])
                    self.assertTrue(control.enabled())
                    self.assertEqual((root / 'baseline.pt').read_bytes(), b'existing-baseline')
