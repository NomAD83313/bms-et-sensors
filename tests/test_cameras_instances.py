"""Several camera instances (data, control, preview) in one analysis process stay independent."""
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.cameras import anomaly_control, control
from tests.isolated_import import load_isolated_module
from tests.test_cameras_worker_quality import FakeModelGeometry


class ControlDirectoryTests(unittest.TestCase):
    def test_functions_use_the_given_directory_and_the_module_default_otherwise(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, default = root / 'first', root / 'second', root / 'default'
            with patch.object(control, 'CONTROL', default):
                control.set_enabled(False, first)
                self.assertFalse(control.enabled(first))
                self.assertTrue(control.enabled(second))
                self.assertTrue(control.enabled())
                control.heartbeat('recording', first, event_active=True)
                self.assertEqual(control.recording_state(first), 'recording')
                self.assertTrue(control.recording_details(first)['event_active'])
                self.assertEqual(control.recording_state(second), 'offline')
                control.set_enabled(False)
                self.assertFalse(control.enabled())
                self.assertTrue((default / 'paused').exists())
                control.follow_detection(True, 'anomaly', first)
                self.assertTrue(control.enabled(first))
                self.assertFalse(control.enabled(), 'The default directory is untouched')
                control.follow_detection(False, 'continuous', first)
                self.assertTrue(control.enabled(first), 'Only event-only mode follows detection')


class TwoInstancesTests(unittest.TestCase):
    def build_module(self, root):
        class Model(FakeModelGeometry):
            memory = True
            baseline_frames = 60
            threshold = 1

            def load(self, path):
                self.memory = True

            def set_roi(self, roi):
                self.roi = roi

        module = types.ModuleType('patchcore_lite')
        module.PatchCoreLite, module.overlay = Model, Mock()
        with patch.object(anomaly_control, 'DATA', root / 'process-default'), \
                patch.object(control, 'CONTROL', root / 'process-control'):
            return load_isolated_module('app.cameras.anomaly_worker', {
                'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})

    def test_instances_keep_their_own_files_commands_flags_and_preview_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker_module = self.build_module(root)
            for name in ('a', 'b'):
                (root / name / 'data').mkdir(parents=True)
                (root / name / 'data' / 'baseline.npz').write_bytes(b'baseline')
                anomaly_control.write_json(root / name / 'data' / 'session.json',
                                           dict(monitoring=True, multiplier=1.25))
            with patch.object(control, 'CONTROL', root / 'process-control'):
                cameras = {name: worker_module.AnomalyWorker(
                    data=root / name / 'data', control_dir=root / name / 'control',
                    preview_url=f'http://127.0.0.1:90{index}/api/live.jpg', recording_mode='anomaly')
                    for index, name in enumerate(('a', 'b'))}
                a, b = cameras['a'], cameras['b']
                self.assertTrue(a.monitoring and b.monitoring)
                self.assertNotEqual(a.preview, b.preview)
                self.assertTrue((root / 'a' / 'data' / 'status.json').exists())
                self.assertTrue((root / 'b' / 'data' / 'status.json').exists())
                self.assertTrue((root / 'a' / 'data' / 'events').is_dir())
                self.assertFalse((root / 'process-default').exists(), 'The process default must not be used')

                # A command for one camera does not reach the other.
                anomaly_control.write_json(root / 'a' / 'data' / 'command.json',
                                           dict(id='pause-a', action='pause', multiplier=1.25))
                a.apply_pending_command()
                b.apply_pending_command()
                self.assertFalse(a.monitoring)
                self.assertTrue(b.monitoring)
                self.assertEqual(anomaly_control.read_json(root / 'a' / 'data' / 'session.json')['last_request'], 'pause-a')
                self.assertNotIn('last_request', anomaly_control.read_json(root / 'b' / 'data' / 'session.json'))

                # Event-only recording follows each camera's own detection through its own flag.
                a.state = a.status_snapshot()
                control.follow_detection(a.monitoring, a.recording_mode, a.control_dir)
                control.follow_detection(b.monitoring, b.recording_mode, b.control_dir)
                self.assertFalse(control.enabled(root / 'a' / 'control'))
                self.assertTrue(control.enabled(root / 'b' / 'control'))
                a.handle_geometry_change()
                self.assertFalse((root / 'process-control' / 'paused').exists())

                # Each instance fetches from its own preview.
                requested = []

                def fetch(url, timeout=None):
                    requested.append(url)
                    raise OSError('stop')

                with patch.object(worker_module.urllib.request, 'urlopen', side_effect=fetch):
                    for camera in (a, b):
                        with self.assertRaises(OSError):
                            camera.fetch_analysis_frame()
                self.assertEqual(requested, ['http://127.0.0.1:900/api/live.jpg?analysis=1',
                                             'http://127.0.0.1:901/api/live.jpg?analysis=1'])

    def test_a_single_camera_container_still_uses_the_environment_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker_module = self.build_module(root)
            with patch.dict(os.environ, {'CAMERA_ANOMALY_PREVIEW_URL': 'http://127.0.0.1:8099/api/live.jpg',
                                         'CAMERA_RECORDING_MODE': 'anomaly'}), \
                    patch.object(worker_module, 'DATA', root / 'env-data'), \
                    patch.object(control, 'CONTROL', root / 'env-control'):
                worker = worker_module.AnomalyWorker()
                self.assertEqual(worker.data, root / 'env-data')
                self.assertIsNone(worker.control_dir)
                self.assertEqual(worker.preview, 'http://127.0.0.1:8099/api/live.jpg')
                self.assertEqual(worker.recording_mode, 'anomaly')
                self.assertTrue((root / 'env-data' / 'events').is_dir())


if __name__ == '__main__':
    unittest.main()


class StoppedCameraTests(TwoInstancesTests):
    def test_a_stopped_camera_does_not_fetch_frames_or_delete_its_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            worker_module=self.build_module(root)
            data,controls=root/'data',root/'control'
            data.mkdir();controls.mkdir()
            baseline=data/'baseline.npz';baseline.write_bytes(b'normal baseline')
            anomaly_control.write_json(data/'session.json',dict(monitoring=True,multiplier=1.3))
            worker=worker_module.AnomalyWorker(data=data,control_dir=controls,recording_mode='anomaly')
            (controls/'camera-stopped').touch()
            with patch.object(worker,'fetch_analysis_frame') as fetch:
                wait=worker.step()
            fetch.assert_not_called()
            self.assertFalse(worker.monitoring)
            self.assertEqual(baseline.read_bytes(),b'normal baseline')
            self.assertGreaterEqual(wait,1)
            self.assertFalse(control.enabled(controls))
