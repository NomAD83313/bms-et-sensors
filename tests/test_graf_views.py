import unittest

from app.graf.graf_views import VIEW_CONFIGS


class GrafViewsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
