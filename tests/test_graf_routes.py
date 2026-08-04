import unittest

from flask import Flask, Response

from app.graf.graf_routes import register_routes


def _build_ctx(**overrides):
    def render_index(view, default_range, default_refresh_sec, allowed_ranges):
        return f"{view}|{default_range}|{default_refresh_sec}|{sorted(allowed_ranges.keys())}"

    def resolve_dashboard_request(**_kwargs):
        return (
            {
                "view_mode": "all",
                "range_key": "5m",
                "sample_key": "auto",
                "target_points": 1400,
                "custom_from_raw": None,
                "custom_to_raw": None,
                "start_expr": "-5m",
                "stop_expr": None,
                "window_from_dt": None,
                "window_to_dt": None,
                "window": "1s",
                "sample_label": "Auto",
            },
            None,
        )

    def resolve_export_request(**_kwargs):
        return (
            {
                "range_key": "5m",
                "sample_key": "auto",
                "start_expr": "-5m",
                "stop_expr": None,
                "window": "1s",
                "raw_mode": False,
            },
            None,
        )

    def load_dashboard_panels(**_kwargs):
        return (
            {
                "mscl_temperature": [],
                "redlab_temperature": [],
                "almemo_live": [],
                "pyrometers_temperature": [],
                "messkluppe_force": [],
                "messkluppe_orientation": [],
                "messkluppe_battery": [],
                "messkluppe_temperatures": [],
                "matter_temperature": [],
                "matter_humidity": [],
                "matter_pressure": [],
                "matter_pm": [],
                "matter_battery": [],
            },
            {
                "mscl_temperature": {"raw_cadence_ms": None},
                "redlab_temperature": {"raw_cadence_ms": None},
                "almemo_live": {"raw_cadence_ms": None},
                "pyrometers_temperature": {"raw_cadence_ms": None},
                "messkluppe_force": {"raw_cadence_ms": None},
                "messkluppe_orientation": {"raw_cadence_ms": None},
                "messkluppe_battery": {"raw_cadence_ms": None},
                "messkluppe_temperatures": {"raw_cadence_ms": None},
                "matter_temperature": {"raw_cadence_ms": None},
                "matter_humidity": {"raw_cadence_ms": None},
                "matter_pressure": {"raw_cadence_ms": None},
                "matter_pm": {"raw_cadence_ms": None},
                "matter_battery": {"raw_cadence_ms": None},
            },
        )

    def build_single_export_response(**_kwargs):
        return Response("single-export", mimetype="text/csv")

    def build_all_export_response(**_kwargs):
        return Response("all-export", mimetype="text/csv")

    ctx = {
        "render_index": render_index,
        "default_range": "5m",
        "default_refresh_sec": 5,
        "allowed_ranges": {"5m": 300, "1h": 3600},
        "influx_url": "http://influxdb:8086",
        "influx_token": "",
        "influx_org": "",
        "influx_bucket": "",
        "load_redlab_channels": lambda *_args: {"ch0": True},
        "save_redlab_channels": lambda *_args: {"ch0": False},
        "redlab_channel_state_path": "/tmp/redlab.json",
        "redlab_channel_keys": ["ch0"],
        "resolve_dashboard_request": resolve_dashboard_request,
        "resolve_export_request": resolve_export_request,
        "sample_presets": {"auto": {"label": "Auto"}},
        "max_points_per_series": 20000,
        "safe_range_fn": lambda value: value,
        "safe_sample_key_fn": lambda value: value,
        "safe_target_points_fn": lambda value: value,
        "parse_iso_ts_fn": lambda value: value,
        "window_for_target_points_fn": lambda *_args: "1s",
        "estimated_points_for_window_fn": lambda *_args: 300,
        "view_configs": {"all": {}},
        "to_iso_z_fn": lambda dt: getattr(dt, "isoformat", lambda: dt)(),
        "load_dashboard_panels": load_dashboard_panels,
        "load_mscl_series_fn": lambda *_args: [],
        "load_redlab_series_fn": lambda *_args: [],
        "load_almemo_series_fn": lambda *_args: [],
        "load_pyrometers_series_fn": lambda *_args: [],
        "load_messkluppe_series_fn": lambda *_args: [],
        "load_messkluppe_orientation_series_fn": lambda *_args: [],
        "load_messkluppe_battery_series_fn": lambda *_args: [],
        "load_messkluppe_temperature_series_fn": lambda *_args: [],
        "load_matter_series_fn": lambda *_args: [],
        "load_matter_humidity_series_fn": lambda *_args: [],
        "load_matter_pressure_series_fn": lambda *_args: [],
        "load_matter_pm_series_fn": lambda *_args: [],
        "load_matter_environment_series_fn": lambda *_args: [],
        "load_matter_battery_series_fn": lambda *_args: [],
        "panel_raw_cadence_ms_fn": lambda *_args: None,
        "build_single_export_response": build_single_export_response,
        "build_all_export_response": build_all_export_response,
        "append_series_rows_fn": lambda *_args: None,
        "csv_export_response_fn": lambda **_kwargs: Response("csv-response", mimetype="text/csv"),
        "mscl_csv_column_name_fn": lambda name, *_args: name,
        "redlab_csv_column_name_fn": lambda name: name,
        "pyrometers_csv_column_name_fn": lambda name: name,
        "messkluppe_csv_column_name_fn": lambda name: name,
        "matter_csv_column_name_fn": lambda name: name,
        "almemo_csv_column_name_fn": lambda name: name,
        "mscl_source": "mscl_config_stream",
        "mscl_source_extra": "mscl_node_export",
    }
    ctx.update(overrides)
    return ctx


