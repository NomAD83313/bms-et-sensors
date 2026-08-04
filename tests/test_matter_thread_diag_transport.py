import tempfile
import unittest
from pathlib import Path

from app.matter.thread_diag_transport import (
    decode_transport_payload,
    extract_lines_from_payload,
    load_transport_lines,
)


class ThreadDiagTransportTests(unittest.TestCase):
    def test_decode_plain_text_payload(self):
        payload = (
            "ThreadDiag: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
            "role=leader ext_address=a670a314caa9803d rloc16=0x8800 thread_attached=1 commissioned=1\n"
            "ThreadDiagParent: schema=1 serial=BMS-C6Z-5BAFA0 product=ESP32-C6-Zero "
            "source=reported-by-node parent_ext_address=a670a314caa9803d parent_rloc16=0x8800 "
            "link_quality_in=3 link_quality_out=3 parent_avg_rssi_dbm=-53 parent_last_rssi_dbm=-53\n"
        )
        lines = decode_transport_payload(payload)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("ThreadDiag:"))

    def test_decode_json_payload_with_lines(self):
        payload = '{"lines":["ThreadDiag: schema=1 serial=A product=X role=child ext_address=1 rloc16=0x1 thread_attached=1 commissioned=1"]}'
        lines = decode_transport_payload(payload)
        self.assertEqual(len(lines), 1)
        self.assertIn("serial=A", lines[0])

    def test_extract_lines_from_list_payload(self):
        lines = extract_lines_from_payload([" one ", "", "two"])
        self.assertEqual(lines, ["one", "two"])

    def test_load_transport_lines_from_file_plain_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feed.txt"
            path.write_text(
                "ThreadDiag: schema=1 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
                "role=router ext_address=767f13ec2877d7a9 rloc16=0x9c00 thread_attached=1 commissioned=1\n",
                encoding="utf-8",
            )
            lines = load_transport_lines("file", file_path=str(path), http_url="", timeout_sec=1.0)
            self.assertEqual(len(lines), 1)
            self.assertIn("serial=BMS-C6DK-499B30", lines[0])

    def test_load_transport_lines_from_file_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feed.json"
            path.write_text(
                '{"lines":["ThreadDiagNeighbor: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC source=observed-by-parent ext_address=767f13ec2877d7a9 rloc16=0x9c00 role=router is_child=0 full_thread_device=1 link_quality_in=3 link_quality_out=-1 avg_rssi_dbm=-56 last_rssi_dbm=-55 age_s=4"]}',
                encoding="utf-8",
            )
            lines = load_transport_lines("file", file_path=str(path), http_url="", timeout_sec=1.0)
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("ThreadDiagNeighbor:"))

    def test_load_transport_lines_disabled_mode(self):
        lines = load_transport_lines("off", file_path="/tmp/unused", http_url="", timeout_sec=1.0)
        self.assertEqual(lines, [])

    def test_load_transport_lines_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            load_transport_lines("tcp", file_path="/tmp/unused", http_url="", timeout_sec=1.0)


if __name__ == "__main__":
    unittest.main()
