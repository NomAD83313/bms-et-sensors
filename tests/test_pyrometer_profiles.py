import json
import tempfile
import unittest
from pathlib import Path

from app.pyrometers.pyrometer_profiles import build_device_profiles, preferred_device_id, summarize_devices_status


class PyrometerProfilesTests(unittest.TestCase):
    def test_shared_install_template_maps_lab_devices_by_usb_serial(self):
        template = Path(__file__).resolve().parents[1] / "config" / "pyrometers-devices.example.json"

        profiles = build_device_profiles({"PYROMETERS_REGISTRY": str(template)})

        self.assertEqual(
            [(profile.id, profile.serial, profile.sensor_serial, profile.default_port) for profile in profiles],
            [
                ("microeps1", "CT-00004944", "6060099", "/dev/ttyMICROEPS1"),
                ("optris1", "CT-00004971", "6070401", "/dev/ttyOPTRIS1"),
            ],
        )

    def test_build_device_profiles_reads_stream_options_from_registry(self):
        entries = [
            {
                "serial": "CT0001",
                "type": "optris",
                "id": "optris1",
                "symlink": "ttyOPTRIS1",
                "display_name": "Optris CT",
                "sensor_serial": "6070401",
                "mode": "burst",
                "stream_frame_format": "burst_words",
                "burst_interval_ms": 50,
                "burst_command_set": "classic_ct",
                "checksum_enabled": False,
                "burst_channels": ["target_act", "internal", "box", "target_act"],
            }
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump(entries, handle)
            handle.flush()

            profiles = build_device_profiles({"PYROMETERS_REGISTRY": handle.name})

        self.assertEqual([profile.id for profile in profiles], ["optris1"])
        self.assertEqual(profiles[0].mode, "burst")
        self.assertTrue(profiles[0].burst_mode)
        self.assertFalse(profiles[0].poll_mode)
        self.assertEqual(profiles[0].stream_frame_format, "burst_words")
        self.assertEqual(profiles[0].burst_interval_ms, 50)
        self.assertEqual(profiles[0].burst_command_set, "classic_ct")
        self.assertFalse(profiles[0].checksum_enabled)
        self.assertEqual(profiles[0].burst_channels, ("target_act", "internal", "box", "target_act"))
        self.assertEqual(profiles[0].serial, "CT0001")
        self.assertEqual(profiles[0].sensor_serial, "6070401")

    def test_summarize_devices_status_prefers_connected(self):
        self.assertEqual(
            summarize_devices_status(
                {
                    "microeps": {"connected": False, "port_present": False, "status": "missing"},
                    "optris": {"connected": True, "port_present": True, "status": "ok"},
                }
            ),
            "ok",
        )

    def test_summarize_devices_status_marks_present_without_connection_as_degraded(self):
        self.assertEqual(
            summarize_devices_status(
                {
                    "microeps": {"connected": False, "port_present": True, "status": "degraded"},
                    "optris": {"connected": False, "port_present": False, "status": "missing"},
                }
            ),
            "degraded",
        )

    def test_preferred_device_id_prefers_connected_then_present(self):
        devices = {
            "microeps": {"id": "microeps", "connected": False, "port_present": True},
            "optris": {"id": "optris", "connected": True, "port_present": True},
        }
        self.assertEqual(preferred_device_id(devices), "optris")


if __name__ == "__main__":
    unittest.main()
