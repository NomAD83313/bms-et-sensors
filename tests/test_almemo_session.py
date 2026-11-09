import threading
import unittest
from unittest.mock import Mock, patch, call

from app.almemo.almemo_serial import AlmemoSession
from app.almemo.almemo_streaming import AlmemoStreaming


class AlmemoSessionTests(unittest.TestCase):
    def setUp(self):
        self.session = AlmemoSession()
        self.streams = AlmemoStreaming(self.session)
        self.session.streams = self.streams

    def test_command_failure_resumes_previous_stream_and_stops_batch(self):
        previous = {"fast_live": "10"}
        with (
            patch.object(self.streams, "_pause_streaming_sessions", return_value=previous),
            patch.object(self.streams, "_resume_streaming_sessions") as resume,
            patch.object(self.session, "_rearm_command_session_after_stream_pause"),
            patch.object(self.session, "_send_command_on_active_serial", side_effect=[
                {"ok": True}, {"ok": False, "error": "no response"}, {"ok": True},
            ]) as send,
        ):
            result = self.session._send_command_sequence([{"command": name} for name in ("G00", "t0", "p")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_command"], "t0")
        self.assertEqual(send.call_count, 2)
        resume.assert_called_once_with(previous)

    def test_rearm_failure_still_resumes_previous_stream(self):
        previous = {"live": "000002"}
        with (
            patch.object(self.streams, "_pause_streaming_sessions", return_value=previous),
            patch.object(self.streams, "_resume_streaming_sessions") as resume,
            patch.object(self.session, "_rearm_command_session_after_stream_pause", side_effect=OSError("lost")),
            patch.object(self.session, "_close_pser"),
        ):
            result = self.session._send_command_sequence([{"command": "t0"}])
        self.assertFalse(result["ok"])
        resume.assert_called_once_with(previous)

    def test_live_reader_preserves_setup_cleanup_and_final_record_write(self):
        serial_port = Mock()
        serial_port.__enter__ = Mock(return_value=serial_port)
        serial_port.__exit__ = Mock(return_value=False)
        stop = self.streams.LIVE_STOP
        lines = iter([b"12:34:56 00: +23.5 C Room\r\n", b"01: +24.1 C Outdoor\r\n"])
        def read_line():
            try:
                return next(lines)
            except StopIteration:
                stop.set()
                return b""
        serial_port.readline.side_effect = read_line
        with (
            patch.object(self.streams, "_influx_enabled", return_value=False),
            patch.object(self.session, "_close_pser") as close,
            patch.object(self.session, "_open_verified_serial", return_value=(serial_port, "/dev/fake")),
            patch.object(self.session, "_write_line") as write,
            patch.object(self.session, "_discard_input_until_quiet") as drain,
            patch.object(self.streams, "_write_record_to_influx") as record,
            patch("app.almemo.almemo_streaming.time.sleep"),
        ):
            self.streams._live_reader("000002")
        close.assert_called_once()
        commands = [item.args[1] for item in write.call_args_list]
        self.assertEqual(commands, ["C11", "f5 k-5", "f5 k-4", "f5 k-2", "G00", "Z000002", "S2", "M00", "C11"])
        record.assert_called_once_with(None, "12:34:56 00: +23.5 C Room 01: +24.1 C Outdoor", "print_cycle")
        drain.assert_called_once_with(serial_port, max_sec=1.5, quiet_sec=0.15)
        self.assertEqual(list(self.streams.LIVE_LINES), ["12:34:56 00: +23.5 C Room", "01: +24.1 C Outdoor"])
        self.assertFalse(self.session.SERIAL_LOCK.locked())
        self.assertFalse(stop.is_set())

    def test_concurrent_batches_cannot_interleave_serial_commands(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_entered = threading.Event()
        commands = []
        errors = []

        def send(command, **kwargs):
            commands.append(command)
            self.assertTrue(self.session.SERIAL_LOCK.locked())
            if command == "first-a":
                first_entered.set()
                if not release_first.wait(2.0):
                    raise RuntimeError("batch release timed out")
            if command == "second":
                second_entered.set()
            return {"ok": True}

        def worker(names, started=None):
            if started is not None:
                started.set()
            try:
                self.session._send_command_sequence([{"command": name} for name in names])
            except Exception as exc:
                errors.append(exc)

        with (
            patch.object(self.streams, "_pause_streaming_sessions", return_value={}),
            patch.object(self.streams, "_resume_streaming_sessions"),
            patch.object(self.session, "_send_command_on_active_serial", side_effect=send),
        ):
            first = threading.Thread(target=worker, args=(["first-a", "first-b"],))
            second = threading.Thread(target=worker, args=(["second"], second_started))
            first.start()
            try:
                self.assertTrue(first_entered.wait(1.0))
                second.start()
                self.assertTrue(second_started.wait(1.0))
                self.assertFalse(second_entered.wait(0.05))
            finally:
                release_first.set()
                first.join(timeout=2.0)
                if second.ident is not None:
                    second.join(timeout=2.0)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(commands, ["first-a", "first-b", "second"])


if __name__ == "__main__":
    unittest.main()
