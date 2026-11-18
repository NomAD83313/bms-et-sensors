import math
import unittest

from app.vibration import vibration_config as cfg
from app.vibration import vibration_fft as vfft
from app.vibration import wtvb01_protocol as proto
from app.vibration import vibration_sim as sim
from app.vibration.vibration_bus import ModbusBus


def nominal_hz(speed_m_min, diameter_mm):
    """Roll frequency of the simulated machine before slip."""
    return speed_m_min / (math.pi * diameter_mm / 1000.0) / 60.0


class VirtualClock:
    """Shared fake time: sleeping advances the clock, so a 4.5 s capture runs instantly."""

    def __init__(self):
        self.now = 1000.0

    def clock(self):
        self.now += 0.0005  # Every poll of the clock costs a little time, like real I/O.
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.0005)


class SimulatedSensorTests(unittest.TestCase):
    def setUp(self):
        self.time = VirtualClock()
        self.port = sim.SimulatedSerial("sim://roll?speed=600&diameter=600", 230400,
                                        clock=self.time.clock, sleep=self.time.sleep, seed=7)
        self.bus = ModbusBus(opener=lambda _p, _b: self.port, sleep=self.time.sleep, clock=self.time.clock)
        self.bus.open("sim://roll", 230400)

    def test_port_parameters(self):
        self.assertEqual((self.port.line_speed_m_min, self.port.roll_diameter_mm), (600.0, 600.0))
        nominal = nominal_hz(600, 600)
        self.assertAlmostEqual(nominal, 5.3052, places=3)
        self.assertAlmostEqual(self.port.roll_hz(0.0), nominal * sim.SLIP, places=6)

    def test_registers_answer_through_the_bus(self):
        self.assertTrue(self.bus.probe(sim.SIM_ADDRESS))
        values = self.bus.read_profile(sim.SIM_ADDRESS, "full")
        self.assertAlmostEqual(values["acc_z_g"], 1.0, places=3)
        self.assertGreater(values["velocity_x_mm_s"], 2.0)
        self.assertEqual(values["temperature_c"], 26.5)
        self.assertGreater(values["energy_x_total"], 0)

    def test_fifo_capture_timing_and_content(self):
        started = self.time.now
        capture = self.bus.capture_fifo(sim.SIM_ADDRESS)
        elapsed = self.time.now - started
        # 1024 samples at 246.3 Hz plus 0.27 s for 6149 bytes at 230400 baud.
        self.assertAlmostEqual(elapsed, 1024 / sim.SIM_SAMPLE_RATE + proto.FIFO_FRAME_LENGTH * 10 / 230400, delta=0.15)
        self.assertGreaterEqual(len(capture["progress"]), 15)
        self.assertAlmostEqual(capture["sample_rate"], sim.SIM_SAMPLE_RATE, delta=1.0)
        mean_z = sum(s[2] for s in capture["samples"]) / len(capture["samples"])
        self.assertAlmostEqual(mean_z, 1.0, places=2)  # Sign restored to the register convention.

    def test_spectrum_shows_roll_orders_and_the_foreign_line(self):
        capture = self.bus.capture_fifo(sim.SIM_ADDRESS)
        nominal = nominal_hz(600, 600)
        result = vfft.analyze(capture["samples"], capture["sample_rate"], velocity_min_hz=2.0, roll_hz=nominal)
        x = result["axes"]["x"]["orders"]
        self.assertAlmostEqual(x["1x"]["hz"], nominal * sim.SLIP, delta=0.1)
        self.assertAlmostEqual(x["1x"]["mm_s"], 2.5, delta=0.25)
        self.assertAlmostEqual(x["2x"]["mm_s"], 1.0, delta=0.15)
        self.assertAlmostEqual(result["axes"]["z"]["peaks"][0][0], 24.5, delta=0.2)
        # Velocity RMS from 2 Hz includes 1x and 2x; from 10 Hz it misses 1x.
        from_10 = vfft.analyze(capture["samples"], capture["sample_rate"], velocity_min_hz=10.0)
        self.assertGreater(result["axes"]["x"]["velocity_rms_mm_s"], 1.5 * from_10["axes"]["x"]["velocity_rms_mm_s"])

    def test_other_addresses_stay_silent(self):
        self.assertFalse(self.bus.probe(0x51))

    def test_writes_are_echoed(self):
        self.bus.write_register(sim.SIM_ADDRESS, proto.REG_UNLOCK, proto.UNLOCK_KEY)


