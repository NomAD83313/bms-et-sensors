import unittest

from app.mscl.mscl_utils import sample_rate_text_to_hz


class SampleRateTextToHzTests(unittest.TestCase):
    def test_hz_labels(self):
        self.assertEqual(sample_rate_text_to_hz("64 Hz"), 64.0)
        self.assertEqual(sample_rate_text_to_hz("1 hz"), 1.0)

    def test_khz_labels(self):
        self.assertEqual(sample_rate_text_to_hz("1 kHz"), 1000.0)
        self.assertEqual(sample_rate_text_to_hz("32khz"), 32000.0)

    def test_slow_mode_seconds(self):
        self.assertAlmostEqual(sample_rate_text_to_hz("every 2 seconds"), 0.5)
        self.assertAlmostEqual(sample_rate_text_to_hz("every 5 second"), 0.2)

    def test_slow_mode_minutes(self):
        self.assertAlmostEqual(sample_rate_text_to_hz("every 1 minute"), 1.0 / 60.0)
        self.assertAlmostEqual(sample_rate_text_to_hz("every 10 minutes"), 1.0 / 600.0)

    def test_slow_mode_hours(self):
        self.assertAlmostEqual(sample_rate_text_to_hz("every 1 hour"), 1.0 / 3600.0)
        self.assertAlmostEqual(sample_rate_text_to_hz("every 2 hours"), 1.0 / 7200.0)

    def test_invalid_inputs(self):
        self.assertIsNone(sample_rate_text_to_hz(""))
        self.assertIsNone(sample_rate_text_to_hz("unknown"))
        self.assertIsNone(sample_rate_text_to_hz("every 0 seconds"))


if __name__ == "__main__":
    unittest.main()
