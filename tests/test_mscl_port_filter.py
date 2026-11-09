import unittest
from unittest import mock

from app.mscl.mscl_port_filter import filter_excluded_usb_ports, parse_usb_ids


class MsclPortFilterTests(unittest.TestCase):
    def test_parse_usb_ids_accepts_comma_separated_values(self):
        self.assertEqual(
            parse_usb_ids("10C4:834B, 0483:5740"),
            {("10c4", "834b"), ("0483", "5740")},
        )

    def test_filter_excluded_usb_ports_rejects_pyrometers(self):
        paths = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
        with mock.patch(
            "app.mscl.mscl_port_filter.serial_usb_ids",
            side_effect=[("10c4", "834b"), ("10c4", "ea60")],
        ):
            self.assertEqual(filter_excluded_usb_ports(paths, "10c4:834b"), ["/dev/ttyUSB1"])

    def test_filter_excluded_usb_ports_keeps_unknown_devices(self):
        with mock.patch("app.mscl.mscl_port_filter.serial_usb_ids", return_value=None):
            self.assertEqual(filter_excluded_usb_ports(["/dev/ttyUSB0"], "10c4:834b"), ["/dev/ttyUSB0"])


if __name__ == "__main__":
    unittest.main()
