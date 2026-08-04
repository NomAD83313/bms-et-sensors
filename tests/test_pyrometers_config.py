import unittest

from app.pyrometers.pyrometers_config import load_config, logging_interval_ms, normalize_logging_hz


class PyrometersConfigTest(unittest.TestCase):
    def test_load_config_keeps_logging_frequency_user_controlled(self):
        config = load_config({"THERMOMETER_LOG_HZ": "20", "THERMOMETER_STREAM_STALE_SEC": "4.5"})

        self.assertEqual(config.log_hz, 20.0)
        self.assertEqual(config.stream_stale_sec, 4.5)

    def test_normalize_logging_hz_accepts_full_stream_aliases(self):
        for value in ("0", "0.0", "full", "max", "unlimited"):
            self.assertEqual(normalize_logging_hz(value, default_hz=10.0), 0.0)

    def test_normalize_logging_hz_falls_back_for_invalid_values(self):
        self.assertEqual(normalize_logging_hz("bad", default_hz=10.0), 10.0)
        self.assertEqual(normalize_logging_hz("-1", default_hz=10.0), 10.0)

    def test_logging_interval_ms_reflects_dropdown_frequency(self):
        self.assertEqual(logging_interval_ms(10.0), 100.0)
        self.assertEqual(logging_interval_ms(20.0), 50.0)
        self.assertEqual(logging_interval_ms(0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
