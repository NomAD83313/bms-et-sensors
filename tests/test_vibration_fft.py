import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.vibration import vibration_app as vapp
from app.vibration import vibration_config as cfg
from app.vibration import vibration_fft as vfft
from app.vibration import wtvb01_protocol as proto
from app.vibration.vibration_bus import BusError, ModbusBus
from tests.vibration_fakes import FakeSensor, FakeSerial, fifo_data_frame, fifo_progress_frame, opener_for


REAL_PORT_EXISTS = vapp._port_exists


def sine(frequency, amplitude, rate, n=1024, offset=0.0):
    return [offset + amplitude * math.sin(2 * math.pi * frequency * i / rate) for i in range(n)]


class FifoProtocolTests(unittest.TestCase):
    def test_request_matches_witmotion_support(self):
        self.assertEqual(proto.fifo_request(0x50).hex(" "), "50 03 00 2c 00 01 48 42")

    def test_progress_count_is_bytes_including_header(self):
        # Observed sequence on a real sensor: 369, 741, 1113 ... = 3 + 6 * samples.
        self.assertEqual(proto.fifo_progress_samples(369), 61)
        self.assertEqual(proto.fifo_progress_samples(6069), 1011)

    def test_real_progress_frame_is_parsed(self):
        events, rest = proto.split_fifo_stream(bytes.fromhex("50 03 01 01 71 74 3c"), 0x50)
        self.assertEqual(events, [("progress", 61)])
        self.assertEqual(rest, b"")

    def test_stream_with_noise_progress_and_data(self):
        raw = [(28, 24, 1961)] * proto.FIFO_SAMPLES
        stream = b"\x00" + fifo_progress_frame(0x50, 61) + fifo_progress_frame(0x50, 123) + fifo_data_frame(0x50, raw)
        events, rest = proto.split_fifo_stream(stream, 0x50)
        self.assertEqual([kind for kind, _ in events], ["progress", "progress", "data"])
        samples = events[2][1]
        self.assertEqual(len(samples), proto.FIFO_SAMPLES)
        # The FIFO has the opposite sign to registers 0x34..0x36.
        self.assertAlmostEqual(samples[0][2], -1961 * 16 / 32768)
        self.assertEqual(rest, b"")

    def test_partial_data_frame_is_kept_for_the_next_chunk(self):
        frame = fifo_data_frame(0x50, [(1, 2, 3)] * proto.FIFO_SAMPLES)
        events, rest = proto.split_fifo_stream(frame[:4000], 0x50)
        self.assertEqual(events, [])
        events, rest = proto.split_fifo_stream(rest + frame[4000:], 0x50)
        self.assertEqual(events[0][0], "data")

    def test_corrupted_data_frame_raises(self):
        frame = bytearray(fifo_data_frame(0x50, [(1, 2, 3)] * proto.FIFO_SAMPLES))
        frame[500] ^= 0xFF
        with self.assertRaises(proto.ProtocolError):
            proto.split_fifo_stream(bytes(frame), 0x50)

    def test_corrupted_progress_frame_is_skipped(self):
        bad = bytearray(fifo_progress_frame(0x50, 61))
        bad[4] ^= 0x01
        events, _ = proto.split_fifo_stream(bytes(bad) + fifo_progress_frame(0x50, 123), 0x50)
        self.assertEqual(events, [("progress", 123)])

    def test_sample_rate_from_progress(self):
        progress = [(0.252 * i, round(246.0 * 0.252 * i)) for i in range(1, 17)]
        self.assertAlmostEqual(proto.estimate_sample_rate(progress), 246.0, delta=0.5)

    def test_sample_rate_needs_three_points_and_a_plausible_value(self):
        self.assertIsNone(proto.estimate_sample_rate([(0.25, 61), (0.5, 123)]))
        self.assertIsNone(proto.estimate_sample_rate([(0.0, 61), (0.0, 123), (0.0, 185)]))
        self.assertIsNone(proto.estimate_sample_rate([(1.0, 10), (2.0, 20), (3.0, 30)]))


