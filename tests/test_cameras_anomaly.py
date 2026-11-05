import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.cameras import anomaly_control


class AnomalyControlTests(unittest.TestCase):
    def test_confirmation_requires_consecutive_scores_and_cooldown(self):
        confirmation = anomaly_control.Confirmation(required=3, cooldown=30)
        self.assertEqual(confirmation.update(2, 1, 0), (False, False))
        self.assertEqual(confirmation.update(0, 1, 2), (False, False))
        self.assertEqual(confirmation.update(2, 1, 4), (False, False))
        self.assertEqual(confirmation.update(2, 1, 6), (False, False))
        self.assertEqual(confirmation.update(2, 1, 8), (True, True))
        self.assertEqual(confirmation.update(2, 1, 10), (True, False))
        self.assertEqual(confirmation.update(2, 1, 38), (True, True))

    def test_fast_confirmation_and_new_episode_do_not_wait_for_snapshot_cooldown(self):
        confirmation = anomaly_control.Confirmation(required=2, cooldown=30)
        self.assertEqual(confirmation.update(2, 1, 0), (False, False))
        self.assertEqual(confirmation.update(2, 1, .5), (True, True))
        self.assertEqual(confirmation.update(2, 1, 1), (True, False))
        self.assertEqual(confirmation.update(0, 1, 1.5), (False, False))
        self.assertEqual(confirmation.update(2, 1, 2), (False, False))
        self.assertEqual(confirmation.update(2, 1, 2.5), (True, True))

    def test_commands_validate_threshold_and_action(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            request = anomaly_control.request('learn', 1.25)
            self.assertEqual(json.loads((Path(directory) / 'command.json').read_text()), request)
            for action, threshold in [('delete', 1), ('learn', 0), ('learn', float('nan')), ('learn', 21)]:
                with self.subTest(action=action, threshold=threshold), self.assertRaises(ValueError):
                    anomaly_control.request(action, threshold)

    def test_configure_accepts_values_above_old_limit_without_starting_detection(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            result = anomaly_control.request('configure', 8.5)
            self.assertEqual(result['multiplier'], 8.5)
            self.assertEqual(anomaly_control.command_mode('configure', True, False, False), (False, False))
            self.assertEqual(anomaly_control.command_mode('configure', True, True, False), (True, False))
            self.assertEqual(anomaly_control.command_mode('configure', True, False, True), (False, True))
            self.assertEqual(anomaly_control.validate_multiplier(.1), .1)
            self.assertEqual(anomaly_control.validate_multiplier(20), 20)
            for value in (.09, 20.1, float('inf'), float('nan')):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    anomaly_control.validate_multiplier(value)

    def test_stale_status_and_safe_snapshot_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            root = Path(directory)
            self.assertEqual(anomaly_control.status()['state'], 'offline')
            anomaly_control.write_json(root / 'status.json', dict(state='normal', updated=time.time()))
            self.assertEqual(anomaly_control.status()['state'], 'normal')
            (root / 'events').mkdir()
            (root / 'events/clip.jpg').write_bytes(b'jpeg')
            (root / 'events/link.jpg').symlink_to(root / 'status.json')
            self.assertEqual(anomaly_control.event_path('clip.jpg'), root / 'events/clip.jpg')
            for name in ['../status.json', '/etc/passwd', 'link.jpg', 'missing.jpg', 'clip.json']:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    anomaly_control.event_path(name)


if __name__ == '__main__':
    unittest.main()
