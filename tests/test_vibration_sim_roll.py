import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.vibration import vibration_app as vapp
from app.vibration import vibration_config as cfg
from app.vibration import vibration_fft as vfft
from app.vibration import vibration_sim as sim
from app.vibration.vibration_bus import BusError, ModbusBus
from tests.test_vibration_sim import VirtualClock
from tests.vibration_fakes import FakeSensor, FakeSerial, opener_for

SIM_ADDRESS = 0x7F
HEALTHY_ADDRESS = 0x7E


class SpeedProfileTests(unittest.TestCase):
    def test_line_speed_alternates_with_five_second_ramps(self):
        speeds = [sim.roll_line_speed_m_min(t) for t in (0, 24.9, 27.5, 30, 54.9, 57.5, 60, 87.5)]
        self.assertEqual(speeds, [450, 450, 525, 600, 600, 525, 450, 525])

    def test_roll_frequency_matches_line_speed_and_diameter(self):
        self.assertAlmostEqual(sim.roll_hz(0), 450 / 60 / (math.pi * 0.6), places=6)
        self.assertAlmostEqual(sim.roll_hz(40), 600 / 60 / (math.pi * 0.6), places=6)

    def test_revolutions_integrate_the_speed_exactly(self):
        step = 0.001
        numeric = sum(sim.roll_line_speed_m_min(i * step) / 60 * step for i in range(130_000))
        self.assertAlmostEqual(sim.roll_revolutions(130), numeric / (math.pi * 0.6), places=2)


class RollSimulatorBusTests(unittest.TestCase):
    """The simulator as a virtual sensor next to a real sensor on the same bus."""

    def setUp(self):
        self.time = VirtualClock()
        self.real = FakeSensor(0x50, baud=230400)
        self.serial = FakeSerial([self.real], baudrate=230400)
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=self.time.sleep, clock=self.time.clock)
        self.bus.open("/dev/ttyFAKE", 230400)
        self.roll = sim.RollDriveSimulator(SIM_ADDRESS, 230400, clock=self.time.clock, sleep=self.time.sleep, seed=3)
        self.bus.virtual[SIM_ADDRESS] = self.roll

    def capture_at(self, elapsed):
        self.time.now = self.roll._started + elapsed
        capture = self.bus.capture_fifo(SIM_ADDRESS)
        return vfft.analyze(capture["samples"], capture["sample_rate"] or sim.SIM_SAMPLE_RATE)

    def test_frames_for_the_virtual_address_never_reach_the_line(self):
        self.assertTrue(self.bus.probe(SIM_ADDRESS))
        self.assertTrue(self.bus.probe(0x50))
        self.assertTrue(all(frame[0] != SIM_ADDRESS for frame in self.serial.written))

    def test_registers_follow_the_line_speed(self):
        self.time.now = self.roll._started + 10  # 450 m/min
        slow = self.bus.read_profile(SIM_ADDRESS, "basic")
        self.time.now = self.roll._started + 40  # 600 m/min
        fast = self.bus.read_profile(SIM_ADDRESS, "basic")
        self.assertGreater(fast["velocity_x_mm_s"], slow["velocity_x_mm_s"])
        self.assertAlmostEqual(fast["frequency_x_hz"], sim.roll_hz(40), delta=0.1)

    def test_spectrum_shows_unbalance_and_bearing_lines(self):
        result = self.capture_at(35)  # steady 600 m/min
        f1 = sim.roll_hz(35)
        peaks_x = [hz for hz, _ in result["axes"]["x"]["peaks"]]
        self.assertAlmostEqual(peaks_x[0], f1, delta=0.3)  # 1x unbalance is the strongest line
        # BPFO harmonics around the 72 Hz resonance stand out on Y (load direction).
        acc_y = result["axes"]["y"]["acc_g"]
        df = result["df_hz"]
        harmonic = round(72 / (sim.ROLL_BPFO_ORDER * f1))
        line = harmonic * sim.ROLL_BPFO_ORDER * f1
        k = round(line / df)
        between = round((line + sim.ROLL_BPFO_ORDER * f1 / 2) / df)
        self.assertGreater(max(acc_y[k - 2:k + 3]), 3 * max(acc_y[between - 2:between + 3]))

    def test_healthy_roll_has_small_1x_and_no_bearing_lines(self):
        healthy = sim.RollDriveSimulator(HEALTHY_ADDRESS, 230400, profile="healthy",
                                         clock=self.time.clock, sleep=self.time.sleep, seed=3)
        self.bus.virtual[HEALTHY_ADDRESS] = healthy
        self.time.now = healthy._started + 35
        capture = self.bus.capture_fifo(HEALTHY_ADDRESS)
        result = vfft.analyze(capture["samples"], capture["sample_rate"] or sim.SIM_SAMPLE_RATE)
        defective = self.capture_at(35)
        self.assertLess(result["axes"]["x"]["velocity_rms_mm_s"], defective["axes"]["x"]["velocity_rms_mm_s"] / 3)
        # No BPFO harmonics around the 72 Hz resonance: that band is only noise.
        band = [a for k, a in enumerate(result["axes"]["y"]["acc_g"]) if 55 <= k * result["df_hz"] <= 90]
        defect_band = [a for k, a in enumerate(defective["axes"]["y"]["acc_g"]) if 55 <= k * defective["df_hz"] <= 90]
        self.assertLess(max(band), max(defect_band) / 4)
        self.assertFalse(healthy.status()["bearing_defect"])

    def test_unbalance_grows_with_speed(self):
        slow = self.capture_at(5)["axes"]["x"]["velocity_rms_mm_s"]
        fast = self.capture_at(35)["axes"]["x"]["velocity_rms_mm_s"]
        self.assertGreater(fast, slow * 1.4)


class SimSensorApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.serial = FakeSerial([FakeSensor(0x50)])
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=lambda _s: None)
        patches = [
            patch.object(vapp, "CONFIG_PATH", Path(directory.name) / "config.json"),
            patch.object(vapp, "BUS", self.bus),
            patch.object(vapp, "CONFIG", cfg.default_config()),
            patch.object(vapp, "SENSOR_STATE", {}),
            patch.object(vapp, "SPECTRA", {}),
            patch.object(vapp, "FFT_NEXT", {}),
            patch.object(vapp, "WAVEFORMS", {}),
            patch.object(vapp, "OPERATION", dict(vapp.OPERATION, running=False)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = vapp.app.test_client()
        vapp._store_config({"port": "/dev/ttyFAKE", "baud": 9600, "sensors": [{"address": 0x50, "name": "pump-1"}]})

    def test_switching_on_registers_and_attaches_the_simulator(self):
        response = self.client.post("/api/sim-sensor", json={"active": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["sim_sensor"]["active"])
        addresses = [s["address"] for s in vapp._config_snapshot()["sensors"]]
        self.assertEqual(addresses, [0x50, vapp.SIM_SENSOR_ADDRESS])
        self.assertIsInstance(self.bus.virtual[vapp.SIM_SENSOR_ADDRESS], sim.RollDriveSimulator)
        status = {item["address"]: item for item in self.client.get("/api/status").get_json()["sim_sensors"]}
        self.assertTrue(status[vapp.SIM_SENSOR_ADDRESS]["active"])
        self.assertEqual(status[vapp.SIM_SENSOR_ADDRESS]["line_speed_m_min"], 450.0)
        self.assertEqual(status[vapp.SIM_SENSOR_ADDRESS]["profile"], "defects")

    def test_switching_off_removes_it_and_keeps_the_real_sensor(self):
        self.client.post("/api/sim-sensor", json={"active": True})
        self.assertEqual(self.client.post("/api/sim-sensor", json={"active": False}).status_code, 200)
        self.assertEqual([s["address"] for s in vapp._config_snapshot()["sensors"]], [0x50])
        self.assertEqual(self.bus.virtual, {})
        self.assertFalse(any(item["active"] for item in self.client.get("/api/status").get_json()["sim_sensors"]))

    def test_validation_and_conflicts(self):
        self.assertEqual(self.client.post("/api/sim-sensor", json={"active": "yes"}).status_code, 400)
        vapp._store_config({"port": "/dev/ttyFAKE", "sensors": [{"address": vapp.SIM_SENSOR_ADDRESS, "name": "real-240"}]})
        self.assertEqual(self.client.post("/api/sim-sensor", json={"active": True}).status_code, 409)

    def test_simulated_sensor_is_polled_next_to_the_real_one(self):
        self.client.post("/api/sim-sensor", json={"active": True})
        with patch.object(vapp, "_port_exists", lambda _path: True):
            vapp.poll_cycle(vapp._config_snapshot(), RecordingSink())
        self.assertTrue(vapp.SENSOR_STATE[vapp.SIM_SENSOR_ADDRESS]["online"])
        self.assertTrue(vapp.SENSOR_STATE[0x50]["online"])

    def test_switching_on_one_simulator_switches_the_other_off(self):
        self.client.post("/api/sim-sensor", json={"active": True})
        response = self.client.post("/api/sim-sensor", json={"address": HEALTHY_ADDRESS, "active": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["sim_sensor"]["profile"], "healthy")
        self.assertEqual([s["address"] for s in vapp._config_snapshot()["sensors"]], [0x50, HEALTHY_ADDRESS])
        self.assertEqual(list(self.bus.virtual), [HEALTHY_ADDRESS])
        self.assertEqual(self.client.post("/api/sim-sensor", json={"address": 5, "active": True}).status_code, 404)

    def test_first_version_name_is_still_recognised(self):
        vapp._store_config({"port": "/dev/ttyFAKE", "sensors": [{"address": 0x7F, "name": "sim-roll-600"}]})
        response = self.client.post("/api/sim-sensor", json={"address": HEALTHY_ADDRESS, "active": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s["name"] for s in vapp._config_snapshot()["sensors"]], ["sim-roll-600-healthy"])

    def test_address_change_is_refused(self):
        self.client.post("/api/sim-sensor", json={"active": True})
        response = self.client.post(f"/api/sensors/{vapp.SIM_SENSOR_ADDRESS}/address", json={"new_address": 3})
        self.assertEqual(response.status_code, 400)


class RecordingSink:
    def __init__(self):
        self.points = []
        self.last_error = None

    def write(self, sensor, values, observed, measurement="wtvb01"):
        self.points.append((sensor["name"], values))


if __name__ == "__main__":
    unittest.main()
