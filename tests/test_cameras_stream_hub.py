"""RTSP relay routing, private configuration and live camera additions."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from app.cameras.stream_hub import StreamHub
from app.cameras.camera_manager import CameraManager
from tests.test_cameras_manager import RegistryTests, camera


TEMPLATE = Path(__file__).resolve().parents[1] / 'config/camera-hub.example.json'


class StreamHubTests(unittest.TestCase):
    def test_config_shares_one_source_and_bypasses_local_csi(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mediamtx.yml'
            hub = StreamHub(path, TEMPLATE, 'rtsp://127.0.0.1:8554')
            remote = camera()
            local = camera('pi', url='http://127.0.0.1:8084/stream.ts')
            hub.sync([remote, local])
            config = json.loads(path.read_text())
            self.assertEqual(list(config['paths']), ['first'])
            self.assertEqual(config['paths']['first']['source'], remote['url'])
            self.assertTrue(config['paths']['first']['sourceOnDemand'])
            self.assertEqual(config['paths']['first']['rtspTransport'], 'tcp')
            self.assertEqual(hub.source_url(remote), 'rtsp://127.0.0.1:8554/first')
            self.assertEqual(hub.source_url(local), local['url'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            inode = path.stat().st_ino
            hub.sync([remote, local])
            self.assertEqual(path.stat().st_ino, inode, 'Unchanged config must not cause a relay reload')
            hub.sync([remote, local, camera('second')])
            self.assertEqual(set(json.loads(path.read_text())['paths']), {'first', 'second'})
            self.assertEqual(json.loads(path.read_text())['paths']['first'], config['paths']['first'])

    def test_failed_atomic_write_preserves_last_working_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mediamtx.yml'
            hub = StreamHub(path, TEMPLATE, 'rtsp://127.0.0.1:8554')
            hub.sync([camera()])
            before = path.read_bytes()
            with patch.object(Path, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    hub.sync([camera('second')])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])


class ManagerHubTests(RegistryTests):
    def test_both_consumers_use_same_hub_path_without_changing_registry_source(self):
        remote = self.registry.add(camera())
        hub = StreamHub(self.directory / 'hub.yml', TEMPLATE, 'rtsp://127.0.0.1:8554')
        manager = CameraManager(self.registry, hub=hub)
        self.assertEqual(manager.environment(remote)['CAMERA_URL'], 'rtsp://127.0.0.1:8554/first')
        self.assertEqual(self.registry.get('first')['url'], remote['url'])
