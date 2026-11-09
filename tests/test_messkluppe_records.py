import unittest

from app.messkluppe.messkluppe_protocol import (
    TASK_FILE_DOWNLOAD,
    TASK_IDLE,
    TASK_LIVE_DATA,
    decode_file_data_packet,
    decode_live_data_packet,
    decode_ping_packet,
    make_id_task,
    words16_to_legacy_payload,
)
from app.messkluppe.messkluppe_records import (
    file_packet_to_fields,
    file_packet_to_influx_record,
    live_packet_to_fields,
    live_packet_to_influx_record,
    ping_packet_to_fields,
    ping_packet_to_influx_record,
    record_to_line_protocol,
)


class MesskluppeRecordTests(unittest.TestCase):
    def _packet(self):
        payload = words16_to_legacy_payload([
            make_id_task(1, TASK_FILE_DOWNLOAD),
            0x5E2D,
            0x1234,
            0x0000,
            0x0009,
            123,
            0xFFFE,
            102,
            103,
            104,
            105,
            1234,
            2500,
            2600,
            3700,
            0,
        ])
        return decode_file_data_packet(payload)

    def test_file_packet_to_fields_keeps_raw_and_named_values(self):
        fields = file_packet_to_fields(self._packet())

        self.assertEqual(fields["line"], 9)
        self.assertEqual(fields["sensor_ms"], 123)
        self.assertEqual(fields["force_x_raw"], -2)
        self.assertEqual(fields["yaw_raw"], 1234)
        self.assertEqual(fields["yaw_deg"], 123.4)
        self.assertEqual(fields["raw_01"], 0xFFFE)

    def test_file_packet_to_influx_record_sets_tags_and_time(self):
        record = file_packet_to_influx_record(self._packet(), file_id=1580000000)

        self.assertEqual(record.measurement, "messkluppe_sensor")
        self.assertEqual(record.tags["source"], "messkluppe")
        self.assertEqual(record.tags["clip_id"], "1")
        self.assertEqual(record.tags["file_id"], "1580000000")
        self.assertEqual(record.time_ns, 0x5E2D1234 * 1_000_000_000 + 123_000_000)

    def test_record_to_line_protocol(self):
        record = file_packet_to_influx_record(self._packet(), file_id=1580000000)
        line = record_to_line_protocol(record)

        self.assertIn("messkluppe_sensor,clip_id=1,file_id=1580000000,packet_task=40,source=messkluppe", line)
        self.assertIn("force_x_raw=-2i", line)
        self.assertIn("yaw_deg=123.4", line)
        self.assertTrue(line.endswith(str(record.time_ns)))

    def test_live_packet_to_fields_uses_live_word_layout(self):
        packet = decode_live_data_packet(words16_to_legacy_payload([
            make_id_task(1, TASK_LIVE_DATA),
            0x0003,
            0x0768,
            423,
            326,
            307,
            309,
            299,
            270,
            2276,
            4076,
            77,
            0,
            1,
            3,
            777,
        ]))

        fields = live_packet_to_fields(packet)
        record = live_packet_to_influx_record(packet, file_id="radio")

        self.assertEqual(fields["node_millis"], 0x00030768)
        self.assertEqual(fields["force_x_raw"], 307)
        self.assertEqual(fields["force_y_raw"], 309)
        self.assertEqual(fields["force_z_raw"], 299)
        self.assertEqual(fields["accel_x_raw"], 270)
        self.assertEqual(fields["accel_y_raw"], 2276)
        self.assertEqual(fields["yaw_raw"], 4076)
        self.assertEqual(fields["yaw_deg"], 47.6)
        self.assertEqual(record.tags["packet_task"], "60")
        self.assertIsNone(record.time_ns)

    def test_yaw_degrees_are_normalized_to_one_turn(self):
        packet = decode_live_data_packet(words16_to_legacy_payload([
            make_id_task(1, TASK_LIVE_DATA),
            0x0000,
            0x0001,
            0,
            0,
            100,
            200,
            300,
            400,
            500,
            4076,
            77,
            0,
            1,
            0,
            0,
        ]))

        fields = live_packet_to_fields(packet)

        self.assertEqual(fields["yaw_raw"], 4076)
        self.assertEqual(fields["yaw_deg"], 47.6)

    def test_ping_packet_to_fields_keeps_status_out_of_sensor_fields(self):
        packet = decode_ping_packet(words16_to_legacy_payload([
            make_id_task(1, TASK_IDLE),
            0x0004,
            0x93AE,
            100,
            9500,
            3,
            *([0] * 10),
        ]))

        fields = ping_packet_to_fields(packet)
        record = ping_packet_to_influx_record(packet, file_id="radio")

        self.assertEqual(fields["node_millis"], 0x000493AE)
        self.assertEqual(fields["ping_ms"], 100)
        self.assertEqual(fields["success_percent_x100"], 9500)
        self.assertEqual(fields["success_ratio"], 95.0)
        self.assertEqual(fields["file_count"], 3)
        self.assertNotIn("force_x_raw", fields)
        self.assertEqual(record.tags["packet_task"], "0")


if __name__ == "__main__":
    unittest.main()
