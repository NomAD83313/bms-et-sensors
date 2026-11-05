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
