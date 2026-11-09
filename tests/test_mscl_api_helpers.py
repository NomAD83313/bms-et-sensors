import unittest

from app.mscl.mscl_api_helpers import (
    EXPORT_STORAGE_TRANSIENT_HINT,
    cached_node_snapshot,
    map_export_storage_error,
    parse_raw_node_id,
)


class MsclApiHelpersTests(unittest.TestCase):
    def test_parse_raw_node_id(self):
        self.assertEqual(parse_raw_node_id(16904), 16904)
        self.assertEqual(parse_raw_node_id("16904"), 16904)
        self.assertEqual(parse_raw_node_id(" 16904 "), 16904)
        self.assertIsNone(parse_raw_node_id("16x"))
        self.assertIsNone(parse_raw_node_id(None))
        self.assertIsNone(parse_raw_node_id(True))

    def test_cached_node_snapshot(self):
        cache = {
            16904: {"model": "TC-Link-200-OEM", "current_rate": 112},
        }
        self.assertEqual(
            cached_node_snapshot("16904", cache),
            {"model": "TC-Link-200-OEM", "current_rate": 112},
        )
        self.assertEqual(cached_node_snapshot("bad", cache), {})

    def test_map_export_storage_error_transient(self):
        status_code, msg = map_export_storage_error("Failed to download data from the Node.")
        self.assertEqual(status_code, 409)
        self.assertEqual(msg, EXPORT_STORAGE_TRANSIENT_HINT)

    def test_map_export_storage_error_generic(self):
        status_code, msg = map_export_storage_error("Unexpected runtime error")
        self.assertEqual(status_code, 500)
        self.assertEqual(msg, "Unexpected runtime error")


if __name__ == "__main__":
    unittest.main()