class SimConfigTests(unittest.TestCase):
    def test_sim_port_is_accepted(self):
        self.assertEqual(cfg.validate({"port": "sim://roll?speed=600"})["port"], "sim://roll?speed=600")
        with self.assertRaises(ValueError):
            cfg.validate({"port": "tcp://x"})

    def test_no_machine_data_and_automatic_defaults(self):
        sensor = cfg.parse_sensor({"address": 0x50, "line_speed_m_min": 600, "roll_diameter_mm": 600})
        self.assertEqual(set(sensor), {"address", "name", "enabled"})  # Old machine fields are dropped.
        config = cfg.validate({})
        self.assertEqual(config["fft_velocity_min_hz"], 0.0)  # Automatic.
        self.assertEqual(cfg.roll_search_hz(config), (1.0, 25.0))  # 60-1500 rpm.
        self.assertEqual(cfg.validate({"fft_velocity_min_hz": 2})["fft_velocity_min_hz"], 2.0)
        with self.assertRaises(ValueError):
            cfg.validate({"fft_velocity_min_hz": 5})
        with self.assertRaises(ValueError):
            cfg.validate({"roll_min_rpm": 600, "roll_max_rpm": 900})  # Less than a factor of two.


class FaultScenarioTests(unittest.TestCase):
    PORT = "sim://roll?speed=600&diameter=600&scenario=faults&run=test-{}"

    def at(self, seconds, tag):
        """A bus on a scenario port whose scenario started `seconds` ago."""
        clock = VirtualClock()
        port = self.PORT.format(tag)
        sim._SCENARIO_START[port] = clock.now - seconds
        self.addCleanup(sim._SCENARIO_START.pop, port, None)
        serial = sim.SimulatedSerial(port, 230400, clock=clock.clock, sleep=clock.sleep, seed=3)
        bus = ModbusBus(opener=lambda _p, _b: serial, sleep=clock.sleep, clock=clock.clock)
        bus.open(port, 230400)
        return serial, bus

    def spectrum(self, bus):
        capture = bus.capture_fifo(sim.SIM_ADDRESS)
        roll = nominal_hz(600, 600)
        return vfft.analyze(capture["samples"], capture["sample_rate"], velocity_min_hz=2.0, roll_hz=roll)

    def test_stages_cover_thirty_minutes(self):
        self.assertEqual(sim.SCENARIO_LENGTH_SEC, 1800)
        names = [name for _s, _e, name in sim.FAULT_STAGES]
        self.assertEqual(names[1:7], ["unbalance", "misalignment", "speed 450 m/min", "looseness",
                                      "bearing impacts", "communication faults"])
        self.assertEqual(sim.stage_at("faults", 239)[0], "normal")
        self.assertEqual(sim.stage_at("faults", 540)[0], "misalignment")
        self.assertEqual(sim.stage_at("faults", 4000)[0], "normal (scenario finished)")
        self.assertEqual(sim.stage_at("steady", 300)[0], "steady")

    def test_unbalance_raises_1x(self):
        _serial, bus = self.at(535, "unbalance")
        x = self.spectrum(bus)["axes"]["x"]["orders"]
        self.assertGreater(x["1x"]["mm_s"], 6.0)

    def test_misalignment_brings_2x_to_the_1x_level(self):
        _serial, bus = self.at(835, "misalignment")
        y = self.spectrum(bus)["axes"]["y"]["orders"]
        self.assertGreater(y["2x"]["mm_s"] / y["1x"]["mm_s"], 0.9)

    def test_speed_drop_moves_the_lines_away_from_the_configured_orders(self):
        serial, bus = self.at(900, "speed")
        result = self.spectrum(bus)
        self.assertAlmostEqual(result["axes"]["x"]["peaks"][0][0], serial.roll_hz(900), delta=0.1)
        self.assertLess(result["axes"]["x"]["orders"]["1x"]["mm_s"], 0.5)  # 5.3 Hz window is empty now.
        self.assertAlmostEqual(result["axes"]["z"]["peaks"][0][0], 24.5, delta=0.2)  # Foreign line stays.

    def test_looseness_adds_half_orders(self):
        _serial, bus = self.at(1200, "looseness")
        result = self.spectrum(bus)
        frequencies = [f for axis in "xy" for f, _a in result["axes"][axis]["peaks"]]
        self.assertGreater(result["axes"]["x"]["orders"]["0_5x"]["mm_s"], 0.5)
        self.assertGreater(result["axes"]["x"]["bands_g"]["10_25"], 0.004)
        self.assertTrue(frequencies)

    def test_bearing_impacts_raise_high_bands_and_kurtosis_registers(self):
        _serial, quiet = self.at(100, "quiet")
        serial, bus = self.at(1500, "bearing")
        normal = self.spectrum(quiet)["axes"]["z"]["bands_g"]["50_100"]
        impacts = self.spectrum(bus)["axes"]["z"]["bands_g"]["50_100"]
        self.assertGreater(impacts, 5 * normal)
        self.assertGreater(bus.read_profile(sim.SIM_ADDRESS, "extended")["kurtosis_z"], 5)

    def test_communication_faults_corrupt_fifo_frames(self):
        _serial, bus = self.at(1650, "comm")
        errors = 0
        for _ in range(6):
            try:
                bus.capture_fifo(sim.SIM_ADDRESS)
            except Exception as exc:  # BusError with CRC mismatch.
                self.assertIn("CRC", str(exc))
                errors += 1
        self.assertGreater(errors, 0)
        self.assertLess(errors, 6)

    def test_scenario_status(self):
        port = self.PORT.format("status")
        sim._SCENARIO_START[port] = 100.0
        self.addCleanup(sim._SCENARIO_START.pop, port, None)
        status = sim.scenario_status(port, now=100.0 + 600)
        self.assertEqual((status["stage"], status["elapsed_s"], status["length_s"]), ("misalignment", 600, 1800))
        self.assertEqual(sim.scenario_status("sim://roll")["scenario"], "steady")



