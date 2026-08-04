import unittest

from app.mscl.mscl_export_helpers import (
    filter_rows_by_host_window,
    parse_iso_utc_to_ns,
    resolve_export_time_window,
)


class ExportHelpersTests(unittest.TestCase):
    def test_parse_iso_utc_to_ns_valid(self):
        ts_ns = parse_iso_utc_to_ns("2026-02-11T12:00:00Z", "ui_from")
        self.assertTrue(isinstance(ts_ns, int))
        self.assertGreater(ts_ns, 0)

    def test_parse_iso_utc_to_ns_missing(self):
        with self.assertRaises(ValueError):
            parse_iso_utc_to_ns("", "ui_from")

    def test_resolve_export_time_window_ui(self):
        w_from, w_to, origin = resolve_export_time_window(
            export_format="csv",
            ui_window_from_ns=100,
            ui_window_to_ns=200,
            host_hours=None,
            now_ns=500,
        )
        self.assertEqual((w_from, w_to, origin), (100, 200, "ui"))

    def test_resolve_export_time_window_host_hours(self):
        w_from, w_to, origin = resolve_export_time_window(
            export_format="json",
            ui_window_from_ns=None,
            ui_window_to_ns=None,
            host_hours=1.0,
            now_ns=3_600_000_000_000,
        )
        self.assertEqual(origin, "host_hours")
        self.assertEqual(w_to - w_from, 3_600_000_000_000)

    def test_filter_rows_by_host_window(self):
        rows = [
            {"timestamp_ns": 100, "value": 1.0},
            {"timestamp_ns": 200, "value": 2.0},
            {"timestamp_ns": 300, "value": 3.0},
        ]
        out = filter_rows_by_host_window(rows, window_from_ns=180, window_to_ns=310, time_offset_ns=0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["value"], 2.0)
        self.assertEqual(out[1]["value"], 3.0)

    def test_filter_rows_by_host_window_with_offset(self):
        rows = [{"timestamp_ns": 100, "value": 1.0}]
        out = filter_rows_by_host_window(rows, window_from_ns=120, window_to_ns=140, time_offset_ns=30)
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