class FftMathTests(unittest.TestCase):
    def test_fft_matches_direct_dft(self):
        values = [complex(math.sin(i), math.cos(3 * i)) for i in range(16)]
        direct = [sum(v * complex(math.cos(-2 * math.pi * k * i / 16), math.sin(-2 * math.pi * k * i / 16))
                      for i, v in enumerate(values)) for k in range(16)]
        for a, b in zip(vfft.fft(values), direct):
            self.assertAlmostEqual(abs(a - b), 0, places=9)

    def test_fft_rejects_non_power_of_two(self):
        with self.assertRaises(ValueError):
            vfft.fft([0j] * 12)

    def test_sine_peak_frequency_and_amplitude(self):
        rate = 246.0
        samples = [(0.0, 0.0, z) for z in sine(29.6, 0.05, rate, offset=-0.958)]
        result = vfft.analyze(samples, rate)
        frequency, amplitude = result["axes"]["z"]["peaks"][0]
        self.assertAlmostEqual(frequency, 29.6, delta=result["df_hz"] / 2)
        self.assertAlmostEqual(amplitude, 0.05, delta=0.05 * 0.16)  # Hann scalloping loss <= 1.42 dB.
        self.assertEqual(result["axes"]["x"]["peaks"], [])

    def test_gravity_offset_is_removed(self):
        rate = 246.0
        result = vfft.analyze([(0.0, 0.0, -0.958)] * 1024, rate)
        self.assertLess(max(result["axes"]["z"]["acc_g"]), 1e-9)

    def test_band_rms_of_sine_is_amplitude_over_sqrt2(self):
        rate = 246.0
        df = rate / 1024
        samples = [(x, 0.0, 0.0) for x in sine(256 * df, 0.1, rate)]  # 61.5 Hz, exactly on a bin.
        bands = vfft.analyze(samples, rate)["axes"]["x"]["bands_g"]
        self.assertAlmostEqual(bands["50_100"], 0.1 / math.sqrt(2), delta=0.002)
        self.assertLess(bands["2_10"], 0.001)

    def test_velocity_of_sine(self):
        rate = 246.0
        df = rate / 1024
        frequency = 100 * df
        amplitude_g = 0.1
        samples = [(0.0, y, 0.0) for y in sine(frequency, amplitude_g, rate)]
        axis = vfft.analyze(samples, rate)["axes"]["y"]
        expected_peak = amplitude_g * vfft.STANDARD_GRAVITY_MM_S2 / (2 * math.pi * frequency)
        self.assertAlmostEqual(max(axis["velocity_mm_s"]), expected_peak, delta=expected_peak * 0.01)
        self.assertAlmostEqual(axis["velocity_rms_mm_s"], expected_peak / math.sqrt(2), delta=expected_peak * 0.02)

    def test_three_strongest_separated_peaks(self):
        rate = 246.0
        signal = [a + b + c + d for a, b, c, d in zip(sine(12.5, 0.02, rate), sine(25.0, 0.05, rate),
                                                      sine(50.0, 0.03, rate), sine(75.0, 0.01, rate))]
        peaks = vfft.analyze([(s, 0.0, 0.0) for s in signal], rate)["axes"]["x"]["peaks"]
        self.assertEqual([round(f) for f, _ in peaks], [25, 50, 12])

    def test_average_keeps_steady_line_and_smooths_noise(self):
        import random
        rng = random.Random(1)
        rate = 246.0
        captures = []
        for _ in range(8):
            phase = rng.uniform(0, 2 * math.pi)
            captures.append([(0.05 * math.sin(2 * math.pi * 40.0 * i / rate + phase) + rng.gauss(0, 0.01), 0.0, 0.0)
                             for i in range(1024)])
        single = vfft.analyze(captures[0], rate)["axes"]["x"]
        averaged = vfft.analyze_average(captures, rate)
        axis = averaged["axes"]["x"]
        self.assertEqual(averaged["averages"], 8)
        self.assertEqual(averaged["summary"]["averages"], 8.0)
        # The steady line keeps its amplitude regardless of the phase of each capture.
        self.assertAlmostEqual(axis["peaks"][0][0], 40.0, delta=0.12)
        self.assertAlmostEqual(axis["peaks"][0][1], single["peaks"][0][1], delta=0.008)

        def spread(values):
            noise = [v for k, v in enumerate(values) if abs(k * rate / 1024 - 40.0) > 3 and k * rate / 1024 > 5]
            mean = sum(noise) / len(noise)
            return math.sqrt(sum((v - mean) ** 2 for v in noise) / len(noise))

        # Noise floor fluctuation drops by about sqrt(8).
        self.assertLess(spread(axis["acc_g"]), spread(single["acc_g"]) / 2)

    def test_average_rejects_mismatched_captures(self):
        with self.assertRaises(ValueError):
            vfft.analyze_average([], 246.0)
        with self.assertRaises(ValueError):
            vfft.analyze_average([[(0.0, 0.0, 0.0)] * 1024, [(0.0, 0.0, 0.0)] * 512], 246.0)

    def test_waveform_integration_and_orbit_shape(self):
        rate, f1 = 256.0, 5.0
        to_g = lambda velocity: velocity * 2 * math.pi * f1 / vfft.STANDARD_GRAVITY_MM_S2
        samples = [(to_g(2.0) * math.sin(2 * math.pi * f1 * i / rate),
                    to_g(1.0) * math.sin(2 * math.pi * f1 * i / rate - math.pi / 2),
                    1.0 + to_g(1.5) * math.sin(2 * math.pi * f1 * i / rate)) for i in range(1024)]
        wave = vfft.waveforms(samples, rate)
        x = wave["axes"]["x"]
        self.assertAlmostEqual(max(x["velocity_mm_s"][100:900]), 2.0, delta=0.15)
        self.assertAlmostEqual(max(x["displacement_um"][100:900]), 2.0 / (2 * math.pi * f1) * 1000, delta=4)
        self.assertAlmostEqual(wave["orbits"]["xy"]["overall"]["ratio"], 0.5, delta=0.05)
        self.assertLess(wave["orbits"]["xz"]["overall"]["ratio"], 0.05)
        self.assertEqual(wave["integration_min_hz"], 2.0)

    def test_waveform_removes_gravity(self):
        rate = 246.0
        samples = [(0.01 * math.sin(2 * math.pi * 30 * i / rate), 0.0, 1.0) for i in range(1024)]
        wave = vfft.waveforms(samples, rate)
        self.assertEqual(wave["integration_min_hz"], vfft.WAVEFORM_MIN_HZ)
        self.assertNotIn("1x", wave["orbits"]["xy"])
        self.assertAlmostEqual(sum(wave["axes"]["z"]["acc_g"]), 0.0, places=6)  # Gravity removed.

    def test_summary_fields_for_influx(self):
        rate = 246.0
        summary = vfft.analyze([(x, 0.0, 0.0) for x in sine(30.0, 0.05, rate)], rate)["summary"]
        self.assertEqual(summary["sample_rate_hz"], 246.0)
        self.assertAlmostEqual(summary["peak1_x_hz"], 30.0, delta=0.12)
        self.assertIn("band_x_100_nyq_g", summary)
        self.assertIn("velocity_rms_z_mm_s", summary)
        self.assertTrue(all(isinstance(v, float) for v in summary.values()))


