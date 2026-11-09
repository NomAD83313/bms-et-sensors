import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_svcctl_mscl_guard import _load_module


class CameraGroupTests(unittest.TestCase):
    def test_group_actions_order_dependencies_and_preserve_manual_pause(self):
        mod = _load_module()
        client = mod.app.test_client()
        with tempfile.TemporaryDirectory() as directory, patch.object(mod, 'MANUAL_PAUSE_FILE', Path(directory) / 'pauses.json'):
            for target, names in [
                ('cameras', ['camera-recorder', 'camera-dashboard', 'camera-anomaly']),
                ('pi-camera', ['pi-camera-recorder', 'pi-camera-dashboard', 'pi-camera-anomaly']),
            ]:
                for action, expected in [
                    ('stop', [(name, False) for name in reversed(names)]),
                    ('start', [(name, True) for name in names]),
                    ('restart', [(name, False) for name in reversed(names)] + [(name, True) for name in names]),
                ]:
                    calls = []
                    with self.subTest(target=target, action=action), patch.object(mod, '_set_container_running', side_effect=lambda name, run: calls.append((name, run)) or 'done'):
                        response = client.post('/services/' + target + '/' + action)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(calls, expected)
                    self.assertEqual(target in response.get_json()['manual_pauses'], action == 'stop')
            self.assertEqual(client.post('/services/cameras/delete').status_code, 400)
            self.assertEqual(client.post('/services/unknown/start').status_code, 404)


if __name__ == '__main__':
    unittest.main()
