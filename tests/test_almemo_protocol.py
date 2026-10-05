import unittest
from app.almemo.almemo_protocol import Measurement, parse_measurements
from app.almemo.almemo_serial import AlmemoSession
from app.almemo.almemo_streaming import AlmemoStreaming
from unittest.mock import patch
from datetime import datetime, timezone


class AlmemoProtocolTests(unittest.TestCase):
    def test_v6_parses_channels_sensor_names_and_limit_markers(self):
        self.assertEqual(
            parse_measurements("12:34:56 | 00: >+23.5 C Room Sensor | 01: <-1.2 C Outdoor", "V6"),
            [Measurement("00", 23.5, "C", "Room Sensor"), Measurement("01", -1.2, "C", "Outdoor")],
        )

    def test_v7_parses_semicolon_records(self):
        self.assertEqual(parse_measurements("12:34:56;0.0;23.5;C;1.0;-2.1;C", "V7"),
                         [Measurement("0.0", 23.5, "C", ""), Measurement("1.0", -2.1, "C", "")])

    def test_empty_noise_and_invalid_values_produce_no_points(self):
        for version in ("V6", "V7"):
            for text in ("", "G00", "t0", "00: ERROR C", "0.0;ERROR;C"):
                self.assertEqual(parse_measurements(text, version), [])

    def test_influx_adapter_preserves_measurement_tags_and_timestamp(self):
        session = AlmemoSession()
        stream = AlmemoStreaming(session)
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(stream, "_record_timestamp", return_value=at):
            points = stream._parse_record_points("00: +23.5 C Room", "print_cycle")
        line = points[0].to_line_protocol()
        self.assertIn("almemo_live,", line)
        self.assertIn("channel=00", line)
        self.assertIn("mode=print_cycle", line)
        self.assertIn("source=almemo", line)
        self.assertIn("sensor=Room", line)
        self.assertTrue(line.endswith(" 1767225600000"), line)

    def test_detected_device_version_updates_session_protocol(self):
        session = AlmemoSession()
        session._update_protocol_version("ALMEMO 710 V7")
        self.assertEqual(session._get_protocol_version(), "V7")
        session._update_protocol_version("8590-9KL 6.52")
        self.assertEqual(session._get_protocol_version(), "V6")

    def test_session_and_stream_state_are_not_shared_between_instances(self):
        first, second = AlmemoSession(), AlmemoSession()
        first.ALMEMO_PROTOCOL_VERSION = "V7"
        self.assertEqual(second._get_protocol_version(), "V6")
        stream1, stream2 = AlmemoStreaming(first), AlmemoStreaming(second)
        stream1._live_push("00: 23.5 C")
        self.assertEqual(list(stream2.LIVE_LINES), [])
        self.assertEqual(stream2.LIVE_LAST_DATA_AT, 0.0)


if __name__ == "__main__":
    unittest.main()
