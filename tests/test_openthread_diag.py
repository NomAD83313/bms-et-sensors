import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.matter.otbr_diag import (
    best_usb_dongle_guess,
    neighbor_signal_summary,
    otbr_diag_snapshot,
    parse_meshdiag_topology_children,
    parse_otctl_table,
    primary_value,
    serial_link_matches_device,
)


class OpenThreadDiagTests(unittest.TestCase):
    def test_primary_value_skips_done(self):
        self.assertEqual(primary_value("\nDone\n0e080000000000010000\nDone\n"), "0e080000000000010000")
        self.assertIsNone(primary_value("\nDone\n"))

    def test_parse_otctl_table(self):
        table = """
+------+--------+-----+
| ID   | RLOC16 | AGE |
+------+--------+-----+
| 10   | 0x2800 | 9   |
+------+--------+-----+
Done
"""
        self.assertEqual(
            parse_otctl_table(table),
            {"headers": ["ID", "RLOC16", "AGE"], "rows": [["10", "0x2800", "9"]]},
        )

    def test_parse_meshdiag_topology_children(self):
        output = """
id:17 rloc16:0x4400 ext-addr:b6041f8eb35c02ac ver:5 - me - br
    3-links:{ 10 }
    children: none
id:10 rloc16:0x2800 ext-addr:16761775d58b0438 ver:5 - leader
    3-links:{ 17 }
    children:
        rloc16:0x2802 lq:3, mode:r
        rloc16:0x2803 lq:2, mode:r
Done
"""
        parsed = parse_meshdiag_topology_children(output)

        self.assertEqual(len(parsed["routers"]), 2)
        self.assertEqual(parsed["routers"][0]["id"], 17)
        self.assertEqual(parsed["routers"][0]["links"], [10])
        self.assertEqual(parsed["routers"][1]["flags"], ["leader"])
        self.assertEqual(parsed["routers"][1]["children"][0]["rloc16"], "0x2802")
        self.assertEqual(parsed["routers"][1]["children"][1]["link_quality_in"], 2)

    def test_neighbor_signal_summary_extracts_rssi(self):
        table = """
| R | RLOC16 | AGE | AVG RSSI | LAST RSSI |
| R | 0x2800 | 9   | -46      | -47       |
| R | 0xa400 | 6   | -53      | -55       |
Done
"""
        summary = neighbor_signal_summary(table)
        self.assertEqual(summary["node_count"], 2)
        self.assertEqual(summary["best_dbm"], -46)
        self.assertEqual(summary["worst_dbm"], -55)

    def test_best_usb_dongle_guess_prefers_matching_serial(self):
        devices = [
            {"manufacturer": "Generic", "product": "USB Serial", "serial": "A1", "vendor_id": "0000"},
            {"manufacturer": "Silicon Labs", "product": "Sonoff ZBDongle-E", "serial": "ABC123456", "vendor_id": "10c4"},
        ]
        serial_matches = [{"name": "usb-Silicon_Labs_Sonoff_ZBDongle-E_ABC123456", "target": "/dev/ttyACM0"}]
        self.assertEqual(best_usb_dongle_guess(devices, serial_matches), devices[1])

    def test_best_usb_dongle_guess_requires_matching_serial(self):
        devices = [
            {"manufacturer": "Silicon Labs", "product": "ALMEMO to USB", "serial": "12121728", "vendor_id": "10c4"},
        ]

        self.assertIsNone(best_usb_dongle_guess(devices, []))

    def test_serial_link_matches_device_accepts_path_target_and_basename(self):
        serial_link = {
            "name": "usb-Silicon_Labs_Sonoff_ZBDongle-E_ABC123456-if00-port0",
            "path": "/dev/serial/by-id/usb-Silicon_Labs_Sonoff_ZBDongle-E_ABC123456-if00-port0",
            "target": "/dev/ttyACM0",
        }
        self.assertTrue(serial_link_matches_device(serial_link, "/dev/ttyACM0"))
        self.assertTrue(serial_link_matches_device(serial_link, "/dev/serial/by-id/usb-Silicon_Labs_Sonoff_ZBDongle-E_ABC123456-if00-port0"))
        self.assertTrue(serial_link_matches_device(serial_link, "ttyACM0"))
        self.assertFalse(serial_link_matches_device(serial_link, "/dev/ttyACM1"))

    def test_otbr_diag_does_not_expose_thread_dataset_tlv(self):
        class FakeContainer:
            status = "running"
            attrs = {"Config": {"Env": ["OT_RCP_DEVICE=spinel+hdlc+uart:///dev/ttyOTBR?uart-baudrate=460800"]}}

            def __init__(self):
                self.commands = []

            def reload(self):
                return None

            def exec_run(self, command, stdout=True, stderr=True):
                del stdout, stderr
                self.commands.append(command)
                key = tuple(command[1:])
                outputs = {
                    ("state",): "child\nDone",
                    ("networkname",): "BMS-Thread\nDone",
                    ("channel",): "26\nDone",
                    ("panid",): "0xfaa9\nDone",
                    ("partitionid",): "1\nDone",
                    ("rloc16",): "7005\nDone",
                    ("extaddr",): "f2f681f8f84b7710\nDone",
                    ("version",): "OPENTHREAD/test\nDone",
                    ("neighbor", "table"): "Done",
                    ("router", "table"): "Done",
                    ("child", "table"): "Done",
                    ("meshdiag", "topology", "children"): "id:28 rloc16:0x7000 ext-addr:be900c5854f7aa67 ver:5 - leader\nDone",
                }
                return SimpleNamespace(exit_code=0, output=outputs.get(key, "Done").encode("utf-8"))

        container = FakeContainer()
        client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container))

        with patch("app.matter.otbr_diag.serial_devices_by_id", return_value=[]), patch("app.matter.otbr_diag.usb_devices", return_value=[]):
            payload = otbr_diag_snapshot(client)

        self.assertNotIn("dataset_tlv", payload)
        self.assertNotIn(["ot-ctl", "dataset", "active", "-x"], container.commands)

    def test_otbr_diag_reports_network_rcp_bridge_without_usb_guess(self):
        class FakeContainer:
            status = "running"
            attrs = {"Config": {"Env": ["OT_RCP_DEVICE=spinel+hdlc+uart:///dev/ttyOTBR?uart-baudrate=460800"]}}

            def reload(self):
                return None

            def exec_run(self, command, stdout=True, stderr=True):
                del stdout, stderr
                key = tuple(command[1:])
                outputs = {
                    ("state",): "router\nDone",
                    ("networkname",): "BMS-Thread\nDone",
                    ("channel",): "26\nDone",
                    ("panid",): "0xfaa9\nDone",
                    ("partitionid",): "1\nDone",
                    ("rloc16",): "b400\nDone",
                    ("extaddr",): "f2f681f8f84b7710\nDone",
                    ("version",): "OPENTHREAD/test\nDone",
                    ("neighbor", "table"): "Done",
                    ("router", "table"): "Done",
                    ("child", "table"): "Done",
                    ("meshdiag", "topology", "children"): "Done",
                }
                return SimpleNamespace(exit_code=0, output=outputs.get(key, "Done").encode("utf-8"))

        client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: FakeContainer()))
        usb_devices = [{"manufacturer": "Silicon Labs", "product": "ALMEMO to USB", "vendor_id": "10c4", "product_id": "ea60"}]

        with patch.dict("os.environ", {"OTBR_RCP_TCP_ENDPOINT": "10.42.0.2:6638"}), patch(
            "app.matter.otbr_diag.serial_devices_by_id", return_value=[]
        ), patch("app.matter.otbr_diag.usb_devices", return_value=usb_devices):
            payload = otbr_diag_snapshot(client)

        dongle = payload["dongle"]
        self.assertEqual(dongle["rcp_device"], "/dev/ttyOTBR")
        self.assertEqual(dongle["transport"]["kind"], "network_rcp_bridge")
        self.assertEqual(dongle["transport"]["endpoint"], "10.42.0.2:6638")
        self.assertIsNone(dongle["usb_guess"])


if __name__ == "__main__":
    unittest.main()
