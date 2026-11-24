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
        self.assertIn('id="waveformSensors"', template)
        self.assertIn('Latest signed waveforms', template)
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
        self.assertIn('waveformSvg(wave, "acc_g"', script)
        self.assertIn('waveformSvg(wave, "velocity_mm_s"', script)
        self.assertIn('waveformSvg(wave, "displacement_um"', script)
        self.assertIn('data-wave-scale-mode=', script)
        self.assertIn('data-wave-fixed-max=', script)
        self.assertIn('data-wave-scale-reset=', script)
        self.assertIn('graf.vibration.waveScaleMode.', script)
        self.assertIn('graf.vibration.waveFixedMax.', script)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', script)
        self.assertIn('const xTicks = Array.from({ length: 6 }', script)
        self.assertIn('waveform-chart-head', script)

    def test_vibration_page_has_persistent_time_signal_recorder(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/graf/templates/index.html").read_text(encoding="utf-8")
        script = (root / "app/graf/static/graf_orbits.js").read_text(encoding="utf-8")

        for element_id in ("recordSensor", "recordAverages", "recordNote", "recordTimeSignal",
                           "recordsTableBody", "savedRecordFft", "recordAutoInterval",
                           "recordRetentionDays", "recordMaxCount", "recordAutoStart", "recordAutoStop",
                           "recordsSelectAll", "recordsDeleteSelected", "recordsSelectionStatus"):
            self.assertIn(f'id="{element_id}"', template)
        self.assertIn("1024 · hardware fixed", template)
        self.assertIn('/vibration/api/sensors/${address}/records', script)
        self.assertIn('fetch("/vibration/api/records"', script)
        self.assertIn('openExportModal(getExportConfig("vibrationRecord")', script)
        exports = (root / "app/graf/static/graf_exports.js").read_text(encoding="utf-8")
        self.assertIn("/vibration/api/records/${id}/download.csv", exports)
        self.assertIn("download.json", script)
        self.assertIn("function fftSvg", script)
        self.assertIn("spectrum.df_hz", script)
        self.assertIn("fft_save_records", script)
        self.assertIn("Start automatic recording", template)
        self.assertIn("function bindFftHover", script)
        self.assertIn("FFT bin", script)
        self.assertIn("X axis · green", script)
        self.assertIn("single-sided acceleration amplitude", script)
        self.assertIn("Signed acceleration with gravity/DC mean removed", script)
        self.assertIn("Signed velocity integrated from acceleration", script)
        self.assertIn("Signed displacement integrated above", script)
        self.assertIn("Captured ${escapeHtml(capturedLabel(record.captured_at))}", script)
        self.assertIn("Periodic FFT running every", script)
        self.assertIn("window.setInterval(() =>", script)
        self.assertIn("loadRecords();", script)
        # A new record is shown automatically without pinning the live panels.
        self.assertIn("if (!viewingRecord) await showRecord(latestRecordId, false, false)", script)
        self.assertIn('localStorage.getItem("graf.vibration.fftScaleMode")', script)
        self.assertIn('data-fft-scale-mode', script)
        self.assertIn('data-fft-fixed-max', script)
        self.assertIn('data-fft-scale-reset', script)
        self.assertIn('data-record-select', script)
        self.assertIn('method: "DELETE"', script)
        self.assertIn('body: JSON.stringify({ ids: itemIds })', script)

    def test_vibration_page_has_continuous_recording(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/graf/templates/index.html").read_text(encoding="utf-8")
        orbits = (root / "app/graf/static/graf_orbits.js").read_text(encoding="utf-8")
        view = (root / "app/graf/static/graf_vibration_burst.js").read_text(encoding="utf-8")

        for element_id in ("burstDuration", "burstNote", "burstStart", "burstStop", "burstStatus", "burstView", "orbitLiveButton"):
            self.assertIn(f'id="{element_id}"', template)
        # Trend, frequency and spectrogram sit in their own panel below the orbits.
        self.assertLess(template.index('id="orbitSensors"'), template.index('id="burstPanel"'))
        self.assertIn("function skeletonMore()", view)
        self.assertIn('data-burst-control="envScale"', view)
        self.assertIn("data-burst-env-reset", view)
        self.assertIn('min="10" max="600"', template)
        self.assertIn("graf_vibration_burst.js", template)
        self.assertIn("/vibration/api/sensors/${address}/burst", orbits)
        self.assertIn('"/vibration/api/operation/stop"', orbits)
        self.assertIn("window.grafBurst.show(record, pin)", orbits)
        self.assertIn('state.spectrum = "capture";', view)
        self.assertIn("window.grafVibration = {", orbits)
        self.assertIn("/burst?window=${state.window}", view)
        self.assertIn("/captures/${state.selected}", view)
        self.assertIn('data-burst-chart="timeline"', view)
        self.assertIn('data-burst-chart="trend"', view)
        self.assertIn("data-burst-chart='spectrogram'", view)
        self.assertIn('data-burst-chart="frequency"', view)

    def test_simulated_sensor_is_switched_by_the_sensor_selector(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/graf/templates/index.html").read_text(encoding="utf-8")
        orbits = (root / "app/graf/static/graf_orbits.js").read_text(encoding="utf-8")
        self.assertIn('id="recordSensorInfo"', template)
        self.assertIn('fetch("/vibration/api/sim-sensor"', orbits)
        self.assertIn("payload.sim_sensors", orbits)
        self.assertIn("(off)", orbits)

    def test_vibration_charts_have_mouse_over_tooltips(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/graf/templates/index.html").read_text(encoding="utf-8")
        hover = (root / "app/graf/static/graf_hover.js").read_text(encoding="utf-8")
        orbits = (root / "app/graf/static/graf_orbits.js").read_text(encoding="utf-8")
        view = (root / "app/graf/static/graf_vibration_burst.js").read_text(encoding="utf-8")

        self.assertLess(template.index("graf_hover.js"), template.index("graf_orbits.js"))
        self.assertIn("window.grafHover = {", hover)
        for chart in ("wave", "orbit"):
            self.assertIn(f'data-hover-chart="{chart}"', orbits)
            self.assertIn(f'hover.register("{chart}"', orbits)
        for chart in ("burst-timeline", "burst-trend", "burst-frequency", "burst-spectrogram"):
            self.assertIn(f'data-hover-chart="{chart}"', view)
            self.assertIn(f'hover.register("{chart}"', view)
        collector = (root / "app/vibration/ui.js").read_text(encoding="utf-8")
        for chart in ("spectrum", "waveform", "orbit"):
            self.assertIn(f'data-hover="{chart}"', collector)

    def test_vibration_ui_periodic_timer_controls_record_persistence(self):
        script = (Path(__file__).resolve().parents[1] / "app/vibration/ui.js").read_text(encoding="utf-8")

        self.assertIn("fft_periodic: true, fft_save_records: true", script)
        self.assertIn("fft_periodic: false, fft_save_records: false", script)

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
