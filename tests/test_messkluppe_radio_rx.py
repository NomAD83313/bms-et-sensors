import unittest

from app.messkluppe.messkluppe_radio_rx import MesskluppeRadioRx


class FakeSpi:
    def __init__(self):
        self.closed = False
        self.max_speed_hz = 0
        self.registers = {0x17: 0x01}
        self.commands = []

    def open(self, bus, device):
        self.bus = bus
        self.device = device

    def close(self):
        self.closed = True

    def xfer2(self, data):
        self.commands.append(list(data))
        command = data[0]
        if command == 0xFF:
            return [0x0E]
        if command == 0x61:
            return [0x40, *range(32)]
        if command == 0xA9:
            self.ack_payload = list(data[1:])
            return [0x0E, *([0] * (len(data) - 1))]
        if command == 0xE1:
            self.flushed_tx = True
            return [0x0E]
        if command & 0xE0 == 0x20:
            reg = command & 0x1F
            if len(data) > 2:
                self.registers[reg] = list(data[1:])
            else:
                self.registers[reg] = data[1]
            return [0x0E, *([0] * (len(data) - 1))]
        if command & 0x1F == command and len(data) > 1:
            reg = command & 0x1F
            value = self.registers.get(reg, 0)
            if isinstance(value, list):
                values = value[: len(data) - 1]
            else:
                values = [value]
            return [0x0E, *values, *([0] * max(0, len(data) - 1 - len(values)))]
        return [0x0E, *([0] * (len(data) - 1))]


class MesskluppeRadioRxTests(unittest.TestCase):
    def test_read_payload_returns_none_when_fifo_is_empty(self):
        radio = MesskluppeRadioRx({"payload_size": 32})
        radio.spi = FakeSpi()

        self.assertIsNone(radio.read_payload())

    def test_read_payload_reads_fixed_payload_when_fifo_has_data(self):
        radio = MesskluppeRadioRx({"payload_size": 32})
        radio.spi = FakeSpi()
        radio.spi.registers[0x17] = 0x00

        payload = radio.read_payload()

        self.assertEqual(payload, bytes(range(32)))

    def test_write_ack_payload_queues_pipe_one_payload(self):
        radio = MesskluppeRadioRx({"payload_size": 32})
        radio.spi = FakeSpi()

        result = radio.write_ack_payload(bytes.fromhex("24040000c3ac4508"), pipe=1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pipe"], 1)
        self.assertEqual(result["payload_hex"], "24040000c3ac4508")
        self.assertEqual(radio.spi.ack_payload, list(bytes.fromhex("24040000c3ac4508")))
        self.assertTrue(radio.spi.flushed_tx)

    def test_write_ack_payload_can_pad_to_static_payload_size(self):
        radio = MesskluppeRadioRx({"payload_size": 32})
        radio.spi = FakeSpi()

        result = radio.write_ack_payload(bytes.fromhex("24040000c3ac4508"), pipe=1, pad_to=32)

        self.assertEqual(result["size"], 32)
        self.assertEqual(radio.spi.ack_payload[:8], list(bytes.fromhex("24040000c3ac4508")))
        self.assertEqual(radio.spi.ack_payload[8:], [0] * 24)


if __name__ == "__main__":
    unittest.main()
