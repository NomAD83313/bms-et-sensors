import unittest
from datetime import datetime, timezone

from app.graf.graf_csv_helpers import append_series_rows, build_csv_content, make_csv_response


def _parse_iso_ts(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


class GrafCsvHelpersTests(unittest.TestCase):
    def test_build_csv_content_uses_selected_delimiter(self):
        csv_content = build_csv_content(
            {"2026-05-07T10:00:00Z": {"sensor_a": 23.456}},
            ["sensor_a", "sensor_b"],
            parse_iso_ts_fn=_parse_iso_ts,
            precision=2,
            delimiter=";",
            decimal_separator=",",
        )

        self.assertIn("timestamp_utc;timestamp_unix_ms;sensor_a;sensor_b\r\n", csv_content)
        self.assertIn("2026-05-07 10:00:00.000;1778148000000;23,46;\r\n", csv_content)

    def test_make_csv_response_adds_utf8_bom_for_spreadsheet_apps(self):
        response = make_csv_response("name,value\r\nДатчик,1\r\n", "export.csv")
        self.assertTrue(response.get_data().startswith("\xef\xbb\xbf".encode("latin1")))
        self.assertEqual(response.mimetype, "text/csv")

    def test_append_series_rows_keeps_duplicate_channel_columns_distinct_and_ordered(self):
        by_ts = {}
        cols = []
        append_series_rows(
            by_ts,
            cols,
            [
                {"name": "device=redlab_A | channel=ch0", "points": [{"t": "2026-05-07T10:00:00Z", "v": 10}]},
                {"name": "device=redlab_B | channel=ch0", "points": [{"t": "2026-05-07T10:00:00Z", "v": 20}]},
            ],
            lambda _name: "outdoor_(d1ch0)",
        )

        self.assertEqual(cols, ["outdoor_(d1ch0)", "outdoor_(d1ch0)_2"])
        self.assertEqual(by_ts["2026-05-07T10:00:00Z"], {"outdoor_(d1ch0)": 10, "outdoor_(d1ch0)_2": 20})


if __name__ == "__main__":
    unittest.main()