class RollDetectionTests(unittest.TestCase):
    """Automatic 1x detection on simulated captures of every scenario stage."""

    def capture(self, seconds, tag, seed=5):
        clock = VirtualClock()
        port = f"sim://roll?speed=600&diameter=600&scenario=faults&run=detect-{tag}-{seed}"
        sim._SCENARIO_START[port] = clock.now - seconds
        self.addCleanup(sim._SCENARIO_START.pop, port, None)
        serial = sim.SimulatedSerial(port, 230400, clock=clock.clock, sleep=clock.sleep, seed=seed)
        bus = ModbusBus(opener=lambda _p, _b: serial, sleep=clock.sleep, clock=clock.clock)
        bus.open(port, 230400)
        return serial, [bus.capture_fifo(sim.SIM_ADDRESS)["samples"] for _ in range(2)]

    def analyze(self, captures, manual=None):
        """Default search range 60-1500 rpm and automatic velocity limit, no machine data."""
        search = cfg.roll_search_hz(cfg.validate({}))
        roll = nominal_hz(manual, 600) if manual else None
        return vfft.analyze_average(captures, sim.SIM_SAMPLE_RATE, velocity_min_hz=None, roll_hz=roll, roll_search_hz=search)

    def test_rolls_of_different_size_and_speed_without_machine_data(self):
        for speed, diameter in ((600, 350), (600, 900), (200, 600), (1200, 400)):
            for tag, seconds in (("normal", 100), ("misalignment", 830), ("looseness", 1200)):
                with self.subTest(speed=speed, diameter=diameter, stage=tag):
                    clock = VirtualClock()
                    port = f"sim://roll?speed={speed}&diameter={diameter}&scenario=faults&run=size-{speed}-{diameter}-{tag}"
                    sim._SCENARIO_START[port] = clock.now - seconds
                    self.addCleanup(sim._SCENARIO_START.pop, port, None)
                    serial = sim.SimulatedSerial(port, 230400, clock=clock.clock, sleep=clock.sleep, seed=5)
                    bus = ModbusBus(opener=lambda _p, _b, s=serial: s, sleep=clock.sleep, clock=clock.clock)
                    bus.open(port, 230400)
                    result = self.analyze([bus.capture_fifo(sim.SIM_ADDRESS)["samples"] for _ in range(2)])
                    self.assertEqual(result["roll_source"], "auto")
                    self.assertAlmostEqual(result["roll_hz"], serial.roll_hz(seconds), delta=0.05)

    def test_detects_1x_in_every_stage(self):
        for tag, seconds in (("normal", 100), ("unbalance", 500), ("misalignment", 830), ("speed", 900),
                             ("looseness", 1200), ("bearing", 1500)):
            with self.subTest(stage=tag):
                serial, captures = self.capture(seconds, tag)
                result = self.analyze(captures)
                self.assertEqual(result["roll_source"], "auto")
                self.assertAlmostEqual(result["roll_hz"], serial.roll_hz(seconds), delta=0.03)
                self.assertGreaterEqual(result["roll_detection"]["confidence"], vfft.MIN_CONFIDENCE)

    def test_speed_change_keeps_orders_correct(self):
        _serial, captures = self.capture(900, "orders")
        result = self.analyze(captures)
        self.assertAlmostEqual(result["axes"]["x"]["orders"]["1x"]["mm_s"], 2.5, delta=0.3)
        self.assertEqual(result["summary"]["roll_source"], 2.0)

    def test_noise_only_falls_back(self):
        import random
        rng = random.Random(1)
        noise = [[(rng.gauss(0, 0.0003), rng.gauss(0, 0.0003), 1 + rng.gauss(0, 0.0003)) for _ in range(1024)]]
        self.assertEqual(self.analyze(noise, manual=600)["roll_source"], "manual")
        result = self.analyze(noise)
        self.assertEqual((result["roll_source"], result["roll_hz"]), ("none", None))
        self.assertEqual(result["axes"]["x"]["orders"], {})

    def test_velocity_limit_follows_the_detected_speed(self):
        self.assertEqual(vfft.velocity_min_for(5.3), 2.0)    # 318 rpm
        self.assertEqual(vfft.velocity_min_for(15.8), 10.0)  # 948 rpm
        self.assertEqual(vfft.velocity_min_for(None), 2.0)


if __name__ == "__main__":
    unittest.main()
