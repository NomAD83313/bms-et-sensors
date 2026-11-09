import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DASHBOARD_PATH = ROOT_DIR / "grafana" / "provisioning" / "dashboards" / "main_dash.json"


class GrafanaDashboardTests(unittest.TestCase):
    def _redlab_panel(self):
        dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        for panel in dashboard.get("panels", []):
            if panel.get("title") == "RedLab-TC Historical Data":
                return panel
        return None

    def _panel_by_title(self, title):
        dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        for panel in dashboard.get("panels", []):
            if panel.get("title") == title:
                return panel
        return None

    def test_redlab_legend_has_channel_names_only(self):
        panel = self._redlab_panel()

        self.assertIsNotNone(panel)
        legend = panel.get("options", {}).get("legend", {})
        self.assertEqual(legend.get("calcs"), [])
        self.assertEqual(legend.get("displayMode"), "list")
        self.assertTrue(legend.get("showLegend"))

    def test_redlab_query_uses_device_and_channel_as_series_name(self):
        dashboard = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
        queries = [
            target.get("query", "")
            for panel in dashboard.get("panels", [])
            for target in panel.get("targets", [])
            if 'r["_measurement"] == "redlab"' in target.get("query", "")
        ]

        self.assertTrue(queries)
        for query in queries:
            self.assertIn('r.device + " " + r.channel', query)
            self.assertIn('keep(columns: ["_time", "_value", "series"])', query)
            self.assertIn('pivot(rowKey: ["_time"], columnKey: ["series"], valueColumn: "_value")', query)

    def test_messkluppe_force_panel_uses_force_measurement(self):
        panel = self._panel_by_title("Messkluppe Force")

        self.assertIsNotNone(panel)
        query = panel["targets"][0]["query"]
        self.assertIn('r["_measurement"] == "messkluppe_sensor"', query)
        self.assertIn('r["_field"] == "force_x_raw"', query)
        self.assertIn('r["_field"] == "force_y_raw"', query)
        self.assertIn('r["_field"] == "force_z_raw"', query)

    def test_pyrometers_panel_uses_object_head_and_box_fields(self):
        panel = self._panel_by_title("Pyrometers Temperature")

        self.assertIsNotNone(panel)
        query = panel["targets"][0]["query"]
        self.assertIn('r["_field"] == "object_temperature_c"', query)
        self.assertIn('r["_field"] == "sensor_head_temperature_c"', query)
        self.assertIn('r["_field"] == "controller_box_temperature_c"', query)
        self.assertIn('"TObj"', query)
        self.assertIn('"THead"', query)
        self.assertIn('"TBox"', query)
        self.assertIn("serial_label", query)
        self.assertIn("r.serial", query)
        self.assertIn('r.serial_label + " " + r.channel', query)
        self.assertNotIn('r.device + " " + r.serial_label', query)
        self.assertIn('pivot(rowKey: ["_time"], columnKey: ["series"], valueColumn: "_value")', query)

if __name__ == "__main__":
    unittest.main()
