import unittest
from pathlib import Path

from app.graf.graf_views import VIEW_CONFIGS


class GrafViewsTests(unittest.TestCase):
    def test_vibration_panels_are_mapped_in_frontend(self):
        script = (Path(__file__).resolve().parents[1] / "app/graf/static/graf_app.js").read_text(encoding="utf-8")

        for chart_id, panel_key in {
            "c14": "vibration_acceleration",
            "c15": "vibration_velocity",
            "c16": "vibration_displacement",
            "c17": "vibration_frequency",
        }.items():
            self.assertIn(f"{chart_id}: toXY(data.panels.{panel_key} || [])", script)

    def test_vibration_page_includes_latest_orbits(self):
        template = (Path(__file__).resolve().parents[1] / "app/graf/templates/index.html").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "app/graf/static/graf_orbits.js").read_text(encoding="utf-8")

        self.assertIn('id="orbitSensors"', template)
        self.assertIn('id="orbitDemoToggle"', template)
        self.assertIn('id="orbitScale"', template)
        self.assertIn('id="orbitCustomScale"', template)
        self.assertIn('graf_orbits.js', template)
        self.assertIn('/vibration/api/sensors/${sensor.address}/waveform', script)
        self.assertIn('displacement_um', script)
        self.assertIn('[["x", "y"], ["x", "z"], ["y", "z"]]', script)
        self.assertIn('Demo drive · simulated unbalance', script)
        self.assertIn('Simulation only', script)
        self.assertIn('graf.vibration.orbitScale', script)
        self.assertIn('fixedOrbitRange()', script)

    def test_pyrometers_view_uses_friendly_titles(self):
        cfg = VIEW_CONFIGS["pyrometers"]

        self.assertEqual(cfg["title"], "Graf App Lite · Pyrometers")
        self.assertEqual(cfg["device"]["panel_title"], "Pyrometers")

    def test_matter_view_shows_environment_and_battery(self):
        cfg = VIEW_CONFIGS["matter"]

        self.assertEqual(cfg["title"], "Graf App Lite · Matter")
        self.assertTrue(cfg["show_matter"])
        self.assertTrue(cfg["show_matter_humidity"])
        self.assertTrue(cfg["show_matter_pressure"])
        self.assertTrue(cfg["show_matter_pm"])
        self.assertTrue(cfg["show_matter_battery"])

    def test_all_view_includes_messkluppe_panel(self):
        cfg = VIEW_CONFIGS["all"]

        self.assertTrue(cfg["show_messkluppe"])
        self.assertTrue(cfg["show_messkluppe_orientation"])
        self.assertTrue(cfg["show_messkluppe_battery"])
        self.assertTrue(cfg["show_messkluppe_temperatures"])
        self.assertTrue(cfg["show_matter_humidity"])
        self.assertTrue(cfg["show_matter_pressure"])
        self.assertTrue(cfg["show_matter_pm"])
        self.assertTrue(cfg["show_matter_battery"])

    def test_messkluppe_view_uses_force_orientation_and_battery_windows(self):
        cfg = VIEW_CONFIGS["messkluppe"]

        self.assertEqual(cfg["title"], "Graf App Lite · Messkluppe")
        self.assertTrue(cfg["show_messkluppe"])
        self.assertTrue(cfg["show_messkluppe_orientation"])
        self.assertTrue(cfg["show_messkluppe_battery"])
        self.assertTrue(cfg["show_messkluppe_temperatures"])

    def test_vibration_view_has_four_primary_charts(self):
        cfg = VIEW_CONFIGS["vibration"]

        self.assertEqual(cfg["title"], "Graf App Lite · Vibration")
        self.assertTrue(cfg["show_vibration_acceleration"])
        self.assertTrue(cfg["show_vibration_velocity"])
        self.assertTrue(cfg["show_vibration_displacement"])
        self.assertTrue(cfg["show_vibration_frequency"])


if __name__ == "__main__":
    unittest.main()
