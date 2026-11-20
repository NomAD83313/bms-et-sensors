import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.vibration import vibration_app as vapp
from app.vibration import vibration_config as cfg
from app.vibration.vibration_bus import ModbusBus
from tests.vibration_fakes import FakeSensor, FakeSerial, opener_for


class RecordingSink:
    def __init__(self):
        self.points = []
        self.last_error = None

    def write(self, sensor, values, observed):
        self.points.append((sensor["name"], values))


class AppTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sensors = [FakeSensor(0x50, registers={0x40: 2310, 0x3A: 125})]
        self.serial = FakeSerial(self.sensors)
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=lambda _s: None)
        patches = [
            patch.object(vapp, "CONFIG_PATH", Path(directory.name) / "config.json"),
            patch.object(vapp, "BUS", self.bus),
            patch.object(vapp, "CONFIG", cfg.default_config()),
            patch.object(vapp, "SENSOR_STATE", {}),
            patch.object(vapp, "BUS_STATE", dict(vapp.BUS_STATE, cycles=0, open=False)),
            patch.object(vapp, "OPERATION", dict(vapp.OPERATION, running=False)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = vapp.app.test_client()

    def configure(self, **overrides):
        config = {"port": "/dev/ttyFAKE", "baud": 9600, "poll_interval_sec": 0.5, "read_profile": "basic",
                  "sensors": [{"address": 0x50, "name": "pump-1"}]}
        config.update(overrides)
        vapp._store_config(config)
        return vapp._config_snapshot()

    def wait_operation(self):
        deadline = time.monotonic() + 5
        while vapp.OPERATION["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(vapp.OPERATION["running"])
        return vapp.OPERATION


class PollTests(AppTestCase):
    def test_cycle_reads_sensor_and_writes_point(self):
        sink = RecordingSink()
        delay = vapp.poll_cycle(self.configure(), sink, port_exists=lambda _p: True)
        self.assertEqual(sink.points[0][0], "pump-1")
        self.assertEqual(sink.points[0][1]["temperature_c"], 23.1)
        self.assertGreater(delay, 0)
        payload = self.client.get("/health").get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["sensors_online"], 1)

    def test_missing_port_is_degraded_and_not_opened(self):
        vapp.poll_cycle(self.configure(), RecordingSink(), port_exists=lambda _p: False)
        self.assertFalse(self.bus.is_open)
        self.assertEqual(self.client.get("/health").get_json()["status"], "degraded")

    def test_unconfigured_service_is_idle(self):
        self.assertEqual(vapp.poll_cycle(vapp._config_snapshot(), RecordingSink()), 1.0)
        self.assertEqual(self.client.get("/health").get_json()["status"], "idle")

    def test_silent_sensor_goes_offline_and_backs_off(self):
        config = self.configure(sensors=[{"address": 0x10, "name": "absent"}])
        clock = [100.0]
        for _ in range(vapp.OFFLINE_AFTER_FAILURES):
            vapp.poll_cycle(config, RecordingSink(), clock=lambda: clock[0], port_exists=lambda _p: True)
        state = vapp.SENSOR_STATE[0x10]
        self.assertFalse(state["online"])
        written = len(self.serial.written)
        vapp.poll_cycle(config, RecordingSink(), clock=lambda: clock[0] + 1, port_exists=lambda _p: True)
        self.assertEqual(len(self.serial.written), written)  # Still backing off.
        vapp.poll_cycle(config, RecordingSink(), clock=lambda: clock[0] + vapp.OFFLINE_RETRY_SEC + 1,
                        port_exists=lambda _p: True)
        self.assertGreater(len(self.serial.written), written)

    def test_slow_blocks_are_read_only_when_the_sensor_refreshed_them(self):
        self.sensors[0].registers.update({0x70: 40, 0x6F: 10, 0x71: 60})  # x: 1x, 0.5x, 2x energy
        config = self.configure(read_profile="full")
        sink = RecordingSink()
        clock = [10.0]
        tick = lambda: clock[0]
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertEqual(sink.points[-1][1]["energy_x_ratio_2x_1x"], 1.5)
        self.assertEqual(len(self.serial.written), 3)
        clock[0] += 0.05  # Stats and energy not refreshed yet: basic block only.
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertEqual(len(self.serial.written), 4)
        self.assertNotIn("energy_x_point3", sink.points[-1][1])
        self.assertEqual(vapp.SENSOR_STATE[0x50]["values"]["energy_x_point3"], 40)  # UI keeps last value.
        clock[0] += 0.1  # Stats refreshed, energy still not.
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertEqual([f[3] for f in self.serial.written[4:]], [0x34, 0x47])
        clock[0] += 1.0
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertEqual([f[3] for f in self.serial.written[6:]], [0x34, 0x47, 0x6E])

    def test_invalid_stats_block_is_dropped_and_read_again(self):
        self.sensors[0].registers.update({0x47: 1500, 0x50: 1200})
        config = self.configure(read_profile="extended")
        sink = RecordingSink()
        clock = [10.0]
        tick = lambda: clock[0]
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.sensors[0].registers.update({0x47: 31000, 0x50: 0xFFFF})  # Transient: not computed.
        clock[0] += 0.2
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertNotIn("velocity_rms_x_mm_s", sink.points[-1][1])
        self.assertIn("velocity_x_mm_s", sink.points[-1][1])
        state = vapp.SENSOR_STATE[0x50]
        self.assertEqual(state["values"]["velocity_rms_x_mm_s"], 1.2)  # UI keeps the last valid value.
        self.assertEqual(state["rejected_blocks"], 1)
        self.sensors[0].registers[0x50] = 1300
        clock[0] += 0.01  # Rejected block is not throttled.
        vapp.poll_cycle(config, sink, clock=tick, port_exists=lambda _p: True)
        self.assertEqual(sink.points[-1][1]["velocity_rms_x_mm_s"], 1.3)

    def test_impossible_kurtosis_is_not_written(self):
        self.sensors[0].registers.update({0x48: 2500, 0x54: 0x10000 - 18512, 0x60: 1500, 0x5B: 1000})  # kurtosis x, y, z; skewness y
        sink = RecordingSink()
        vapp.poll_cycle(self.configure(read_profile="extended"), sink, port_exists=lambda _p: True)
        written = sink.points[-1][1]
        self.assertEqual(written["kurtosis_x"], 2.5)
        self.assertNotIn("kurtosis_y", written)
        self.assertNotIn("skewness_y", written)
        self.assertIn("velocity_rms_y_mm_s", written)
        self.assertEqual(vapp.SENSOR_STATE[0x50]["invalid_shape_axes"], 1)

    def test_disabled_sensor_is_not_polled(self):
        config = self.configure(sensors=[{"address": 0x50, "name": "pump-1", "enabled": False}])
        vapp.poll_cycle(config, RecordingSink(), port_exists=lambda _p: True)
        self.assertEqual(self.serial.written, [])


class RouteTests(AppTestCase):
    def test_sensor_crud(self):
        self.assertEqual(self.client.post("/api/sensors", json={"address": "0x50", "name": "pump-1"}).status_code, 200)
        self.assertEqual(self.client.post("/api/sensors", json={"address": 80}).status_code, 400)  # Duplicate.
        self.assertEqual(self.client.patch("/api/sensors/80", json={"enabled": False, "name": "fan"}).status_code, 200)
        sensor = self.client.get("/api/status").get_json()["sensors"][0]
        self.assertEqual((sensor["name"], sensor["enabled"]), ("fan", False))
        self.assertEqual(self.client.delete("/api/sensors/80").status_code, 200)
        self.assertEqual(self.client.delete("/api/sensors/80").status_code, 404)
        self.assertTrue(vapp.CONFIG_PATH.exists())

    def test_config_validation(self):
        self.assertEqual(self.client.post("/api/config", json={"baud": 1234}).status_code, 400)
        response = self.client.post("/api/config", json={"port": "/dev/ttyFAKE", "baud": 230400, "poll_interval_sec": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["config"]["baud"], 230400)

    def test_scan_runs_in_background(self):
        self.configure(sensors=[])
        response = self.client.post("/api/scan", json={"first": 0x4F, "last": 0x51})
        self.assertEqual(response.status_code, 202)
        operation = self.wait_operation()
        self.assertIsNone(operation["error"])
        self.assertEqual(operation["result"]["found"], [{"address": 0x50, "baud": 9600}])

    def test_scan_without_port_reports_error(self):
        self.client.post("/api/scan", json={})
        self.assertIn("serial port", self.wait_operation()["error"])

    def test_change_address_updates_registry(self):
        self.configure()
        self.assertEqual(self.client.post("/api/sensors/80/address", json={"new_address": 2}).status_code, 202)
        operation = self.wait_operation()
        self.assertIsNone(operation["error"])
        self.assertEqual(self.sensors[0].address, 2)
        self.assertEqual([s["address"] for s in vapp.CONFIG["sensors"]], [2])

    def test_change_baud_updates_port_setting(self):
        self.configure()
        self.assertEqual(self.client.post("/api/bus/baud", json={"baud": 230400}).status_code, 202)
        operation = self.wait_operation()
        self.assertIsNone(operation["error"])
        self.assertEqual((self.sensors[0].baud, vapp.CONFIG["baud"]), (230400, 230400))

    def test_operations_are_exclusive(self):
        self.configure()
        vapp.OPERATION["running"] = True
        self.assertEqual(self.client.post("/api/scan", json={}).status_code, 409)
        self.assertEqual(self.client.post("/api/sensors", json={"address": 3}).status_code, 409)

    def test_ui_assets_are_served(self):
        self.assertIn(b"Vibration sensors", self.client.get("/").data)
        self.assertEqual(self.client.get("/ui.js").status_code, 200)
        help_page = self.client.get("/help")
        self.assertEqual(help_page.status_code, 200)
        self.assertIn(b"Fault patterns", help_page.data)
        self.assertIn(b'href="help"', self.client.get("/").data)


if __name__ == "__main__":
    unittest.main()
