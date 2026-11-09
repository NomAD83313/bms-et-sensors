import threading
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from app.pyrometers import pyrometers_app as app
from app.pyrometers.pyrometer_profiles import DeviceProfile


class PyrometersLifecycleTests(unittest.TestCase):
    def setUp(self):
        profile = DeviceProfile(
            id="test", port_env_key="TEST_PORT", default_port="/dev/fake",
            device_name="test", display_name="test", ui_subtitle="test",
            source_tag="test", serial="test", sensor_serial="", match_tokens=(),
            poll_mode=True,
        )
        self.runtime = app.DeviceRuntime(profile)

    def run_poll(self, present, failures=None):
        ports = [Mock(), Mock()]
        stop = threading.Event()
        sleeps = 0

        def sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps == len(present):
                stop.set()

        with ExitStack() as stack:
            stack.enter_context(patch.object(app, "POLL_STOP", stop))
            stack.enter_context(patch.object(app, "_open_influx_writer", return_value=(None, None)))
            stack.enter_context(patch.object(app, "_resolve_port", return_value="/dev/fake"))
            stack.enter_context(patch.object(app.os.path, "exists", side_effect=present))
            stack.enter_context(patch.object(app.time, "sleep", side_effect=sleep))
            opened = stack.enter_context(patch.object(app, "_open_serial", side_effect=ports))
            for name in ("_update_device_state", "_verify_sensor_identity", "_process_ir_setting_requests", "_drain_serial_input", "_begin_measurement_session"):
                stack.enter_context(patch.object(app, name))
            stack.enter_context(patch.object(app, "_read_serial_exact", side_effect=failures, return_value=b""))
            app._device_loop_poll(self.runtime)
        return ports, opened.call_count

    def test_stop_closes_serial(self):
        ports, opens = self.run_poll([True])
        self.assertEqual(opens, 1)
        ports[0].close.assert_called_once()

    def test_disconnect_closes_port_and_recovery_opens_new_port(self):
        ports, opens = self.run_poll([True, False, True])
        self.assertEqual(opens, 2)
        for port in ports:
            port.close.assert_called_once()

    def test_read_failure_closes_port_before_reconnect(self):
        ports, opens = self.run_poll([True, True], [OSError("device lost"), b""])
        self.assertEqual(opens, 2)
        for port in ports:
            port.close.assert_called_once()

    def test_writer_initialization_failure_closes_client(self):
        client = Mock()
        client.write_api.side_effect = RuntimeError("writer failed")
        with patch.object(app, "_influx_enabled", return_value=True), patch.object(app, "InfluxDBClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "writer failed"):
                app._open_influx_writer()
        client.close.assert_called_once()

    def test_writer_flush_failure_still_closes_writer(self):
        writer = Mock()
        writer.flush.side_effect = RuntimeError("flush failed")
        app._close_influx_writer(writer)
        writer.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
