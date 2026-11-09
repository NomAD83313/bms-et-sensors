import unittest

from app.mscl.mscl_export_request_helpers import (
    ExportRequestValidationError,
    parse_export_storage_request,
)


def _parse_iso_stub(value, _name):
    s = str(value)
    if s == "bad":
        raise ValueError("Invalid ui_from. Use ISO datetime (example: 2026-02-11T12:00:00Z).")
    return int(s)


class ExportRequestHelpersTests(unittest.TestCase):
    def test_parse_defaults(self):
        out = parse_export_storage_request({}, _parse_iso_stub)
        self.assertEqual(out["export_format"], "csv")
        self.assertTrue(out["ingest_influx"])
        self.assertIsNone(out["host_hours"])

    def test_parse_valid_full(self):
        args = {
            "format": "json",
            "ingest_influx": "false",
            "ui_from": "100",
            "ui_to": "200",
            "host_hours": "3.5",
        }
        out = parse_export_storage_request(args, _parse_iso_stub)
        self.assertEqual(out["export_format"], "json")
        self.assertFalse(out["ingest_influx"])
        self.assertEqual(out["ui_window_from_ns"], 100)
        self.assertEqual(out["ui_window_to_ns"], 200)
        self.assertEqual(out["host_hours"], 3.5)

    def test_parse_none_format_with_window(self):
        args = {
            "format": "none",
            "ui_from": "100",
            "ui_to": "200",
        }
        out = parse_export_storage_request(args, _parse_iso_stub)
        self.assertEqual(out["export_format"], "none")
        self.assertEqual(out["ui_window_from_ns"], 100)
        self.assertEqual(out["ui_window_to_ns"], 200)

    def test_table_invalid_cases(self):
        cases = [
            ({"format": "xml"}, "Unsupported format"),
            ({"ui_from": "100"}, "Both ui_from and ui_to are required"),
            ({"ui_from": "100", "ui_to": "90"}, "ui_to must be greater than ui_from"),
            ({"ui_from": "bad", "ui_to": "200"}, "Invalid ui_from"),
            ({"host_hours": "abc"}, "Invalid host_hours"),
            ({"host_hours": "0"}, "host_hours must be > 0"),
        ]
        for args, msg in cases:
            with self.assertRaises(ExportRequestValidationError) as cm:
                parse_export_storage_request(args, _parse_iso_stub)
            self.assertIn(msg, str(cm.exception))
            self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
