import importlib
import os
import sys
import types
import unittest
from unittest import mock
import threading


os.environ["ALMEMO_STARTUP_CONNECT"] = "0"

serial_stub = types.ModuleType("serial")
serial_stub.Serial = object
serial_stub.SerialException = Exception
serial_stub.EIGHTBITS = 8
serial_stub.PARITY_NONE = "N"
serial_stub.STOPBITS_ONE = 1
sys.modules.setdefault("serial", serial_stub)

influx_stub = types.ModuleType("influxdb_client")
influx_stub.InfluxDBClient = object
influx_stub.Point = object
influx_stub.WritePrecision = types.SimpleNamespace(MS="ms")
sys.modules.setdefault("influxdb_client", influx_stub)

write_api_stub = types.ModuleType("influxdb_client.client.write_api")
write_api_stub.SYNCHRONOUS = object()
sys.modules.setdefault("influxdb_client.client.write_api", write_api_stub)

almemo_app = importlib.import_module("app.almemo.almemo_app")


class AlmemoSerialHelpersTests(unittest.TestCase):
    def test_extract_device_version_accepts_2490_response(self):
        version = almemo_app._extract_device_version(["t0", "A2490-2 R02.12"])
        self.assertEqual(version, "A2490-2 R02.12")

    def test_extract_device_version_rejects_echo_and_noise(self):
        version = almemo_app._extract_device_version(["t0", "G00", "\x03", ""])
        self.assertIsNone(version)

    def test_candidate_ports_deduplicates_symlink_and_device(self):
        fake_candidates = ["/dev/serial/by-id/usb-Silicon_Labs_ALMEMO", "/dev/ttyUSB0", "/dev/ttyUSB0"]

        with mock.patch.object(almemo_app, "ALMEMO_PORT_ENV", ""):
            with mock.patch.object(almemo_app, "ALMEMO_EXCLUDE_PORTS", ""):
                with mock.patch.object(almemo_app, "_serial_candidates", return_value=fake_candidates):
                    with mock.patch(
                        "os.path.exists",
                        side_effect=lambda path: path in {"/dev/ttyUSB0", "/dev/serial/by-id/usb-Silicon_Labs_ALMEMO"},
                    ):
                        with mock.patch("os.path.realpath", side_effect=lambda path: "/dev/ttyUSB0"):
                            ports = almemo_app._candidate_ports()

        self.assertEqual(ports, ["/dev/serial/by-id/usb-Silicon_Labs_ALMEMO"])

    def test_candidate_ports_excludes_pyrometer_symlink_targets(self):
        fake_candidates = [
            "/dev/serial/by-id/usb-Silicon_Labs_ALMEMO",
            "/dev/serial/by-id/usb-Silicon_Labs_CP2102_pyrometer",
            "/dev/ttyUSB0",
            "/dev/ttyUSB1",
        ]

        def fake_exists(path):
            return path in {
                "/dev/serial/by-id/usb-Silicon_Labs_ALMEMO",
                "/dev/serial/by-id/usb-Silicon_Labs_CP2102_pyrometer",
                "/dev/ttyUSB0",
                "/dev/ttyUSB1",
                "/dev/ttyMICROEPS1",
            }

        def fake_realpath(path):
            return {
                "/dev/serial/by-id/usb-Silicon_Labs_ALMEMO": "/dev/ttyUSB0",
                "/dev/serial/by-id/usb-Silicon_Labs_CP2102_pyrometer": "/dev/ttyUSB1",
                "/dev/ttyMICROEPS1": "/dev/ttyUSB1",
            }.get(path, path)

        with mock.patch.object(almemo_app, "ALMEMO_PORT_ENV", ""):
            with mock.patch.object(almemo_app, "ALMEMO_EXCLUDE_PORTS", "/dev/ttyMICROEPS*"):
                with mock.patch.object(almemo_app.glob, "glob", return_value=["/dev/ttyMICROEPS1"]):
                    with mock.patch.object(almemo_app, "_serial_candidates", return_value=fake_candidates):
                        with mock.patch("os.path.exists", side_effect=fake_exists):
                            with mock.patch("os.path.realpath", side_effect=fake_realpath):
                                ports = almemo_app._candidate_ports()

        self.assertEqual(ports, ["/dev/serial/by-id/usb-Silicon_Labs_ALMEMO"])

    def test_candidate_ports_excludes_pyrometer_usb_ids(self):
        fake_candidates = ["/dev/serial/by-id/usb-Silicon_Labs_CP2102_pyrometer", "/dev/ttyUSB1"]

        with mock.patch.object(almemo_app, "ALMEMO_PORT_ENV", ""):
            with mock.patch.object(almemo_app, "ALMEMO_EXCLUDE_PORTS", ""):
                with mock.patch.object(almemo_app, "ALMEMO_EXCLUDE_USB_IDS", "10c4:834b"):
                    with mock.patch.object(almemo_app, "_serial_candidates", return_value=fake_candidates):
                        with mock.patch.object(almemo_app, "_serial_usb_ids", return_value=("10c4", "834b")):
                            with mock.patch("os.path.exists", side_effect=lambda path: path in set(fake_candidates)):
                                ports = almemo_app._candidate_ports()

        self.assertEqual(ports, [])

    def test_candidate_ports_does_not_prefer_generic_silicon_labs(self):
        fake_candidates = ["/dev/serial/by-id/usb-Silicon_Labs_CP2102_sensor", "/dev/ttyUSB1"]

        with mock.patch.object(almemo_app, "ALMEMO_PORT_ENV", ""):
            with mock.patch.object(almemo_app, "ALMEMO_EXCLUDE_PORTS", ""):
                with mock.patch.object(almemo_app, "_serial_candidates", return_value=fake_candidates):
                    with mock.patch("os.path.exists", side_effect=lambda path: path in set(fake_candidates)):
                        with mock.patch("os.path.realpath", side_effect=lambda path: path):
                            ports = almemo_app._candidate_ports()

        self.assertEqual(ports, fake_candidates)

    def test_open_serial_on_port_uses_exclusive_lock(self):
        with mock.patch.object(almemo_app.serial, "Serial", return_value=mock.Mock()) as serial_mock:
            almemo_app._open_serial_on_port("/dev/ttyUSB0", 800)

        self.assertTrue(serial_mock.call_args.kwargs["exclusive"])

    def test_open_verified_serial_keeps_successful_port_open(self):
        fake_ser = mock.Mock()

        with mock.patch.object(almemo_app, "_candidate_ports", return_value=["/dev/ttyUSB0"]):
            with mock.patch("os.path.exists", return_value=True):
                with mock.patch.object(almemo_app, "_open_serial_on_port", return_value=fake_ser):
                    with mock.patch.object(almemo_app, "_probe_device", return_value=("A2490-2 R02.12", ["ok"])):
                        ser, port = almemo_app._open_verified_serial(800)

        self.assertIs(ser, fake_ser)
        self.assertEqual(port, "/dev/ttyUSB0")
        fake_ser.close.assert_not_called()

    def test_health_does_not_report_ok_when_serial_lock_is_busy_without_recent_probe(self):
        fake_lock = mock.Mock()
        fake_lock.acquire.return_value = False
        client = almemo_app.app.test_client()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "LAST_DEVICE_OK_AT", 0.0):
                with mock.patch.object(almemo_app, "_live_is_running", return_value=False):
                    with mock.patch.object(almemo_app, "_fast_live_is_running", return_value=False):
                        with mock.patch.object(almemo_app, "_resolve_port", return_value="/dev/ttyUSB0"):
                            with mock.patch("os.path.exists", return_value=True):
                                response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "cable_only")

    def test_live_start_restarts_stale_running_stream(self):
        client = almemo_app.app.test_client()
        fake_thread = threading.Thread()

        with mock.patch.object(almemo_app, "LIVE_THREAD", fake_thread):
            with mock.patch.object(almemo_app, "LIVE_CYCLE", "000002"):
                with mock.patch.object(almemo_app, "_fast_live_is_running", return_value=False):
                    with mock.patch.object(almemo_app, "_live_is_running", return_value=True):
                        with mock.patch.object(almemo_app, "_live_data_fresh", return_value=False):
                            with mock.patch.object(almemo_app, "_stop_stream") as stop_stream:
                                with mock.patch.object(almemo_app, "_start_live_stream") as start_stream:
                                    response = client.post("/api/live/start", json={"cycle": "000002"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        stop_stream.assert_called_once_with(fake_thread, almemo_app.LIVE_STOP)
        start_stream.assert_called_once_with("000002")

    def test_live_start_keeps_fresh_running_stream(self):
        client = almemo_app.app.test_client()

        with mock.patch.object(almemo_app, "LIVE_CYCLE", "000002"):
            with mock.patch.object(almemo_app, "_fast_live_is_running", return_value=False):
                with mock.patch.object(almemo_app, "_live_is_running", return_value=True):
                    with mock.patch.object(almemo_app, "_live_data_fresh", return_value=True):
                        with mock.patch.object(almemo_app, "_stop_stream") as stop_stream:
                            with mock.patch.object(almemo_app, "_start_live_stream") as start_stream:
                                response = client.post("/api/live/start", json={"cycle": "000002"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["running"])
        stop_stream.assert_not_called()
        start_stream.assert_not_called()

    def test_fast_live_start_restarts_stale_running_stream(self):
        client = almemo_app.app.test_client()
        fake_thread = threading.Thread()

        with mock.patch.object(almemo_app, "FAST_LIVE_THREAD", fake_thread):
            with mock.patch.object(almemo_app, "FAST_LIVE_RATE", "10"):
                with mock.patch.object(almemo_app, "_live_is_running", return_value=False):
                    with mock.patch.object(almemo_app, "_fast_live_is_running", return_value=True):
                        with mock.patch.object(almemo_app, "_fast_live_data_fresh", return_value=False):
                            with mock.patch.object(almemo_app, "_stop_stream") as stop_stream:
                                with mock.patch.object(almemo_app, "_start_fast_live_stream") as start_stream:
                                    response = client.post("/api/fast-live/start", json={"rate": "10"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        stop_stream.assert_called_once_with(fake_thread, almemo_app.FAST_LIVE_STOP)
        start_stream.assert_called_once_with("10")

    def test_print_live_filter_accepts_measurement_line(self):
        self.assertTrue(almemo_app._is_print_live_data_line("12:30:10 00: +0020.6 \u00f8C NiCr"))

    def test_print_live_filter_rejects_setup_echo(self):
        self.assertFalse(almemo_app._is_print_live_data_line("\x03\rZ000001"))

    def test_v6_live_setup_selects_first_channel_after_print_cycle_start(self):
        self.assertEqual(
            almemo_app._live_setup_commands("V6", "000002"),
            ("C11", "f5 k-5", "f5 k-4", "f5 k-2", "G00", "Z000002", "S2", "M00"),
        )

    def test_v7_live_setup_keeps_existing_print_cycle_sequence(self):
        self.assertEqual(
            almemo_app._live_setup_commands("V7", "000002"),
            ("C11", "f5 k5", "G00", "Z000002", "S2"),
        )

    def test_recent_missing_reason_is_cleared_by_success(self):
        with mock.patch.object(almemo_app, "LAST_DEVICE_OK_AT", 0.0):
            with mock.patch.object(almemo_app.time, "monotonic", side_effect=[10.0, 10.5, 11.0]):
                almemo_app._mark_device_missing("device missing")
                self.assertEqual(almemo_app._recent_missing_reason(2.0), "device missing")
                almemo_app._mark_device_ok()
                self.assertIsNone(almemo_app._recent_missing_reason(2.0))

    def test_send_command_marks_missing_and_returns_fast_on_empty_response(self):
        fake_serial = mock.Mock()
        fake_serial.is_open = True
        fake_lock = threading.Lock()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "SESSION_SWITCH_LOCK", threading.Lock()):
                with mock.patch.object(almemo_app, "_pause_streaming_sessions", return_value={}):
                    with mock.patch.object(almemo_app, "_resume_streaming_sessions"):
                        with mock.patch.object(almemo_app, "_get_pser", return_value=fake_serial):
                            with mock.patch.object(almemo_app, "_read_lines", return_value=[]):
                                with mock.patch.object(almemo_app, "_discard_input_until_quiet"):
                                    with mock.patch.object(almemo_app, "_close_pser") as close_mock:
                                        with mock.patch.object(almemo_app, "_mark_device_missing") as missing_mock:
                                            result = almemo_app._send_command(
                                                "t0",
                                                timeout_ms=100,
                                                read_lines=1,
                                                raw=False,
                                                eol=almemo_app.ALMEMO_EOL,
                                            )

        self.assertFalse(result["ok"])
        self.assertIn("No response from ALMEMO device", result["error"])
        close_mock.assert_called_once()
        missing_mock.assert_called_once()

    def test_send_command_sends_xon_before_payload_after_buffer_reset(self):
        fake_serial = mock.Mock()
        fake_serial.is_open = True
        fake_lock = threading.Lock()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "SESSION_SWITCH_LOCK", threading.Lock()):
                with mock.patch.object(almemo_app, "_pause_streaming_sessions", return_value={}):
                    with mock.patch.object(almemo_app, "_resume_streaming_sessions"):
                        with mock.patch.object(almemo_app, "_get_pser", return_value=fake_serial):
                            with mock.patch.object(almemo_app, "_read_lines", return_value=["A2490-2 R02.12"]):
                                with mock.patch.object(almemo_app, "_discard_input_until_quiet"):
                                    result = almemo_app._send_command(
                                        "t0",
                                        timeout_ms=100,
                                        read_lines=1,
                                        raw=False,
                                        eol=almemo_app.ALMEMO_EOL,
                                    )

        self.assertTrue(result["ok"])
        self.assertEqual(fake_serial.write.call_args_list[0].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[1].args[0], b"t0\r\n")

    def test_send_command_rearms_link_after_stream_pause(self):
        fake_serial = mock.Mock()
        fake_serial.is_open = True
        fake_lock = threading.Lock()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "SESSION_SWITCH_LOCK", threading.Lock()):
                with mock.patch.object(almemo_app, "_pause_streaming_sessions", return_value={"live": "000001"}):
                    with mock.patch.object(almemo_app, "_resume_streaming_sessions"):
                        with mock.patch.object(almemo_app, "_get_pser", return_value=fake_serial) as get_pser_mock:
                            with mock.patch.object(almemo_app, "_read_lines", return_value=["A2490-2 R02.12"]):
                                with mock.patch.object(almemo_app, "_discard_input_until_quiet"):
                                    with mock.patch.object(almemo_app, "_close_pser") as close_mock:
                                        result = almemo_app._send_command(
                                            "t0",
                                            timeout_ms=100,
                                            read_lines=1,
                                            raw=False,
                                            eol=almemo_app.ALMEMO_EOL,
                                        )

        self.assertTrue(result["ok"])
        close_mock.assert_called_once()
        self.assertEqual(get_pser_mock.call_count, 2)
        self.assertEqual(fake_serial.write.call_args_list[0].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[1].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[2].args[0], b"t0\r\n")

    def test_send_command_returns_error_when_rearm_after_stream_pause_fails(self):
        fake_lock = threading.Lock()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "SESSION_SWITCH_LOCK", threading.Lock()):
                with mock.patch.object(almemo_app, "_pause_streaming_sessions", return_value={"fast_live": "10"}):
                    with mock.patch.object(almemo_app, "_resume_streaming_sessions"):
                        with mock.patch.object(
                            almemo_app,
                            "_get_pser",
                            side_effect=almemo_app.serial.SerialException("probe failed"),
                        ):
                            with mock.patch.object(almemo_app, "_close_pser") as close_mock:
                                result = almemo_app._send_command(
                                    "t0",
                                    timeout_ms=100,
                                    read_lines=1,
                                    raw=False,
                                    eol=almemo_app.ALMEMO_EOL,
                                )

        self.assertFalse(result["ok"])
        self.assertIn("ALMEMO session switch failed", result["error"])
        close_mock.assert_called()

    def test_send_command_sequence_runs_multiple_steps_in_one_guarded_session(self):
        fake_serial = mock.Mock()
        fake_serial.is_open = True
        fake_lock = threading.Lock()

        with mock.patch.object(almemo_app, "SERIAL_LOCK", fake_lock):
            with mock.patch.object(almemo_app, "SESSION_SWITCH_LOCK", threading.Lock()):
                with mock.patch.object(almemo_app, "_pause_streaming_sessions", return_value={"live": "000001"}) as pause_mock:
                    with mock.patch.object(almemo_app, "_resume_streaming_sessions") as resume_mock:
                        with mock.patch.object(almemo_app, "_get_pser", return_value=fake_serial) as get_pser_mock:
                            with mock.patch.object(
                                almemo_app,
                                "_read_lines",
                                side_effect=[["A2490-2 R02.12"], ["01: +0023.5 C"]],
                            ):
                                with mock.patch.object(almemo_app, "_discard_input_until_quiet"):
                                    with mock.patch.object(almemo_app, "_close_pser") as close_mock:
                                        result = almemo_app._send_command_sequence(
                                            [
                                                {"command": "t0", "timeout_ms": 100, "read_lines": 1, "raw": False},
                                                {"command": "p", "timeout_ms": 100, "read_lines": 1, "raw": False},
                                            ]
                                        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["sequence"]), 2)
        pause_mock.assert_called_once()
        resume_mock.assert_called_once()
        close_mock.assert_called_once()
        self.assertEqual(get_pser_mock.call_count, 3)
        self.assertEqual(fake_serial.write.call_args_list[0].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[1].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[2].args[0], b"t0\r\n")
        self.assertEqual(fake_serial.write.call_args_list[3].args[0], b"\x11")
        self.assertEqual(fake_serial.write.call_args_list[4].args[0], b"p\r\n")


if __name__ == "__main__":
    unittest.main()
