import base64
import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.vibration import vibration_app as vapp
from app.vibration import vibration_burst as burst
from app.vibration import vibration_config as cfg
from app.vibration import vibration_records as records
from app.vibration import wtvb01_protocol as proto
from app.vibration.vibration_bus import ModbusBus
from tests.vibration_fakes import FakeSensor, FakeSerial, opener_for

RATE = proto.FIFO_DEFAULT_SAMPLE_RATE


def sine_capture(hz, amplitude_g=0.05, n=proto.FIFO_SAMPLES):
    return [(0.0, 0.0, 1.0 + amplitude_g * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n)]


class TimingTests(unittest.TestCase):
    def test_fit_returns_rate_and_first_sample_time(self):
        progress = [(0.1 + n / 246.0, n) for n in (60, 120, 180, 240)]
        rate, first = proto.estimate_fifo_timing(progress)
        self.assertAlmostEqual(rate, 246.0, places=6)
        self.assertAlmostEqual(first, 0.1, places=6)
        self.assertEqual(proto.estimate_sample_rate(progress), rate)

    def test_fit_needs_three_plausible_points(self):
        self.assertEqual(proto.estimate_fifo_timing([(0.5, 120), (1.0, 240)]), (None, None))
        self.assertEqual(proto.estimate_fifo_timing([(0.0, 1), (1.0, 2), (2.0, 3)]), (None, None))

    def test_capture_starts_use_request_time_plus_fitted_offset(self):
        captures = [{"requested_at": 100.0, "first_sample_s": 0.05},
                     {"requested_at": 104.5, "first_sample_s": 0.07},
                     {"requested_at": 109.0, "first_sample_s": None}]
        # The capture without a fit takes the median offset (0.07).
        self.assertEqual(burst.capture_starts(captures), [0.0, 4.52, 9.02])

    def test_summary_reports_span_coverage_and_gaps(self):
        info = burst.summary([0.0, 4.5, 9.0], 1024, 256.0)
        self.assertEqual(info["span_s"], 13.0)
        self.assertAlmostEqual(info["coverage"], 12 / 13, places=4)
        self.assertEqual((info["gap_mean_s"], info["gap_max_s"]), (0.5, 0.5))


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.captures = [sine_capture(30.0), sine_capture(60.0, amplitude_g=0.1)]
        self.starts = [0.0, 1024 / RATE + 0.3]

    def test_trends_follow_each_capture(self):
        result = burst.analyze(self.captures, self.starts, RATE)
        peaks = [c["axes"]["z"]["peak_hz"] for c in result["captures"]]
        self.assertAlmostEqual(peaks[0], 30.0, delta=0.1)
        self.assertAlmostEqual(peaks[1], 60.0, delta=0.1)
        rms = [c["axes"]["z"]["acc_rms_g"] for c in result["captures"]]
        self.assertAlmostEqual(rms[0], 0.05 / math.sqrt(2), delta=0.001)
        self.assertAlmostEqual(rms[1], 0.1 / math.sqrt(2), delta=0.001)
        self.assertEqual(result["gaps_s"], [0.3])
        strongest = result["captures"][1]["axes"]["z"]["peaks"][0]
        self.assertAlmostEqual(strongest[0], 60.0, delta=0.1)
        self.assertAlmostEqual(strongest[1], 0.1, delta=0.02)

    def test_peak_hold_keeps_both_lines(self):
        hold = burst.analyze(self.captures, self.starts, RATE)["peak_hold"]
        df = hold["df_hz"]
        z = hold["axes"]["z"]["acc_g"]
        self.assertAlmostEqual(z[round(30 / df)], 0.05, delta=0.01)
        self.assertAlmostEqual(z[round(60 / df)], 0.1, delta=0.02)

    def test_spectrogram_windows_stay_inside_captures(self):
        for window, per_capture in ((256, 7), (512, 3), (1024, 1)):
            spectrogram = burst.analyze(self.captures, self.starts, RATE, window=window)["spectrogram"]
            self.assertEqual(len(spectrogram["times_s"]), 2 * per_capture)
            self.assertEqual(spectrogram["bins"], window // 2 + 1)
            codes = base64.b64decode(spectrogram["axes"]["z"]["data"])
            self.assertEqual(len(codes), 2 * per_capture * spectrogram["bins"])
        first_column_end = 1024 / RATE
        times = burst.analyze(self.captures, self.starts, RATE)["spectrogram"]["times_s"]
        self.assertTrue(all(t < first_column_end for t in times[:7]))
        self.assertTrue(all(t > self.starts[1] for t in times[7:]))

    def test_envelope_leaves_the_gap_empty(self):
        result = burst.analyze(self.captures, self.starts, RATE, envelope_bins=200)
        envelope = result["envelope"]
        gap_bin = int((1024 / RATE + 0.15) / envelope["bin_s"])
        z = envelope["quantities"]["acc_g"]["z"]
        self.assertIsNone(z["max"][gap_bin])
        self.assertAlmostEqual(max(v for v in z["max"] if v is not None), 0.1, delta=0.005)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            burst.analyze(self.captures, self.starts, RATE, window=300)
        with self.assertRaises(ValueError):
            burst.analyze(self.captures, [0.0], RATE)
        with self.assertRaises(ValueError):
            burst.analyze([], [], RATE)


class RecordingSink:
    def __init__(self):
        self.points = []
        self.last_error = None

    def write(self, sensor, values, observed, measurement="wtvb01"):
        self.points.append((measurement, sensor["name"], values))


class BurstAppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sensor = FakeSensor(0x50)
        self.sensor.fifo = [(0, 0, round(-1961 + 100 * math.sin(2 * math.pi * 30 * i / RATE)))
                            for i in range(proto.FIFO_SAMPLES)]
        self.serial = FakeSerial([self.sensor])
        self.bus = ModbusBus(opener=opener_for(self.serial), sleep=lambda _s: None)
        self.sink = RecordingSink()
        self.records_dir = Path(directory.name) / "records"
        patches = [
            patch.object(vapp, "CONFIG_PATH", Path(directory.name) / "config.json"),
            patch.object(vapp, "RECORDS_DIR", self.records_dir),
            patch.object(vapp, "BUS", self.bus),
            patch.object(vapp, "INFLUX", self.sink),
            patch.object(vapp, "CONFIG", cfg.default_config()),
            patch.object(vapp, "SENSOR_STATE", {}),
            patch.object(vapp, "SPECTRA", {}),
            patch.object(vapp, "FFT_NEXT", {}),
            patch.object(vapp, "WAVEFORMS", {}),
            patch.object(vapp, "BURST_CACHE", {}),
            patch.object(vapp, "OPERATION", dict(vapp.OPERATION, running=False)),
            patch.object(vapp, "_port_exists", lambda _path: True),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        vapp.STOP_REQUESTED.clear()
        self.client = vapp.app.test_client()
        vapp._store_config({"port": "/dev/ttyFAKE", "baud": 9600, "poll_interval_sec": 0.5, "read_profile": "basic",
                            "sensors": [{"address": 0x50, "name": "pump-1"}]})

    @staticmethod
    def clock(*ticks):
        values = iter(ticks)
        return lambda: next(values)

    def test_record_runs_until_the_duration_and_saves_one_file(self):
        result = vapp._burst_work(0x50, 10, note="spin-up", clock=self.clock(0.0, 0.0, 4.5, 9.0, 13.5))
        self.assertEqual((result["averages"], result["stopped"], result["capture_error"]), (3, False, None))
        self.assertEqual(self.sensor.fifo_requests, 3)
        files = list(self.records_dir.glob("*.json.gz"))
        self.assertEqual(len(files), 1)
        with gzip.open(files[0], "rt") as handle:
            record = json.load(handle)
        self.assertEqual(record["kind"], "burst")
        self.assertEqual(record["requested_duration_s"], 10)
        self.assertEqual(len(record["raw_captures_g"]), 3)
        self.assertEqual(len(record["capture_starts_s"]), 3)
        self.assertEqual(record["capture_starts_s"][0], 0.0)
        # Wall-clock first-sample time of every capture, consistent with the relative starts.
        from datetime import datetime
        walls = [datetime.fromisoformat(value) for value in record["capture_started_at"]]
        self.assertEqual(len(walls), 3)
        self.assertTrue(walls[0] <= walls[1] <= walls[2])
        self.assertEqual(record["waveform"]["started_at"], record["capture_started_at"][-1])
        self.assertIn("span_s", record)
        # The series also refreshes the latest spectrum and writes one FFT summary.
        self.assertEqual(vapp.SPECTRA[0x50]["averages"], 3)
        self.assertEqual([m for m, _, _ in self.sink.points], ["wtvb01_fft"])
        listing = self.client.get("/api/records").get_json()["records"]
        self.assertEqual((listing[0]["kind"], listing[0]["capture_count"]), ("burst", 3))

    def test_polling_pauses_during_the_record(self):
        vapp._burst_work(0x50, 10, clock=self.clock(0.0, 0.0, 4.5, 9.0, 13.5))
        register_reads = [frame for frame in self.serial.written if frame != proto.fifo_request(0x50)]
        self.assertEqual(register_reads, [])

    def test_stop_keeps_the_captures_taken(self):
        original = self.bus.capture_fifo

        def capture_then_stop(address):
            result = original(address)
            vapp.STOP_REQUESTED.set()
            return result

        with patch.object(self.bus, "capture_fifo", side_effect=capture_then_stop):
            result = vapp._burst_work(0x50, 60, clock=self.clock(0.0, 0.0, 4.5))
        self.assertTrue(result["stopped"])
        self.assertEqual(result["averages"], 1)
        self.assertEqual(len(list(self.records_dir.glob("*.json.gz"))), 1)

    def test_failed_capture_ends_the_record_but_keeps_earlier_ones(self):
        original = self.bus.capture_fifo

        def capture_then_corrupt(address):
            result = original(address)
            self.sensor.fifo_corrupt = True
            return result

        with patch.object(self.bus, "capture_fifo", side_effect=capture_then_corrupt):
            result = vapp._burst_work(0x50, 60, clock=self.clock(0.0, 0.0, 4.5, 9.0))
        self.assertEqual(result["averages"], 1)
        self.assertIn("capture 2", result["capture_error"])

    def test_first_capture_failure_fails_the_operation(self):
        self.sensor.fifo_corrupt = True
        with self.assertRaises(vapp.BusError):
            vapp._burst_work(0x50, 10, clock=self.clock(0.0, 0.0))
        self.assertEqual(list(self.records_dir.glob("*.json.gz")), [])

    def test_api_validates_and_serves_analysis(self):
        self.assertEqual(self.client.post("/api/sensors/80/burst", json={"duration_s": 9}).status_code, 400)
        self.assertEqual(self.client.post("/api/sensors/80/burst", json={"duration_s": 601}).status_code, 400)
        self.assertEqual(self.client.post("/api/sensors/81/burst", json={"duration_s": 10}).status_code, 404)
        self.assertEqual(self.client.post("/api/operation/stop").status_code, 409)
        saved = vapp._burst_work(0x50, 10, clock=self.clock(0.0, 0.0, 4.5, 9.0, 13.5))["record"]
        analysis = self.client.get(f"/api/records/{saved['id']}/burst?window=512")
        self.assertEqual(analysis.status_code, 200)
        body = analysis.get_json()
        self.assertEqual((body["capture_count"], body["spectrogram"]["window"]), (3, 512))
        self.assertAlmostEqual(body["captures"][0]["axes"]["z"]["peak_hz"], 30.0, delta=0.2)
        self.assertEqual(self.client.get(f"/api/records/{saved['id']}/burst?window=300").status_code, 400)
        capture = self.client.get(f"/api/records/{saved['id']}/captures/2").get_json()
        self.assertEqual((capture["index"], capture["count"]), (2, 3))
        self.assertEqual(capture["waveform"]["started_at"],
                         self.client.get(f"/api/records/{saved['id']}").get_json()["capture_started_at"][2])
        self.assertEqual(len(capture["waveform"]["axes"]["x"]["acc_g"]), 1024)
        self.assertEqual(self.client.get(f"/api/records/{saved['id']}/captures/3").status_code, 404)
        csv_text = self.client.get(f"/api/records/{saved['id']}/download.csv?export_mode=raw_period").data.decode()
        self.assertTrue(csv_text.startswith("capture,sample,time_s,record_time_s,raw_acc_g_x"))
        self.assertEqual(len(csv_text.strip().splitlines()), 1 + 3 * 1024)

    def test_capture_time_of_older_continuous_records_comes_from_start_and_offsets(self):
        record = {"started_at": "2026-11-24T09:02:20.000+00:00", "capture_starts_s": [0.0, 4.455]}
        self.assertEqual(vapp._capture_start(record, 1), "2026-11-24T09:02:24.455+00:00")
        self.assertIsNone(vapp._capture_start({"captured_at": "2026-11-24T09:02:20+00:00"}, 0))
        stored = dict(record, capture_started_at=["2026-11-24T09:02:20.050+00:00", "2026-11-24T09:02:24.510+00:00"])
        self.assertEqual(vapp._capture_start(stored, 1), "2026-11-24T09:02:24.510+00:00")

    def test_ordinary_record_has_no_burst_analysis(self):
        saved = vapp._fft_work(0x50, averages=1, save_record=True)["record"]
        self.assertEqual(self.client.get(f"/api/records/{saved['id']}/burst").status_code, 400)
        self.assertEqual(self.client.get(f"/api/records/{saved['id']}/captures/0").status_code, 200)

    def test_stop_ends_an_averaged_series(self):
        original = self.bus.capture_fifo

        def capture_then_stop(address):
            result = original(address)
            vapp.STOP_REQUESTED.set()
            return result

        with patch.object(self.bus, "capture_fifo", side_effect=capture_then_stop):
            result = vapp._fft_work(0x50, averages=4)
        self.assertEqual((result["stopped"], result["averages"]), (True, 1))


class RecordListCacheTests(unittest.TestCase):
    def test_listing_follows_file_changes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            saved = records.save(root, {"sensor": "s", "captured_at": "2026-11-24T10:00:00+00:00", "note": "a"})
            self.assertEqual(records.list_records(root)[0]["note"], "a")
            records.save(root, {"sensor": "s", "captured_at": "2026-11-24T10:00:00+00:00", "note": "longer"})
            self.assertEqual(records.list_records(root)[0]["note"], "longer")
            records.delete(root, saved["id"])
            self.assertEqual(records.list_records(root), [])


if __name__ == "__main__":
    unittest.main()
