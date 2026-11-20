import unittest

from app.vibration import vibration_config as cfg
from app.vibration import vibration_fft as vfft
from app.vibration import wtvb01_protocol as proto
from app.vibration import vibration_sim as sim
from app.vibration.vibration_bus import ModbusBus


def velocity_near(result, axis, frequency_hz):
    df = result["df_hz"]
    velocity = result["axes"][axis]["velocity_mm_s"]
    bin_index = round(frequency_hz / df)
    return max(velocity[bin_index - 1:bin_index + 2])


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
        self.port = sim.SimulatedSerial("sim://frequency?hz=5.25", 230400,
                                        clock=self.time.clock, sleep=self.time.sleep, seed=7)
        self.bus = ModbusBus(opener=lambda _p, _b: self.port, sleep=self.time.sleep, clock=self.time.clock)
        self.bus.open("sim://frequency", 230400)

    def test_port_parameters(self):
        self.assertEqual(self.port.base_frequency_hz, 5.25)
        self.assertAlmostEqual(self.port.signal_hz(0.0), 5.25)

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

    def test_spectrum_shows_reference_frequency_and_the_foreign_line(self):
        capture = self.bus.capture_fifo(sim.SIM_ADDRESS)
        result = vfft.analyze(capture["samples"], capture["sample_rate"])
        self.assertAlmostEqual(result["axes"]["x"]["peaks"][0][0], 5.25, delta=0.1)
        self.assertAlmostEqual(velocity_near(result, "x", 5.25), 2.5, delta=0.25)
        self.assertAlmostEqual(velocity_near(result, "x", 10.5), 1.0, delta=0.15)
        self.assertAlmostEqual(result["axes"]["z"]["peaks"][0][0], 24.5, delta=0.2)
        from_10 = vfft.analyze(capture["samples"], capture["sample_rate"], velocity_min_hz=10.0)
        self.assertGreater(result["axes"]["x"]["velocity_rms_mm_s"], 1.5 * from_10["axes"]["x"]["velocity_rms_mm_s"])

    def test_other_addresses_stay_silent(self):
        self.assertFalse(self.bus.probe(0x51))

    def test_writes_are_echoed(self):
        self.bus.write_register(sim.SIM_ADDRESS, proto.REG_UNLOCK, proto.UNLOCK_KEY)


class SimConfigTests(unittest.TestCase):
    def test_sim_port_is_accepted(self):
        self.assertEqual(cfg.validate({"port": "sim://frequency?hz=5.25"})["port"], "sim://frequency?hz=5.25")
        with self.assertRaises(ValueError):
            cfg.validate({"port": "tcp://x"})

    def test_fft_lower_limit_defaults_and_legacy_setting(self):
        self.assertEqual(cfg.validate({})["fft_velocity_min_hz"], 2.0)
        self.assertEqual(cfg.validate({"fft_velocity_min_hz": 0})["fft_velocity_min_hz"], 2.0)
        self.assertEqual(cfg.validate({"fft_velocity_min_hz": 10})["fft_velocity_min_hz"], 10.0)
        with self.assertRaises(ValueError):
            cfg.validate({"fft_velocity_min_hz": 5})


class FaultScenarioTests(unittest.TestCase):
    PORT = "sim://frequency?hz=5.25&scenario=faults&run=test-{}"

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
        return vfft.analyze(capture["samples"], capture["sample_rate"])

    def test_stages_cover_thirty_minutes(self):
        self.assertEqual(sim.SCENARIO_LENGTH_SEC, 1800)
        names = [name for _s, _e, name in sim.FAULT_STAGES]
        self.assertEqual(names[1:7], ["unbalance", "misalignment", "frequency drop", "looseness",
                                      "bearing impacts", "communication faults"])
        self.assertEqual(sim.stage_at("faults", 239)[0], "normal")
        self.assertEqual(sim.stage_at("faults", 540)[0], "misalignment")
        self.assertEqual(sim.stage_at("faults", 4000)[0], "normal (scenario finished)")
        self.assertEqual(sim.stage_at("steady", 300)[0], "steady")

    def test_unbalance_raises_the_reference_frequency_amplitude(self):
        serial, bus = self.at(535, "unbalance")
        result = self.spectrum(bus)
        self.assertGreater(velocity_near(result, "x", serial.signal_hz(535)), 6.0)

    def test_misalignment_raises_the_second_harmonic(self):
        serial, bus = self.at(835, "misalignment")
        result = self.spectrum(bus)
        frequency = serial.signal_hz(835)
        self.assertGreater(velocity_near(result, "y", 2 * frequency) / velocity_near(result, "y", frequency), 0.9)

    def test_frequency_drop_moves_the_peak_but_not_the_foreign_line(self):
        serial, bus = self.at(900, "frequency-drop")
        result = self.spectrum(bus)
        self.assertAlmostEqual(result["axes"]["x"]["peaks"][0][0], serial.signal_hz(900), delta=0.1)
        self.assertLess(velocity_near(result, "x", 5.25), 0.5)
        self.assertAlmostEqual(result["axes"]["z"]["peaks"][0][0], 24.5, delta=0.2)

    def test_looseness_increases_broadband_energy(self):
        serial, bus = self.at(1200, "looseness")
        result = self.spectrum(bus)
        self.assertGreater(velocity_near(result, "x", serial.signal_hz(1200) / 2), 0.5)
        self.assertGreater(result["axes"]["x"]["bands_g"]["10_25"], 0.004)

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
        self.assertEqual(sim.scenario_status("sim://frequency")["scenario"], "steady")

if __name__ == "__main__":
    unittest.main()
