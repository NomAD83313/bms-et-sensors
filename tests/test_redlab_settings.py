import os
import unittest
from unittest.mock import patch

from app.redlab.redlab_settings import ReadSettings, RedLabSettings


class RedLabSettingsTests(unittest.TestCase):
    def test_environment_preserves_existing_names_and_bounds(self):
        with patch.dict(os.environ, {
            "INFLUX_TOKEN": "test-token", "INFLUX_ORG": "test-org", "INFLUX_BUCKET": "test-bucket",
            "REDLAB_ACTIVE_UNIQUE_ID": " device ", "REDLAB_READ_MODE": " SINGLE ",
            "REDLAB_LOOP_SLEEP": "0.25", "REDLAB_REENUMERATION_RECOVERY": "off",
            "REDLAB_REENUMERATION_CONFIRMATIONS": "0", "REDLAB_REENUMERATION_DEBOUNCE_SECONDS": "-1",
            "REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS": "-1", "REDLAB_WRITE_QUEUE_MAX_BATCHES": "0",
            "REDLAB_WRITE_RETRY_SECONDS": "0",
        }, clear=True):
            settings = RedLabSettings.from_env()
        self.assertEqual((settings.token, settings.org, settings.bucket), ("test-token", "test-org", "test-bucket"))
        self.assertEqual(settings.active_unique_id, "device")
        self.assertFalse(settings.reenumeration_recovery)
        self.assertEqual((settings.reenumeration_confirmations, settings.reenumeration_debounce, settings.reenumeration_cooldown), (1, 0, 0))
        self.assertEqual((settings.write_queue_max_batches, settings.write_retry_seconds), (1, 0.1))
        reads = ReadSettings(settings)
        self.assertEqual((reads.get_read_mode(), reads.get_loop_sleep()), ("single", 0.25))

    def test_invalid_initial_mode_falls_back_to_list(self):
        reads = ReadSettings(RedLabSettings(read_mode="invalid"))
        self.assertEqual(reads.get_read_mode(), "list")
        reads.set_read_mode("single")
        reads.set_read_mode("invalid")
        self.assertEqual(reads.get_read_mode(), "single")


if __name__ == "__main__":
    unittest.main()
