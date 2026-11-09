import unittest
from datetime import datetime

from app.graf.graf_series_helpers import normalize_mscl_display_series, series_median_interval_ms


def _parse_iso_ts(raw):
    if raw is None:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


class GrafSeriesHelpersTests(unittest.TestCase):
    def test_mscl_display_series_normalizes_stream_timestamps_for_cadence(self):
        series = [
            {
                "name": "source=mscl_config_stream | node_id=16904 | channel=ch1",
                "points": [
                    {"t": "2026-05-04T07:16:00.000000Z", "v": 1.0},
                    {"t": "2026-05-04T07:16:00.000034Z", "v": 2.0},
                    {"t": "2026-05-04T07:16:00.000068Z", "v": 3.0},
                    {"t": "2026-05-04T07:16:00.000102Z", "v": 4.0},
                ],
            }
        ]

        raw_cadence = series_median_interval_ms(series, _parse_iso_ts)
        normalized = normalize_mscl_display_series(series, _parse_iso_ts)
        normalized_cadence = series_median_interval_ms(normalized, _parse_iso_ts)

        self.assertLess(raw_cadence, 0.1)
        self.assertAlmostEqual(normalized_cadence, 250.0, places=3)

    def test_mscl_node_export_series_is_not_normalized(self):
        series = [
            {
                "name": "source=mscl_node_export | node_id=16904 | channel=ch1",
                "points": [
                    {"t": "2026-05-04T07:16:00.000000Z", "v": 1.0},
                    {"t": "2026-05-04T07:16:00.000034Z", "v": 2.0},
                ],
            }
        ]

        normalized = normalize_mscl_display_series(series, _parse_iso_ts)

        self.assertEqual(normalized, series)


if __name__ == "__main__":
    unittest.main()
