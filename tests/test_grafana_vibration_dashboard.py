import json
import re
import unittest
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "grafana" / "provisioning" / "dashboards" / "vibration_dash.json"
SENSOR_FILTER = 'r["sensor"] =~ /^${sensor:regex}$/'


class VibrationDashboardTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        self.panels = [p for p in self.dashboard["panels"] if p["type"] != "row"]
        self.titles = {p["title"]: p for p in self.panels}

    def query(self, title, ref="A"):
        return next(t["query"] for t in self.titles[title]["targets"] if t["refId"] == ref)

    def test_identity_and_sensor_variable(self):
        self.assertEqual(self.dashboard["uid"], "bms_vibration")
        variable = self.dashboard["templating"]["list"][0]
        self.assertEqual(variable["name"], "sensor")
        self.assertIn('r["_measurement"] == "wtvb01"', variable["query"])
        self.assertEqual(variable["refresh"], 1)
        # No machine data is entered anywhere: the sensor is the only variable.
        self.assertEqual([v["name"] for v in self.dashboard["templating"]["list"]], ["sensor"])
        dashboard_text = json.dumps(self.dashboard)
        self.assertNotRegex(dashboard_text.lower(), r"\broll\b|roll_|orbit_1x|phase_1x")
        self.assertNotIn("line_speed", dashboard_text)
        self.assertNotIn("rpm", dashboard_text)

    def test_panel_ids_are_unique(self):
        ids = [p["id"] for p in self.dashboard["panels"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_data_queries_filter_the_selected_sensors(self):
        for panel in self.panels:
            for target in panel["targets"]:
                query = target["query"]
                self.assertRegex(query, r'r\["_measurement"\] == "wtvb01(_fft)?"', panel["title"])
                self.assertIn(SENSOR_FILTER, query, panel["title"])
                self.assertIn('pivot(rowKey: ["_time"], columnKey: ["series"], valueColumn: "_value")', query)

    def test_register_panels(self):
        expected = {
            "Acceleration": "acc_[xyz]_g",
            "Vibration velocity": "velocity_[xyz]_mm_s",
            "RMS vibration velocity": "velocity_rms_[xyz]_mm_s",
            "Vibration displacement": "displacement_[xyz]_um",
            "Dominant vibration frequency": "frequency_[xyz]_hz",
            "Sensor spectrum focus points": "energy_[xyz]_point[234]",
            "Sensor spectrum focus bands": "energy_[xyz]_band[1-6]",
            "Kurtosis": "/^kurtosis_[xyz]$/",
            "Crest factor": "/^crest_factor_[xyz]$/",
        }
        for title, field_pattern in expected.items():
            self.assertIn(field_pattern, self.query(title), title)

    def test_kurtosis_and_crest_factor_are_separate_panels(self):
        self.assertNotIn("Kurtosis and crest factor", self.titles)
        self.assertNotIn("crest_factor", self.query("Kurtosis"))
        self.assertNotIn("kurtosis", self.query("Crest factor"))
        self.assertEqual(self.titles["Kurtosis"]["gridPos"]["y"], self.titles["Crest factor"]["gridPos"]["y"])

    def test_rms_velocity_follows_instant_velocity(self):
        y = {title: self.titles[title]["gridPos"]["y"] for title in ("Vibration velocity", "RMS vibration velocity")}
        self.assertEqual(y["RMS vibration velocity"], y["Vibration velocity"] + self.titles["Vibration velocity"]["gridPos"]["h"])

    def test_fft_section(self):
        rows = [p for p in self.dashboard["panels"] if p["type"] == "row"]
        self.assertTrue(any("FFT" in r["title"] for r in rows))
        fft = {title: field for title, field in {
            "Velocity RMS from FFT": "velocity_rms_[xyz]_mm_s",
            "FFT peak frequencies": "peak[123]_[xyz]_hz",
            "Main peak amplitude": "peak1_[xyz]_g",
            "Main peak · last capture": "peak1_[xyz]_hz",
            "Capture settings · last": "(sample_rate_hz|averages)",
            "Orbit axis ratio (FFT)": "/^orbit_(xy|xz|yz)_ratio$/",
        }.items()}
        for title, field_pattern in fft.items():
            query = self.query(title)
            self.assertIn('"wtvb01_fft"', query, title)
            self.assertIn(field_pattern, query, title)
        for title in ("Dominant vibration frequency", "FFT peak frequencies"):
            self.assertEqual([t["refId"] for t in self.titles[title]["targets"]], ["A"])
        for axis in "xyz":
            query = self.query(f"Band RMS (FFT) · {axis.upper()}")
            self.assertIn(f"band_{axis}_", query)
            self.assertIn("1 · 2–10 Hz", query)
            self.assertIn('sort(columns: ["series", "_time"])', query)

    def test_layout_has_no_overlaps(self):
        cells = set()
        for panel in self.dashboard["panels"]:
            pos = panel["gridPos"]
            self.assertLessEqual(pos["x"] + pos["w"], 24, panel["title"])
            for row in range(pos["y"], pos["y"] + pos["h"]):
                for col in range(pos["x"], pos["x"] + pos["w"]):
                    self.assertNotIn((row, col), cells, panel["title"])
                    cells.add((row, col))

    def test_fft_peak_tooltip_uses_amplitude_at_capture_time(self):
        title = "FFT peak frequencies"
        panel = self.titles[title]
        query = self.query(title)
        self.assertIn("peak[123]_[xyz]_g", query)
        self.assertIn('timeSrc: "_time"', query)
        custom = panel["fieldConfig"]["defaults"]["custom"]
        self.assertEqual(custom["drawStyle"], "points")
        self.assertFalse(custom["spanNulls"])
        amplitude = panel["fieldConfig"]["overrides"][0]
        self.assertEqual(amplitude["matcher"]["options"], " · amplitude$")
        properties = {p["id"]: p["value"] for p in amplitude["properties"]}
        self.assertEqual(properties["unit"], "suffix:g")
        self.assertEqual(properties["custom.hideFrom"], {"viz": True, "legend": True, "tooltip": False})

    def test_fft_amplitude_and_rms_use_soft_ranges(self):
        for title in ("Main peak amplitude", "Velocity RMS from FFT", "Band RMS (FFT) · X",
                      "Band RMS (FFT) · Y", "Band RMS (FFT) · Z"):
            defaults = self.titles[title]["fieldConfig"]["defaults"]
            self.assertNotIn("max", defaults)
            self.assertEqual(defaults["custom"]["axisSoftMin"], 0)
            self.assertGreater(defaults["custom"]["axisSoftMax"], 0)

    def test_orbit_axis_ratio_has_fixed_scale_and_capture_points(self):
        defaults = self.titles["Orbit axis ratio (FFT)"]["fieldConfig"]["defaults"]
        self.assertEqual((defaults["min"], defaults["max"]), (0, 1))
        self.assertEqual(defaults["custom"]["drawStyle"], "points")
        self.assertFalse(defaults["custom"]["spanNulls"])


if __name__ == "__main__":
    unittest.main()
