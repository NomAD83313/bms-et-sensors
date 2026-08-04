import unittest
from datetime import datetime, timezone

from app.graf.graf_csv_helpers import build_csv_content, make_csv_response


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


if __name__ == "__main__":
    unittest.main()
