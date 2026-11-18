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
        names = [v["name"] for v in self.dashboard["templating"]["list"]]
        self.assertTrue({"line_speed", "roll_diameter"} <= set(names))

    def test_panel_ids_are_unique(self):
        ids = [p["id"] for p in self.dashboard["panels"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_data_queries_filter_the_selected_sensors(self):
        for panel in self.panels:
            for target in panel["targets"]:
                query = target["query"]
                if "from(bucket" not in query:
                    continue  # Expected-roll helpers computed from dashboard variables.
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
            "Harmonic points: 0.5x / 1x / 2x": "energy_[xyz]_point[234]",
            "Harmonic ratios": "energy_[xyz]_ratio_(2x_1x|half_1x)",
        }
        for title, field_pattern in expected.items():
            self.assertIn(field_pattern, self.query(title), title)

    def test_rms_velocity_follows_instant_velocity(self):
        y = {title: self.titles[title]["gridPos"]["y"] for title in ("Vibration velocity", "RMS vibration velocity")}
        self.assertEqual(y["RMS vibration velocity"], y["Vibration velocity"] + self.titles["Vibration velocity"]["gridPos"]["h"])

    def test_fft_section(self):
        rows = [p for p in self.dashboard["panels"] if p["type"] == "row"]
        self.assertTrue(any("FFT" in r["title"] for r in rows))
        fft = {title: field for title, field in {
            "Velocity RMS from FFT (≥10 Hz)": "velocity_rms_[xyz]_mm_s",
            "Spectral peaks vs expected roll harmonics": "peak[123]_[xyz]_hz",
            "Main peak amplitude": "peak1_[xyz]_g",
            "Main peak · last capture": "peak1_[xyz]_hz",
            "Capture settings · last": "(sample_rate_hz|averages)",
        }.items()}
        for title, field_pattern in fft.items():
            query = self.query(title)
            self.assertIn('"wtvb01_fft"', query, title)
            self.assertIn(field_pattern, query, title)
        # Peaks are drawn against the expected roll harmonics from the dashboard variables.
        self.assertIn("${line_speed}", self.query("Spectral peaks vs expected roll harmonics", "B"))
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


if __name__ == "__main__":
    unittest.main()
