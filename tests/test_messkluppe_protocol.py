import unittest

from app.messkluppe.messkluppe_protocol import (
    TASK_FILE_DOWNLOAD,
    TASK_FILE_LIST,
    TASK_IDLE,
    TASK_LIVE_DATA,
    TASK_LOGGING,
    build_file_download_command,
    build_file_list_command,
    build_logging_command,
    build_ping_command,
    decode_file_data_packet,
    decode_live_data_packet,
    decode_ping_packet,
    make_id_task,
    radio_bytes_to_words_16,
    radio_bytes_to_file_words,
    split_id_task,
    words16_to_legacy_payload,
    words_to_radio_bytes_32,
)


class MesskluppeProtocolTests(unittest.TestCase):
    def test_id_task_roundtrip(self):
        value = make_id_task(1, TASK_LOGGING)

        self.assertEqual(value, 1020)
        parsed = split_id_task(value)
        self.assertEqual(parsed.clip_id, 1)
        self.assertEqual(parsed.task, TASK_LOGGING)

    def test_host_command_uses_legacy_reversed_32_bit_word_order(self):
        payload = words_to_radio_bytes_32([0x01020304, 0xAABBCCDD])

        self.assertEqual(payload, bytes.fromhex("04030201ddccbbaa"))

    def test_decode_legacy_16_bit_payload_words(self):
        payload = words16_to_legacy_payload([0x1234, 0xABCD, *range(2, 16)])

        self.assertEqual(len(payload), 32)
        self.assertEqual(radio_bytes_to_words_16(payload)[0:2], (0x1234, 0xABCD))

    def test_decode_ping_packet_combines_timestamp_words(self):
        payload = words16_to_legacy_payload([
            make_id_task(1, TASK_IDLE),
            0x0001,
            0x0020,
            42,
            9500,
            7,
            *([0] * 10),
        ])

        packet = decode_ping_packet(payload)

        self.assertEqual(packet.clip_id, 1)
        self.assertEqual(packet.task, TASK_IDLE)
        self.assertEqual(packet.timestamp_ms, 0x00010020)
        self.assertEqual(packet.ping_ms, 42)
        self.assertEqual(packet.success_percent_x100, 9500)
        self.assertEqual(packet.file_count, 7)

    def test_decode_file_download_payload_combines_time_and_line_number(self):
        payload = words16_to_legacy_payload([
            make_id_task(1, TASK_FILE_DOWNLOAD),
            0x5E2D,
            0x1234,
            0x0000,
            0x0009,
            101,
            102,
            103,
            104,
            105,
            106,
            107,
            108,
            109,
            110,
            111,
        ])

        words = radio_bytes_to_file_words(payload)
        packet = decode_file_data_packet(payload)

        self.assertEqual(words[1], 0x5E2D1234)
        self.assertEqual(words[2], 9)
        self.assertEqual(packet.clip_id, 1)
        self.assertEqual(packet.task, TASK_FILE_DOWNLOAD)
        self.assertEqual(packet.unix_time, 0x5E2D1234)
        self.assertEqual(packet.line_number, 9)
        self.assertEqual(packet.values, tuple(range(101, 112)))

    def test_decode_live_payload_uses_live_sensor_word_layout(self):
        payload = words16_to_legacy_payload([
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
        ])

        packet = decode_live_data_packet(payload)

        self.assertEqual(packet.clip_id, 1)
        self.assertEqual(packet.task, TASK_LIVE_DATA)
        self.assertEqual(packet.timestamp_ms, 0x00030768)
        self.assertEqual(packet.values, (307, 309, 299, 270, 2276, 4076, 77, 0, 1))

    def test_build_common_commands(self):
        self.assertEqual(build_ping_command(1, 0x11223344), bytes.fromhex("e803000044332211"))
        self.assertEqual(build_file_list_command(1, 2)[0:8], bytes.fromhex("0604000002000000"))
        self.assertEqual(build_logging_command(1, 2, 3, 2500, 20)[0:20], bytes.fromhex("fc0300000200000003000000c409000014000000"))
        self.assertEqual(build_file_download_command(1, 2, 1580000000, 1, 70000)[0:28], bytes.fromhex("10040000020000000000000000e32c5e010000000100000070110000"))


if __name__ == "__main__":
    unittest.main()
