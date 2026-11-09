import unittest

from app.mscl.mscl_write_payload_helpers import normalize_write_payload, to_opt_bool, to_opt_int


class WritePayloadHelpersTests(unittest.TestCase):
    def test_to_opt_int(self):
        self.assertEqual(to_opt_int(" 42 "), 42)
        self.assertEqual(to_opt_int(7.9), 7)
        self.assertIsNone(to_opt_int(""))
        self.assertIsNone(to_opt_int("abc"))
        self.assertIsNone(to_opt_int(None))

    def test_to_opt_bool(self):
        self.assertTrue(to_opt_bool("true"))
        self.assertTrue(to_opt_bool("ON"))
        self.assertFalse(to_opt_bool("off"))
        self.assertFalse(to_opt_bool(""))
        self.assertTrue(to_opt_bool(1))
        self.assertFalse(to_opt_bool(0))

    def test_normalize_with_cache_fallbacks(self):
        data = {
            "node_id": 16904,
            "sample_rate": "",
            "tx_power": None,
            "channels": ["1", 2, 3, "x"],
            "lost_beacon_timeout": "120",
            "inactivity_timeout": "",
            "data_mode": "",
        }
        cached = {
            "current_rate": 112,
            "current_power": 10,
            "current_inactivity_timeout": 3600,
            "current_data_mode": 1,
        }
        out = normalize_write_payload(data=data, cached=cached)
        self.assertEqual(out["sample_rate"], 112)
        self.assertEqual(out["tx_power"], 10)
        self.assertEqual(out["channels"], [1, 2])
        self.assertEqual(out["lost_beacon_timeout"], 120)
        self.assertEqual(out["inactivity_timeout"], 3600)
        self.assertEqual(out["data_mode"], 1)
        self.assertTrue(out["lost_beacon_enabled"])

    def test_normalize_channels_default(self):
        out = normalize_write_payload(data={"node_id": 1}, cached={})
        self.assertEqual(out["channels"], [1])


if __name__ == "__main__":
    unittest.main()
