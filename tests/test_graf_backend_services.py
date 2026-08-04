import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.modules.setdefault("influxdb_client", types.SimpleNamespace(InfluxDBClient=object))
sys.modules.setdefault("flask", types.SimpleNamespace(Response=object))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "graf"))

from app.graf.graf_backend_services import _cadence_range_bounds


def _parse_iso_ts(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class GrafBackendServicesTests(unittest.TestCase):
    def test_cadence_range_uses_recent_relative_tail(self):
        start_expr, stop_expr = _cadence_range_bounds("-31d", None, _parse_iso_ts)
        self.assertEqual(start_expr, "-900s")
        self.assertIsNone(stop_expr)

    def test_cadence_range_limits_custom_window_to_recent_tail(self):
        start_expr, stop_expr = _cadence_range_bounds(
            'time(v: "2026-03-01T00:00:00Z")',
            'time(v: "2026-03-31T00:00:00Z")',
            _parse_iso_ts,
        )
        self.assertEqual(start_expr, 'time(v: "2026-03-30T23:45:00Z")')
        self.assertEqual(stop_expr, 'time(v: "2026-03-31T00:00:00Z")')

    def test_cadence_range_respects_short_custom_window_start(self):
        start_expr, stop_expr = _cadence_range_bounds(
            'time(v: "2026-03-31T00:05:00Z")',
            'time(v: "2026-03-31T00:10:00Z")',
            _parse_iso_ts,
            lookback=timedelta(minutes=15),
        )
        self.assertEqual(start_expr, 'time(v: "2026-03-31T00:05:00Z")')
        self.assertEqual(stop_expr, 'time(v: "2026-03-31T00:10:00Z")')


if __name__ == "__main__":
    unittest.main()
