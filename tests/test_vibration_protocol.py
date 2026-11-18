import unittest

from app.vibration import wtvb01_protocol as proto


class ManualFrameTests(unittest.TestCase):
    """Every frame below is quoted from the WTVB01-485 manual (V260508)."""

    def test_request_frames_match_manual_examples(self):
        cases = [
            (proto.read_request(0x50, 0x34, 3), "5003003400034984"),
            (proto.read_request(0x50, 0x3A, 3), "5003003A00032847"),
            (proto.read_request(0x50, 0x40, 1), "500300400001885F"),
            (proto.read_request(0x50, 0x41, 3), "500300410003585E"),
            (proto.read_request(0x50, 0x44, 3), "500300440003485F"),
            (proto.read_request(0x50, 0x6B, 1), "5003006B0001F857"),
            (proto.unlock_request(0x50), "50060069B58822A1"),
            (proto.save_request(0x50), "500600000000844B"),
            (proto.write_request(0x50, proto.REG_SAVE, proto.REBOOT_VALUE), "5006000000FFC40B"),
            (proto.write_request(0x50, proto.REG_BAUD, proto.baud_code(115200)), "5006000400064588"),
            (proto.write_request(0x50, proto.REG_ADDRESS, 0x02), "5006001A0002244D"),
            (proto.unlock_request(0x02), "02060069B5882F13"),
            (proto.save_request(0x02), "02060000000089F9"),
        ]
        for frame, expected in cases:
            self.assertEqual(frame.hex().upper(), expected)

    def test_baud_codes_cover_documented_rates(self):
        self.assertEqual(proto.SUPPORTED_BAUDS, (4800, 9600, 19200, 38400, 57600, 115200, 230400))
        self.assertEqual(proto.baud_code(230400), 0x07)
        with self.assertRaises(ValueError):
            proto.baud_code(14400)


def _reply(address, words):
    body = bytes((address, proto.FUNC_READ, 2 * len(words))) + b"".join(w.to_bytes(2, "big") for w in words)
    crc = proto.crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


class ParseTests(unittest.TestCase):
    def test_read_response_round_trip(self):
        self.assertEqual(proto.parse_read_response(_reply(0x50, [1, 0xFFFF]), 0x50, 2), [1, 0xFFFF])

    def test_crc_error_and_wrong_address_are_rejected(self):
        frame = bytearray(_reply(0x50, [1]))
        frame[-1] ^= 0xFF
        with self.assertRaises(proto.ProtocolError):
            proto.parse_read_response(bytes(frame), 0x50, 1)
        with self.assertRaises(proto.ProtocolError):
            proto.parse_read_response(_reply(0x51, [1]), 0x50, 1)

    def test_exception_reply_is_reported(self):
        body = bytes((0x50, 0x83, 0x02))
        crc = proto.crc16(body)
        with self.assertRaises(proto.ModbusException) as ctx:
            proto.parse_read_response(body + bytes((crc & 0xFF, crc >> 8)), 0x50, 3)
        self.assertEqual(ctx.exception.code, 2)

    def test_write_echo_must_match(self):
        request = proto.unlock_request(0x50)
        proto.parse_write_response(request, request)
        with self.assertRaises(proto.ProtocolError):
            proto.parse_write_response(proto.save_request(0x50), request)


class DecodeTests(unittest.TestCase):
    def test_basic_block_scaling(self):
        first, count, fields = proto.BLOCK_BASIC
        registers = [0] * count
        registers[0x34 - first] = 0x10000 - 2048     # -1 g
        registers[0x36 - first] = 1960              # 0.957 g, as read from a resting sensor
        registers[0x3A - first] = 60000             # 600 mm/s: range end, needs unsigned
        registers[0x40 - first] = 2310              # 23.1 °C
        registers[0x41 - first] = 30000             # 30000 µm
        registers[0x44 - first] = 1005              # 100.5 Hz
        values = proto.decode_fields(first, registers, fields)
        self.assertEqual(values["acc_x_g"], -1.0)
        self.assertAlmostEqual(values["acc_z_g"], 0.957031, places=6)
        self.assertEqual(values["velocity_x_mm_s"], 600.0)
        self.assertEqual(values["temperature_c"], 23.1)
        self.assertEqual(values["displacement_x_um"], 30000)
        self.assertEqual(values["frequency_x_hz"], 100.5)

    def test_statistics_layout_follows_detailed_section(self):
        names = {f.register: f.name for f in proto.STATS_FIELDS}
        self.assertEqual(names[0x47], "crest_factor_x")
        self.assertEqual(names[0x4D], "peak_index_x")
        self.assertEqual(names[0x50], "velocity_rms_x_mm_s")
        self.assertEqual(names[0x52], "displacement_rms_x_mm")
        self.assertEqual(names[0x5C], "velocity_rms_y_mm_s")
        self.assertEqual(names[0x6A], "displacement_rms_z_mm")
        self.assertEqual(names[0x6D], "fault_z")
        self.assertEqual(len({f.name for f in proto.STATS_FIELDS + proto.ENERGY_FIELDS + proto.BASIC_FIELDS}), 97)

    def test_skewness_is_signed(self):
        first, count, fields = proto.BLOCK_STATS
        registers = [0] * count
        registers[0x5B - first] = 65531  # Read from a real sensor: skewness_y = -0.005
        self.assertEqual(proto.decode_fields(first, registers, fields)["skewness_y"], -0.005)

    def test_blocks_fit_one_request(self):
        for first, count, fields in (proto.BLOCK_BASIC, proto.BLOCK_STATS, proto.BLOCK_ENERGY):
            self.assertLessEqual(count, proto.MAX_READ_REGISTERS)
            self.assertTrue(all(first <= f.register < first + count for f in fields))

    def test_stats_block_with_invalid_rms_is_rejected(self):
        first, count, _fields = proto.BLOCK_STATS
        registers = [0] * count
        self.assertTrue(proto.block_valid(first, registers))
        registers[0x50 - first] = 0xFFFF  # velocity_rms_x read 65.535 on a real sensor
        self.assertFalse(proto.block_valid(first, registers))
        self.assertTrue(proto.block_valid(proto.BLOCK_BASIC[0], [0xFFFF] * proto.BLOCK_BASIC[1]))


class DerivedTests(unittest.TestCase):
    def _energy(self, axis, half, one, two):
        return {f"energy_{axis}_point2": half, f"energy_{axis}_point3": one, f"energy_{axis}_point4": two}

    def test_ratios_relative_to_fundamental(self):
        values = {**self._energy("x", 5, 20, 30), **self._energy("y", 0, 0, 7), **self._energy("z", 1, 4, 0)}
        derived = proto.derived_fields(values)
        self.assertEqual(derived["energy_x_ratio_2x_1x"], 1.5)
        self.assertEqual(derived["energy_x_ratio_half_1x"], 0.25)
        self.assertNotIn("energy_y_ratio_2x_1x", derived)  # No 1x energy: no ratio.
        self.assertEqual(derived["energy_z_ratio_2x_1x"], 0.0)

    def test_refresh_intervals_cover_every_block(self):
        self.assertEqual(set(proto.BLOCK_REFRESH_SEC), {b[0] for b in (proto.BLOCK_BASIC, proto.BLOCK_STATS, proto.BLOCK_ENERGY)})
        self.assertEqual(len(proto.HARMONIC_POINTS), 8)
        self.assertEqual(len(proto.HARMONIC_BANDS), 6)


if __name__ == "__main__":
    unittest.main()