class FifoBusTests(unittest.TestCase):
    def setUp(self):
        self.sensor = FakeSensor(0x50, baud=230400)
        self.serial = FakeSerial([self.sensor], baudrate=230400)
        ticks = iter(i * 0.25 for i in range(10_000))
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=lambda _s: None, clock=lambda: next(ticks))
        self.bus.open("/dev/ttyFAKE", 230400)

    def test_capture_returns_samples_in_g(self):
        self.sensor.fifo = [(28, 24, 1961)] * proto.FIFO_SAMPLES
        capture = self.bus.capture_fifo(0x50)
        self.assertEqual(self.serial.written[-1], proto.fifo_request(0x50))
        self.assertEqual(len(capture["samples"]), proto.FIFO_SAMPLES)
        self.assertAlmostEqual(capture["samples"][0][2], -1961 / 2048)
        self.assertEqual([n for _, n in capture["progress"]], [62, 124, 186])

    def test_corrupted_frame_is_bus_error(self):
        self.sensor.fifo_corrupt = True
        with self.assertRaisesRegex(BusError, "CRC"):
            self.bus.capture_fifo(0x50)

    def test_capture_times_out_with_progress_state(self):
        self.sensor.fifo = None
        with self.assertRaisesRegex(BusError, "timed out .186 of 1024"):
            self.bus.capture_fifo(0x50)

    def test_closed_bus_raises(self):
        self.bus.close()
        with self.assertRaises(BusError):
            self.bus.capture_fifo(0x50)


