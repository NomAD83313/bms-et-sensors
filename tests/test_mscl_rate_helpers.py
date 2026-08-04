import unittest

from app.mscl.mscl_rate_helpers import (
    filter_sample_rates_for_model,
    is_tc_link_200_model,
    rate_label_to_hz,
    rate_label_to_interval_seconds,
    sample_rate_label,
)


class RateHelpersTests(unittest.TestCase):
    def test_model_detect(self):
        self.assertTrue(is_tc_link_200_model("TC-Link-200-OEM"))
        self.assertTrue(is_tc_link_200_model("63104100"))
        self.assertFalse(is_tc_link_200_model("other-model"))

    def test_rate_parsers(self):
        self.assertEqual(rate_label_to_hz("128 Hz"), 128.0)
        self.assertEqual(rate_label_to_hz("1 kHz"), 1000.0)
        self.assertEqual(rate_label_to_interval_seconds("every 30 seconds"), 30.0)
        self.assertEqual(rate_label_to_interval_seconds("every 2 minutes"), 120.0)
        self.assertEqual(rate_label_to_interval_seconds("every 1 hour"), 3600.0)
        self.assertIsNone(rate_label_to_interval_seconds("64 Hz"))

    def test_sample_rate_label(self):
        rate_map = {112: "64 Hz"}
        self.assertEqual(sample_rate_label(112, None, rate_map), "64 Hz")
        self.assertEqual(sample_rate_label(112, "every 5 seconds", rate_map), "every 5 seconds")
        self.assertEqual(sample_rate_label(112, "112", rate_map), "64 Hz")

    def test_filter_rates_for_tc_link(self):
        rates = [
            {"enum_val": 1, "str_val": "128 Hz"},
            {"enum_val": 2, "str_val": "512 Hz"},
            {"enum_val": 3, "str_val": "every 30 seconds"},
            {"enum_val": 4, "str_val": "every 7 seconds"},
        ]
        out = filter_sample_rates_for_model(
            model="TC-Link-200-OEM",
            supported_rates=rates,
            current_rate=1,
            rate_map={1: "128 Hz", 2: "512 Hz", 3: "every 30 seconds", 4: "every 7 seconds"},
            tc_link_200_rate_enums={1},
        )
        labels = [x["str_val"] for x in out]
        self.assertIn("128 Hz", labels)
        self.assertIn("every 30 seconds", labels)
        self.assertNotIn("512 Hz", labels)


if __name__ == "__main__":
    unittest.main()
