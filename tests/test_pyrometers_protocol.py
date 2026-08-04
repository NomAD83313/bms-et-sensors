import unittest

from app.pyrometers.pyrometers_protocol import (
    build_classic_ct_burst_start_commands,
    build_classic_ct_burst_stop_commands,
    build_classic_ct_burst_string_command,
    build_optris_burst_mode_command,
    build_optris_burst_start_commands,
    build_optris_burst_value_command,
    build_optris_read_emissivity_command,
    build_optris_read_transmissivity_command,
    build_optris_set_ambient_fixed_temperature_command,
    build_optris_set_ambient_source_command,
    build_optris_set_emissivity_command,
    build_optris_set_transmissivity_command,
    build_read_serial_number_command,
    extract_binary_frame,
    extract_binary_frames,
    parse_optris_ir_factor_response,
    parse_serial_number_response,
    parse_binary_frame,
    parse_binary_frames,
    parse_burst_word_frames,
    parse_marked_burst_word_frames,
)


class ThermometerProtocolTests(unittest.TestCase):
    def test_extract_binary_frame_uses_marker_prefix(self):
        blob = bytes.fromhex("aaaa052b04e00564052baaaa052c04e10565052c")
        self.assertEqual(extract_binary_frame(blob), bytes.fromhex("aaaa052c04e10565052c"))

    def test_extract_binary_frames_returns_all_complete_frames(self):
        blob = bytes.fromhex("aaaa052b04e00564052baaaa052c04e10565052c")
        self.assertEqual(
            extract_binary_frames(blob),
            [
                bytes.fromhex("aaaa052b04e00564052b"),
                bytes.fromhex("aaaa052c04e10565052c"),
            ],
        )

    def test_parse_binary_frame_decodes_offset_tenths(self):
        parsed = parse_binary_frame(bytes.fromhex("aaaa052b04e00564052b"))
        self.assertEqual(parsed["channel_1_c"], 24.8)
        self.assertEqual(parsed["channel_2_c"], 38.0)
        self.assertEqual(parsed["channel_3_c"], 32.3)
        self.assertEqual(parsed["sensor_head_temperature_c"], 24.8)
        self.assertEqual(parsed["controller_box_temperature_c"], 38.0)
        self.assertEqual(parsed["object_temperature_c"], 32.3)
        self.assertEqual(parsed["object_primary_temperature_c"], 32.3)
        self.assertEqual(parsed["object_duplicate_temperature_c"], 32.3)
        self.assertEqual(parsed["value_c"], 32.3)
        self.assertEqual(parsed["labels"]["channel_2"], "TBox")
        self.assertEqual(parsed["marker_hex"], "aaaa")

    def test_parse_binary_frames_decodes_each_frame(self):
        parsed = parse_binary_frames(bytes.fromhex("aaaa052b04e00564052baaaa052c04e10565052c"))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["object_temperature_c"], 32.3)
        self.assertEqual(parsed[1]["object_temperature_c"], 32.4)

    def test_extract_binary_frames_rejects_non_matching_object_duplicates(self):
        blob = bytes.fromhex("aaaa052b04e005640600")
        self.assertEqual(extract_binary_frames(blob), [])

    def test_build_optris_burst_commands_match_documented_examples(self):
        self.assertEqual(
            build_optris_burst_value_command(("target_avg", "target_act", "internal", "box", "process_act")).hex(),
            "51010203040800000000000000000000005d",
        )
        self.assertEqual(build_optris_burst_mode_command(True, 100).hex(), "5201006437")
        self.assertEqual(build_optris_burst_mode_command(False, 0).hex(), "5200000052")

    def test_build_optris_burst_start_commands_sets_values_then_starts(self):
        commands = build_optris_burst_start_commands(("target_act", "internal", "box", "target_act"), 50)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][0], 0x51)
        self.assertEqual(commands[1].hex(), "5201003261")

    def test_build_classic_ct_burst_commands_match_marked_stream_setup(self):
        self.assertEqual(
            build_classic_ct_burst_string_command(("target", "head", "box", "target")).hex(),
            "511231000072",
        )
        self.assertEqual(
            [command.hex() for command in build_classic_ct_burst_start_commands(("target", "head", "box", "target"))],
            ["511231000072", "520153"],
        )
        self.assertEqual([command.hex() for command in build_classic_ct_burst_stop_commands()], ["520052"])

    def test_build_classic_ct_burst_commands_can_omit_checksum(self):
        self.assertEqual(
            [command.hex() for command in build_classic_ct_burst_start_commands(
                ("target", "head", "box", "target"), checksum_enabled=False
            )],
            ["5112310000", "5201"],
        )
        self.assertEqual(
            [command.hex() for command in build_classic_ct_burst_stop_commands(checksum_enabled=False)],
            ["5200"],
        )

    def test_serial_number_command_and_response(self):
        self.assertEqual(build_read_serial_number_command(), b"\x0e")
        self.assertEqual(parse_serial_number_response((6060099).to_bytes(3, "big")), "6060099")
        self.assertIsNone(parse_serial_number_response(b"\x01\x02"))

    def test_build_optris_ir_factor_commands_use_scaled_value_and_checksum(self):
        self.assertEqual(build_optris_read_emissivity_command().hex(), "04")
        self.assertEqual(build_optris_read_transmissivity_command().hex(), "05")
        self.assertEqual(build_optris_set_emissivity_command(0.95).hex(), "8403b631")
        self.assertEqual(build_optris_set_transmissivity_command(0.80).hex(), "850320a6")
        self.assertEqual(parse_optris_ir_factor_response(bytes.fromhex("03b6")), 0.95)

    def test_build_optris_ambient_compensation_commands(self):
        self.assertEqual(build_optris_set_ambient_source_command("fixed").hex(), "1300000013")
        self.assertEqual(build_optris_set_ambient_source_command("internal").hex(), "1300000112")
        self.assertEqual(build_optris_set_ambient_fixed_temperature_command(65.0).hex(), "1301067266")

    def test_build_optris_ir_factor_commands_reject_out_of_range_values(self):
        with self.assertRaises(ValueError):
            build_optris_set_emissivity_command(0.01)
        with self.assertRaises(ValueError):
            build_optris_set_transmissivity_command(1.5)

    def test_parse_burst_word_frames_decodes_unmarked_words(self):
        parsed, remainder = parse_burst_word_frames(
            bytes.fromhex("052b04e00564052bff"),
            ("target_act", "internal", "box", "target_act"),
        )
        self.assertEqual(remainder, b"\xff")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["object_temperature_c"], 32.3)
        self.assertEqual(parsed[0]["sensor_head_temperature_c"], 24.8)
        self.assertEqual(parsed[0]["controller_box_temperature_c"], 38.0)

    def test_parse_burst_word_frames_rejects_marked_stream_frames(self):
        parsed, remainder = parse_burst_word_frames(
            bytes.fromhex("aaaa05dc04d704e905dc"),
            ("target_act", "internal", "box", "target_act"),
        )
        self.assertEqual(parsed, [])
        self.assertEqual(remainder, bytes.fromhex("05dc04d704e905dc"))

    def test_parse_marked_single_channel_burst_frames(self):
        parsed, remainder = parse_marked_burst_word_frames(
            bytes.fromhex("ffaaaa053faaaa0540aa"),
            ("target",),
        )
        self.assertEqual(remainder, b"\xaa")
        self.assertEqual([item["object_temperature_c"] for item in parsed], [34.3, 34.4])
        self.assertEqual(parsed[0]["frame_hex"], "aaaa053f")


if __name__ == "__main__":
    unittest.main()
