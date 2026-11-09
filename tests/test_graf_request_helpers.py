import unittest
from datetime import datetime, timezone

from app.graf.graf_request_helpers import (
    resolve_dashboard_request,
    resolve_export_request,
)


def _safe_range(value):
    text = str(value or "5m").strip().lower()
    return text if text in {"5m", "1h", "custom"} else "5m"


def _safe_sample_key(value):
    text = str(value or "auto").strip().lower()
    return text if text in {"auto", "1hz"} else "auto"


def _safe_target_points(value):
    try:
        return int(value or 1400)
    except Exception:
        return 1400


def _parse_iso_ts(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except Exception:
        return None


class GrafRequestHelpersTests(unittest.TestCase):
    def setUp(self):
        self.allowed_ranges = {"5m": 300, "1h": 3600}
        self.sample_presets = {
            "auto": {"label": "Auto", "duration": None, "sec": None},
            "1hz": {"label": "1 Hz", "duration": "1s", "sec": 1.0},
        }

    def test_resolve_dashboard_request_rejects_invalid_custom_range(self):
        resolved, error = resolve_dashboard_request(
            args={"range": "custom", "from": "bad", "to": "also-bad"},
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=20000,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
            estimated_points_for_window_fn=lambda *_args: 300,
        )
        self.assertIsNone(resolved)
        self.assertEqual(error[1], 400)
        self.assertIn("Invalid custom range", error[0]["error"])

    def test_resolve_dashboard_request_returns_custom_window(self):
        resolved, error = resolve_dashboard_request(
            args={
                "range": "custom",
                "from": "2026-04-17T10:00:00Z",
                "to": "2026-04-17T10:10:00Z",
            },
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=20000,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
            estimated_points_for_window_fn=lambda *_args: 600,
        )
        self.assertIsNone(error)
        self.assertEqual(resolved["range_key"], "custom")
        self.assertEqual(resolved["window"], "1s")
        self.assertEqual(resolved["sample_label"], "Auto (~600 pts)")

    def test_resolve_export_request_rejects_dense_sampling(self):
        resolved, error = resolve_export_request(
            args={"range": "1h", "sample": "1hz"},
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=100,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
        )
        self.assertIsNone(resolved)
        self.assertEqual(error[1], 400)
        self.assertIn("too dense", error[0]["error"])

    def test_resolve_export_request_raw_mode(self):
        resolved, error = resolve_export_request(
            args={"range": "5m", "export_mode": "raw"},
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=10000,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
        )
        self.assertIsNone(error)
        self.assertEqual(resolved["window"], "__raw__")
        self.assertTrue(resolved["raw_mode"])

    def test_resolve_export_request_accepts_locale_delimiter(self):
        resolved, error = resolve_export_request(
            args={"range": "5m", "csv_delimiter": "semicolon"},
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=10000,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
        )
        self.assertIsNone(error)
        self.assertEqual(resolved["csv_delimiter"], ";")
        self.assertEqual(resolved["csv_decimal_separator"], ",")

    def test_resolve_export_request_defaults_invalid_delimiter_to_comma(self):
        resolved, error = resolve_export_request(
            args={"range": "5m", "csv_delimiter": "pipe"},
            default_range="5m",
            allowed_ranges=self.allowed_ranges,
            sample_presets=self.sample_presets,
            max_points_per_series=10000,
            safe_range_fn=_safe_range,
            safe_sample_key_fn=_safe_sample_key,
            safe_target_points_fn=_safe_target_points,
            parse_iso_ts_fn=_parse_iso_ts,
            window_for_target_points_fn=lambda *_args: "1s",
        )
        self.assertIsNone(error)
        self.assertEqual(resolved["csv_delimiter"], ",")
        self.assertEqual(resolved["csv_decimal_separator"], ".")


if __name__ == "__main__":
    unittest.main()
