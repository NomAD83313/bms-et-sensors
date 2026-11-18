import unittest

from app.vibration import wtvb01_protocol as proto
from app.vibration.vibration_bus import BusError, ModbusBus
from tests.vibration_fakes import FakeSensor, FakeSerial, opener_for


def make_bus(*sensors, **serial_options):
    serial = FakeSerial(list(sensors), **serial_options)
    bus = ModbusBus(opener=opener_for(serial), sleep=lambda _s: None)
    bus.open("/dev/ttyFAKE", serial_options.get("baudrate", 9600))
    return bus, serial


class ReadTests(unittest.TestCase):
    def test_read_profile_decodes_values(self):
        bus, _ = make_bus(FakeSensor(registers={0x40: 2310, 0x3A: 150}))
        values = bus.read_profile(0x50, "basic")
        self.assertEqual(values["temperature_c"], 23.1)
        self.assertEqual(values["velocity_x_mm_s"], 1.5)

    def test_full_profile_reads_three_blocks(self):
        bus, serial = make_bus(FakeSensor())
        self.assertEqual(len(bus.read_profile(0x50, "full")), 97)
        self.assertEqual([frame[3] for frame in serial.written], [0x34, 0x47, 0x6E])

    def test_stray_leading_byte_is_skipped(self):
        bus, _ = make_bus(FakeSensor(registers={0x40: 100}), prefix=b"\x00")
        self.assertEqual(bus.read_profile(0x50, "basic")["temperature_c"], 1.0)

    def test_lost_reply_is_retried_once(self):
        bus, serial = make_bus(FakeSensor(), drop_next=1)
        bus.read_registers(0x50, 0x40, 1)
        self.assertEqual(len(serial.written), 2)

    def test_two_lost_replies_fail(self):
        bus, _ = make_bus(FakeSensor(), drop_next=2)
        with self.assertRaises(BusError):
            bus.read_registers(0x50, 0x40, 1)

    def test_probe_does_not_retry(self):
        bus, serial = make_bus(FakeSensor())
        self.assertFalse(bus.probe(0x10))
        self.assertEqual(len(serial.written), 1)

    def test_scan_finds_only_present_addresses(self):
        bus, _ = make_bus(FakeSensor(0x02), FakeSensor(0x50))
        self.assertEqual(bus.scan(range(1, 0x80)), [0x02, 0x50])

    def test_closed_bus_raises(self):
        bus = ModbusBus(opener=opener_for(FakeSerial([])), sleep=lambda _s: None)
        with self.assertRaises(BusError):
            bus.read_registers(0x50, 0x40, 1)

    def test_open_failure_is_bus_error(self):
        def failing(_port, _baud):
            raise OSError("busy")
        bus = ModbusBus(opener=failing, sleep=lambda _s: None)
        with self.assertRaises(BusError):
            bus.open("/dev/ttyFAKE", 9600)
        self.assertFalse(bus.is_open)


class ConfigurationTests(unittest.TestCase):
    def test_set_address_unlocks_writes_and_saves_at_new_address(self):
        sensor = FakeSensor(0x50)
        bus, serial = make_bus(sensor)
        bus.set_address(0x50, 0x02)
        self.assertEqual(sensor.address, 0x02)
        self.assertEqual(sensor.saved, 1)
        hex_frames = [f.hex().upper() for f in serial.written]
        self.assertIn("50060069B58822A1", hex_frames)
        self.assertIn("5006001A0002244D", hex_frames)
        self.assertIn("02060069B5882F13", hex_frames)
        self.assertIn("02060000000089F9", hex_frames)

    def test_set_address_refuses_an_occupied_address(self):
        bus, _ = make_bus(FakeSensor(0x50), FakeSensor(0x02))
        with self.assertRaises(BusError):
            bus.set_address(0x50, 0x02)

    def test_set_baud_moves_sensors_and_port(self):
        first, second = FakeSensor(0x01), FakeSensor(0x02)
        bus, serial = make_bus(first, second)
        results = bus.set_baud([0x01, 0x02], 115200)
        self.assertEqual(results, {0x01: "ok", 0x02: "ok"})
        self.assertEqual((first.baud, second.baud, serial.baudrate, bus.baud), (115200, 115200, 115200, 115200))
        self.assertEqual((first.saved, second.saved), (1, 1))

    def test_set_baud_reports_missing_sensor(self):
        bus, _ = make_bus(FakeSensor(0x01))
        results = bus.set_baud([0x01, 0x09], 19200)
        self.assertEqual(results[0x01], "ok")
        self.assertNotEqual(results[0x09], "ok")

    def test_set_baud_rejects_unknown_rate(self):
        bus, _ = make_bus(FakeSensor())
        with self.assertRaises(ValueError):
            bus.set_baud([0x50], 14400)

    def test_unlock_key_matches_manual(self):
        self.assertEqual(proto.UNLOCK_KEY, 0xB588)


if __name__ == "__main__":
    unittest.main()
