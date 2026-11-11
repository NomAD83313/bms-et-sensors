import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from app.cameras import anomaly_control


class AnomalyControlTests(unittest.TestCase):
    def test_event_ids_are_limited_to_a_safe_file_name_alphabet(self):
        for good in ('a', 'event-2026_11', 'x' * 100):
            self.assertTrue(anomaly_control.valid_event_id(good), good)
        for bad in ('', 'x' * 101, '../etc', 'a/b', 'a b', 'a.jpg', None, 5, b'abc'):
            self.assertFalse(anomaly_control.valid_event_id(bad), bad)

    def test_color_mode_validates_before_replacing_the_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            for mode in ('rgb', 'grayscale'):
                saved = anomaly_control.request('color', color_mode=mode)
                self.assertEqual(saved['color_mode'], mode)
            for mode in (None, '', 'RGB', 'gray', True, 1, []):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    anomaly_control.request('color', color_mode=mode)
            self.assertEqual(anomaly_control.read_json(Path(directory) / 'command.json'), saved)
            self.assertEqual(anomaly_control.command_mode('color', True, True, True), (False, False))

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

    def test_learning_time_is_validated_and_stored_with_the_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            for seconds in anomaly_control.LEARNING_SECONDS:
                command = anomaly_control.request('learn', learning_seconds=seconds)
                self.assertEqual(command['learning_seconds'], seconds)
            saved = anomaly_control.read_json(Path(directory) / 'command.json')
            for value in (0, 30, 300, 120.0, '120', True):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    anomaly_control.request('learn', learning_seconds=value)
            self.assertEqual(anomaly_control.read_json(Path(directory) / 'command.json'), saved)

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

    def test_input_size_command_validates_without_overwriting_on_invalid_request(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            for size in anomaly_control.INPUT_SIZES:
                result = anomaly_control.request('resize', input_size=size)
                self.assertEqual(result['input_size'], size)
            self.assertEqual(anomaly_control.command_mode('resize', True, True, True), (False, False))
            for value in (None, 0, 224, 320, 448, 512, 640, '640x640', '768x576', '320', True, float('nan'), [], {}):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    anomaly_control.request('resize', input_size=value)
            self.assertEqual(anomaly_control.read_json(Path(directory) / 'command.json'), result)

    def test_confirmation_setting_validates_and_preserves_session_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            command = anomaly_control.request('confirmation', confirmation_frames=1)
            self.assertEqual(command['confirmation_frames'], 1)
            for learning, monitoring in ((False, True), (True, False), (False, False)):
                self.assertEqual(anomaly_control.command_mode('confirmation', True, learning, monitoring), (learning, monitoring))
            for value in (None, 0, 6, 1.5, True, '1'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    anomaly_control.request('confirmation', confirmation_frames=value)
            self.assertEqual(anomaly_control.read_json(Path(directory) / 'command.json'), command)
            confirmation = anomaly_control.Confirmation(required=1)
            self.assertEqual(confirmation.update(2, 1, 0), (True, True))

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