class RecordingSink:
    def __init__(self):
        self.points = []
        self.last_error = None

    def write(self, sensor, values, observed, measurement="wtvb01"):
        self.points.append((measurement, sensor["name"], values))


class FftAppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sensor = FakeSensor(0x50)
        rate = proto.FIFO_DEFAULT_SAMPLE_RATE
        self.sensor.fifo = [(0, 0, round(-1961 + 100 * math.sin(2 * math.pi * 30 * i / rate)))
                            for i in range(proto.FIFO_SAMPLES)]
        self.serial = FakeSerial([self.sensor])
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=lambda _s: None)
        self.sink = RecordingSink()
        patches = [
            patch.object(vapp, "CONFIG_PATH", Path(directory.name) / "config.json"),
            patch.object(vapp, "BUS", self.bus),
            patch.object(vapp, "INFLUX", self.sink),
            patch.object(vapp, "CONFIG", cfg.default_config()),
            patch.object(vapp, "SENSOR_STATE", {}),
            patch.object(vapp, "SPECTRA", {}),
            patch.object(vapp, "FFT_NEXT", {}),
            patch.object(vapp, "WAVEFORMS", {}),
            patch.object(vapp, "OPERATION", dict(vapp.OPERATION, running=False)),
            patch.object(vapp, "_port_exists", lambda _path: True),
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
        deadline = time.monotonic() + 15
        while vapp.OPERATION["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(vapp.OPERATION["running"])
        return vapp.OPERATION

    def test_capture_endpoint_stores_spectrum_and_writes_summary(self):
        self.configure()
        response = self.client.post("/api/sensors/80/spectrum")
        self.assertEqual(response.status_code, 202)
        operation = self.wait_operation()
        self.assertIsNone(operation["error"])
        spectrum = self.client.get("/api/sensors/80/spectrum").get_json()
        self.assertEqual(spectrum["samples"], 1024)
        self.assertEqual(len(spectrum["axes"]["z"]["acc_g"]), 513)
        self.assertAlmostEqual(spectrum["axes"]["z"]["peaks"][0]["hz"], 30.0, delta=0.15)
        # Fake progress has no real timing, so the default rate is used and reported.
        self.assertFalse(spectrum["sample_rate_measured"])
        measurement, name, values = self.sink.points[-1]
        self.assertEqual((measurement, name), ("wtvb01_fft", "pump-1"))
        self.assertAlmostEqual(values["peak1_z_hz"], 30.0, delta=0.15)
        brief = self.client.get("/api/status").get_json()["sensors"][0]["fft"]
        self.assertAlmostEqual(brief["peaks"]["z"][0]["hz"], 30.0, delta=0.15)
        self.assertNotIn("acc_g", str(brief))

    def test_capture_averages_several_captures(self):
        self.configure(fft_averages=2)
        self.client.post("/api/sensors/80/spectrum", json={"averages": 3})
        self.assertIsNone(self.wait_operation()["error"])
        self.assertEqual(self.sensor.fifo_requests, 3)
        self.assertEqual(self.client.get("/api/sensors/80/spectrum").get_json()["averages"], 3)
        self.assertEqual(self.sink.points[-1][2]["averages"], 3.0)
        # Without an override the configured value is used.
        self.client.post("/api/sensors/80/spectrum")
        self.wait_operation()
        self.assertEqual(self.sensor.fifo_requests, 5)
        self.assertEqual(self.client.get("/api/status").get_json()["sensors"][0]["fft"]["averages"], 2)

    def test_live_values_are_polled_between_captures(self):
        self.configure()
        vapp._fft_work(0x50, averages=3)
        measurements = [m for m, _name, _values in self.sink.points]
        self.assertEqual(measurements, ["wtvb01", "wtvb01", "wtvb01_fft"])
        self.assertEqual(vapp._sensor_state(0x50)["reads"], 2)

    def test_single_capture_does_not_poll(self):
        self.configure()
        vapp._fft_work(0x50, averages=1)
        self.assertEqual([m for m, _n, _v in self.sink.points], ["wtvb01_fft"])

    def tone_fifo(self, frequency_hz, amplitudes_g=(0.004, 0.003, 0.002)):
        """Raw FIFO with three tones on X and gravity on Z."""
        import random
        rng = random.Random(4)
        rate = proto.FIFO_DEFAULT_SAMPLE_RATE
        samples = []
        for i in range(proto.FIFO_SAMPLES):
            x = sum(a * math.sin(2 * math.pi * frequency_hz * k * i / rate + k) for k, a in enumerate(amplitudes_g, start=1))
            samples.append((round(-(x + rng.gauss(0, 0.0003)) * 2048), 0, -1961))
        return samples

    def test_fft_reports_measured_frequencies_without_rotation_fields(self):
        self.configure()
        self.sensor.fifo = self.tone_fifo(15.0)
        vapp._fft_work(0x50)
        spectrum = self.client.get("/api/sensors/80/spectrum").get_json()
        self.assertEqual(spectrum["velocity_min_hz"], 2.0)
        self.assertEqual(sorted(round(p["hz"]) for p in spectrum["axes"]["x"]["peaks"]), [15, 30, 45])
        self.assertNotIn("orders", spectrum["axes"]["x"])
        summary = self.sink.points[-1][2]
        self.assertAlmostEqual(summary["peak1_x_hz"], 15.0, delta=0.06)
        self.assertFalse(any(key.startswith(("roll_", "order_", "phase_1x_", "orbit_1x_")) for key in summary))
        brief = self.client.get("/api/status").get_json()["sensors"][0]["fft"]
        self.assertEqual(brief["peaks"]["x"], spectrum["axes"]["x"]["peaks"])

    def test_api_accepts_explicit_velocity_rms_cutoff(self):
        self.configure()
        vapp.OPERATION["running"] = True  # FFT settings never touch the bus.
        self.assertEqual(self.client.post("/api/config", json={"fft_velocity_min_hz": 10}).status_code, 200)
        self.assertEqual(self.client.post("/api/config", json={"fft_velocity_min_hz": 5}).status_code, 400)
        vapp.OPERATION["running"] = False
        self.sensor.fifo = self.tone_fifo(5.0)
        vapp._fft_work(0x50)
        spectrum = self.client.get("/api/sensors/80/spectrum").get_json()
        self.assertEqual(spectrum["velocity_min_hz"], 10.0)

    def test_sim_port_is_listed_and_present(self):
        ports = self.client.get("/api/ports").get_json()["ports"]
        self.assertFalse(any(p["path"].startswith("sim://") for p in ports))  # Hidden by default.
        with patch.object(vapp, "SIM_PORTS_LISTED", True):
            ports = self.client.get("/api/ports").get_json()["ports"]
        self.assertTrue(any(p["path"].startswith("sim://") for p in ports))
        self.assertTrue(REAL_PORT_EXISTS("sim://frequency"))
        self.assertFalse(REAL_PORT_EXISTS("/dev/serial/by-id/not-there"))

    def test_capture_averages_validation(self):
        self.configure()
        self.assertEqual(self.client.post("/api/sensors/80/spectrum", json={"averages": 0}).status_code, 400)
        self.assertEqual(self.client.post("/api/sensors/80/spectrum", json={"averages": 17}).status_code, 400)
        self.assertEqual(self.client.post("/api/config", json={"fft_averages": 20}).status_code, 400)
        self.assertEqual(cfg.validate({})["fft_averages"], 1)

    def test_failed_capture_in_a_series_names_it(self):
        self.configure()
        self.sensor.fifo_corrupt = True
        self.client.post("/api/sensors/80/spectrum", json={"averages": 4})
        self.wait_operation()
        error = self.client.get("/api/status").get_json()["sensors"][0]["fft"]["error"]
        self.assertTrue(error.startswith("capture 1/4:"), error)

    def test_waveform_endpoint_and_orbit_summary(self):
        self.configure()
        self.assertEqual(self.client.get("/api/sensors/80/waveform").status_code, 404)
        self.sensor.fifo = self.tone_fifo(5.0)
        vapp._fft_work(0x50, averages=2)
        wave = self.client.get("/api/sensors/80/waveform").get_json()
        self.assertEqual(len(wave["axes"]["x"]["displacement_um"]), 1024)
        self.assertEqual(set(wave["axes"]["x"]), {"acc_g", "velocity_mm_s", "displacement_um"})
        self.assertEqual(set(wave["orbits"]["xy"]), {"overall"})
        summary = self.sink.points[-1][2]
        self.assertIn("orbit_xy_ratio", summary)
        spectrum = self.client.get("/api/sensors/80/spectrum").get_json()
        self.assertIn("xy", spectrum["orbits"])
        self.assertNotIn("displacement_um", str(self.client.get("/api/status").get_json()))

    def test_spectrum_before_capture_is_404(self):
        self.configure()
        self.assertEqual(self.client.get("/api/sensors/80/spectrum").status_code, 404)
        self.assertEqual(self.client.post("/api/sensors/81/spectrum").status_code, 404)

    def test_failed_capture_reports_error(self):
        self.configure()
        self.sensor.fifo_corrupt = True
        self.client.post("/api/sensors/80/spectrum")
        self.assertIn("CRC", self.wait_operation()["error"])
        self.assertIn("CRC", self.client.get("/api/status").get_json()["sensors"][0]["fft"]["error"])

    def test_fft_interval_validation(self):
        self.configure()
        self.assertEqual(self.client.post("/api/config", json={"fft_interval_sec": 4}).status_code, 400)
        self.assertEqual(self.client.post("/api/config", json={"fft_interval_sec": 5}).status_code, 200)
        self.assertEqual(self.client.post("/api/config", json={"fft_periodic": "yes"}).status_code, 400)
        response = self.client.post("/api/config", json={"fft_interval_sec": 600})
        config = response.get_json()["config"]
        self.assertEqual(config["fft_interval_sec"], 600.0)
        self.assertFalse(config["fft_periodic"])  # Setting the interval does not start it.
        defaults = cfg.validate({})
        self.assertEqual((defaults["fft_periodic"], defaults["fft_interval_sec"]), (False, 300.0))

    def test_start_and_stop_keep_the_interval(self):
        self.configure()
        started = self.client.post("/api/config", json={"fft_periodic": True, "fft_interval_sec": 120}).get_json()
        self.assertEqual((started["config"]["fft_periodic"], started["config"]["fft_interval_sec"]), (True, 120.0))
        stopped = self.client.post("/api/config", json={"fft_periodic": False}).get_json()
        self.assertEqual((stopped["config"]["fft_periodic"], stopped["config"]["fft_interval_sec"]), (False, 120.0))

    def test_fft_settings_change_while_an_operation_runs(self):
        self.configure(fft_periodic=True, fft_interval_sec=60)
        vapp.OPERATION["running"] = True
        stop = self.client.post("/api/config", json={"fft_periodic": False})
        self.assertEqual(stop.status_code, 200)
        self.assertFalse(stop.get_json()["config"]["fft_periodic"])
        # Bus settings still wait for the operation.
        self.assertEqual(self.client.post("/api/config", json={"fft_periodic": True, "baud": 9600}).status_code, 409)

    def test_stop_ends_a_periodic_series_between_captures(self):
        self.configure(fft_periodic=True, fft_interval_sec=60)
        original = self.bus.capture_fifo

        def capture_then_stop(address):
            result = original(address)
            vapp._store_config(dict(vapp._config_snapshot(), fft_periodic=False))
            return result

        with patch.object(self.bus, "capture_fifo", side_effect=capture_then_stop):
            result = vapp._fft_work(0x50, periodic=True, averages=4)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["averages"], 1)
        self.assertEqual(self.sensor.fifo_requests, 1)
        self.assertNotIn(0x50, vapp.FFT_NEXT)

    def test_next_periodic_capture_counts_from_the_end_of_the_series(self):
        self.configure(fft_periodic=True, fft_interval_sec=60)
        before = time.monotonic()
        vapp._fft_work(0x50, periodic=True, averages=2)
        self.assertGreaterEqual(vapp.FFT_NEXT[0x50], before + 60)

    def test_older_config_files_are_converted(self):
        self.assertEqual({k: cfg.validate({"fft_interval_sec": 0})[k] for k in ("fft_periodic", "fft_interval_sec")},
                         {"fft_periodic": False, "fft_interval_sec": 300.0})
        self.assertEqual({k: cfg.validate({"fft_interval_sec": 60})[k] for k in ("fft_periodic", "fft_interval_sec")},
                         {"fft_periodic": True, "fft_interval_sec": 60.0})

    def test_periodic_capture_is_due_only_for_online_sensors(self):
        config = self.configure(fft_periodic=True, fft_interval_sec=60)
        self.assertIsNone(vapp._fft_due(config, 100.0))
        vapp._sensor_state(0x50)["online"] = True
        self.assertEqual(vapp._fft_due(config, 100.0), 0x50)
        self.assertIsNone(vapp._fft_due(config, 130.0))
        self.assertEqual(vapp._fft_due(config, 160.0), 0x50)

    def test_status_reports_next_periodic_capture(self):
        self.configure()
        vapp._sensor_state(0x50)["online"] = True
        sensor = lambda: self.client.get("/api/status").get_json()["sensors"][0]
        self.assertIsNone(sensor()["fft_next_in_sec"])  # Periodic capture is off.
        self.client.post("/api/config", json={"fft_periodic": True, "fft_interval_sec": 300})
        self.assertEqual(sensor()["fft_next_in_sec"], 0.0)  # Online and not scheduled yet: due now.
        vapp.FFT_NEXT[0x50] = time.monotonic() + 120
        self.assertAlmostEqual(sensor()["fft_next_in_sec"], 120, delta=1)
        vapp._sensor_state(0x50)["online"] = False
        self.assertIsNone(sensor()["fft_next_in_sec"])

    def test_periodic_capture_off_by_default(self):
        config = self.configure()
        vapp._sensor_state(0x50)["online"] = True
        self.assertIsNone(vapp._fft_due(config, 100.0))


if __name__ == "__main__":
    unittest.main()
