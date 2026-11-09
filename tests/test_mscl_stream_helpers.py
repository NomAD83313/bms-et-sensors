import unittest

from app.mscl.mscl_stream_helpers import ns_to_iso_utc
from app.mscl.mscl_utils import sample_rate_text_to_hz


class StreamHelpersTests(unittest.TestCase):
    def test_ns_to_iso_utc_basic(self):
        ts_ns = 1_700_000_000_123_456_789
        iso = ns_to_iso_utc(ts_ns)
        self.assertTrue(isinstance(iso, str))
        self.assertTrue(iso.endswith("Z"))

    def test_ns_to_iso_utc_invalid(self):
        self.assertIsNone(ns_to_iso_utc("bad"))

    def test_rate_parser_reexport_used(self):
        self.assertEqual(sample_rate_text_to_hz("8 Hz"), 8.0)


if __name__ == "__main__":
    unittest.main()