class GrafRoutesTests(unittest.TestCase):
    def _make_client(self, **ctx_overrides):
        app = Flask(__name__)
        register_routes(app, _build_ctx(**ctx_overrides))
        app.testing = True
        return app.test_client()

    def test_health_degraded_without_influx_credentials(self):
        client = self._make_client(influx_token="", influx_org="", influx_bucket="")
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "degraded")
        self.assertTrue(payload["success"])

    def test_health_ok_with_influx_credentials(self):
        client = self._make_client(influx_token="x", influx_org="org", influx_bucket="bucket")
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")

    def test_dashboard_alias_matches_primary_route(self):
        client = self._make_client()
        primary = client.get("/api/dashboard")
        alias = client.get("/api/data")
        self.assertEqual(primary.status_code, 200)
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(primary.get_json(), alias.get_json())

    def test_redlab_channels_post_rejects_non_object_json(self):
        client = self._make_client()
        response = client.post("/api/redlab/channels", json=[1, 2, 3])
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"], "Expected JSON object")

    def test_export_route_uses_shared_builder(self):
        client = self._make_client()
        response = client.get("/api/export/mscl.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertEqual(response.get_data(as_text=True), "single-export")

    def test_messkluppe_export_route_uses_shared_builder(self):
        client = self._make_client()
        response = client.get("/api/export/messkluppe.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertEqual(response.get_data(as_text=True), "single-export")

    def test_dashboard_route_returns_expected_shape(self):
        client = self._make_client()
        response = client.get("/api/dashboard")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["range"], "5m")
        self.assertEqual(payload["view"], "all")
        self.assertIn("panels", payload)
        self.assertIn("panel_meta", payload)
        self.assertIn("sample_options", payload)

    def test_dashboard_route_propagates_validation_error(self):
        def resolve_dashboard_request_error(**_kwargs):
            return None, ({"success": False, "error": "Invalid custom range."}, 400)

        client = self._make_client(resolve_dashboard_request=resolve_dashboard_request_error)
        response = client.get("/api/dashboard")
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertIn("Invalid custom range", payload["error"])


if __name__ == "__main__":
    unittest.main()
