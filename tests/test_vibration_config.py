import json
import tempfile
import unittest
from pathlib import Path

from app.vibration import vibration_config as cfg


class ValidateTests(unittest.TestCase):
    def test_defaults_do_not_guess_a_port(self):
        config = cfg.default_config()
        self.assertEqual(config["port"], "")
        self.assertEqual(config["baud"], 9600)
        self.assertEqual(config["sensors"], [])

    def test_hex_addresses_and_default_names(self):
        config = cfg.validate({"sensors": [{"address": "0x50"}, {"address": 2, "name": "pump-1"}]})
        self.assertEqual([s["address"] for s in config["sensors"]], [2, 0x50])
        self.assertEqual(config["sensors"][1]["name"], "wtvb01-50")
        self.assertTrue(config["sensors"][0]["enabled"])

    def test_rejections(self):
        bad = [
            {"baud": 14400},
            {"port": "ttyUSB0"},
            {"poll_interval_sec": -1},
            {"read_profile": "everything"},
            {"sensors": [{"address": 0}]},
            {"sensors": [{"address": 0x80}]},
            {"sensors": [{"address": True}]},
            {"sensors": [{"address": 1}, {"address": "0x01"}]},
            {"sensors": [{"address": 1, "name": "a"}, {"address": 2, "name": "a"}]},
            {"sensors": [{"address": 1, "name": "bad,name"}]},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                cfg.validate(data)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "vibration" / "config.json"

    def test_missing_file_is_default_without_error(self):
        config, error = cfg.load(self.path)
        self.assertIsNone(error)
        self.assertEqual(config, cfg.default_config())

    def test_save_and_load_round_trip(self):
        saved = cfg.save(self.path, {"port": "/dev/serial/by-id/usb-x", "baud": 230400, "sensors": [{"address": 3}]})
        loaded, error = cfg.load(self.path)
        self.assertIsNone(error)
        self.assertEqual(loaded, saved)
        self.assertEqual(json.loads(self.path.read_text())["baud"], 230400)

    def test_invalid_file_falls_back_and_reports(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"baud": 1}')
        config, error = cfg.load(self.path)
        self.assertEqual(config, cfg.default_config())
        self.assertIn("baud", error)


if __name__ == "__main__":
    unittest.main()
