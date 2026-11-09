import unittest

from app.matter.thread_diag import (
    ThreadDiagNeighborRecord,
    ThreadDiagNodeRecord,
    ThreadDiagParentRecord,
    parse_thread_diag_line,
    parse_thread_diag_lines,
    record_to_dict,
)


class ThreadDiagParseTests(unittest.TestCase):
    def test_parse_thread_diag_node_router(self):
        line = (
            "ThreadDiag: schema=1 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
            "role=router ext_address=767f13ec2877d7a9 rloc16=0x9c00 thread_attached=1 commissioned=1"
        )
        record = parse_thread_diag_line(line)
        self.assertIsInstance(record, ThreadDiagNodeRecord)
        assert isinstance(record, ThreadDiagNodeRecord)
        self.assertEqual(record.kind, "node")
        self.assertEqual(record.role, "router")
        self.assertEqual(record.ext_address, "767f13ec2877d7a9")
        self.assertEqual(record.rloc16, "0x9c00")
        self.assertTrue(record.thread_attached)
        self.assertTrue(record.commissioned)

    def test_parse_thread_diag_node_child(self):
        line = (
            "ThreadDiag: schema=1 serial=BMS-C6Z-5BAFA0 product=ESP32-C6-Zero "
            "role=child ext_address=020bf76b928401cd rloc16=0x8802 thread_attached=1 commissioned=1"
        )
        record = parse_thread_diag_line(line)
        self.assertIsInstance(record, ThreadDiagNodeRecord)
        assert isinstance(record, ThreadDiagNodeRecord)
        self.assertEqual(record.serial, "BMS-C6Z-5BAFA0")
        self.assertEqual(record.product, "ESP32-C6-Zero")
        self.assertEqual(record.role, "child")
        self.assertEqual(record.ext_address, "020bf76b928401cd")
        self.assertEqual(record.rloc16, "0x8802")

    def test_parse_thread_diag_parent_current_schema(self):
        line = (
            "ThreadDiagParent: schema=1 serial=BMS-C6Z-5D062C product=ESP32-C6-Zero "
            "source=reported-by-node parent_ext_address=a670a314caa9803d parent_rloc16=0x8800 "
            "link_quality_in=3 link_quality_out=3 parent_avg_rssi_dbm=-48 parent_last_rssi_dbm=-47"
        )
        record = parse_thread_diag_line(line)
        self.assertIsInstance(record, ThreadDiagParentRecord)
        assert isinstance(record, ThreadDiagParentRecord)
        self.assertEqual(record.kind, "parent")
        self.assertEqual(record.source, "reported-by-node")
        self.assertEqual(record.parent_ext_address, "a670a314caa9803d")
        self.assertEqual(record.parent_rloc16, "0x8800")
        self.assertEqual(record.parent_avg_rssi_dbm, -48.0)
        self.assertEqual(record.parent_last_rssi_dbm, -47.0)

    def test_parse_thread_diag_parent_legacy_parent_rssi_alias(self):
        line = (
            "ThreadDiagParent: schema=1 serial=BMS-C6P-53AC5C product=ESP32-C6-Pico "
            "source=reported-by-node parent_ext_address=8e2a222e4771cacc parent_rloc16=0xb800 "
            "link_quality_in=3 link_quality_out=3 parent_rssi_dbm=-42"
        )
        record = parse_thread_diag_line(line)
        self.assertIsInstance(record, ThreadDiagParentRecord)
        assert isinstance(record, ThreadDiagParentRecord)
        self.assertEqual(record.parent_avg_rssi_dbm, -42.0)
        self.assertEqual(record.parent_last_rssi_dbm, -42.0)

    def test_parse_thread_diag_neighbor(self):
        line = (
            "ThreadDiagNeighbor: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
            "source=observed-by-parent ext_address=767f13ec2877d7a9 rloc16=0x9c00 role=router "
            "is_child=0 full_thread_device=1 link_quality_in=3 link_quality_out=-1 "
            "avg_rssi_dbm=-56 last_rssi_dbm=-57 age_s=9"
        )
        record = parse_thread_diag_line(line)
        self.assertIsInstance(record, ThreadDiagNeighborRecord)
        assert isinstance(record, ThreadDiagNeighborRecord)
        self.assertEqual(record.kind, "neighbor")
        self.assertEqual(record.source, "observed-by-parent")
        self.assertEqual(record.role, "router")
        self.assertFalse(record.is_child)
        self.assertTrue(record.full_thread_device)
        self.assertEqual(record.link_quality_out, -1)
        self.assertEqual(record.avg_rssi_dbm, -56.0)
        self.assertEqual(record.age_s, 9)

    def test_parse_unknown_line_returns_none(self):
        self.assertIsNone(parse_thread_diag_line("SomeOtherPrefix: schema=1 serial=abc"))

    def test_parse_invalid_schema_raises(self):
        with self.assertRaises(ValueError):
            parse_thread_diag_line(
                "ThreadDiag: schema=2 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
                "role=router ext_address=767f13ec2877d7a9 rloc16=0x9c00 thread_attached=1 commissioned=1"
            )

    def test_parse_invalid_ext_address_raises(self):
        with self.assertRaises(ValueError):
            parse_thread_diag_line(
                "ThreadDiag: schema=1 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
                "role=router ext_address=bad rloc16=0x9c00 thread_attached=1 commissioned=1"
            )

    def test_parse_batch_collects_records_and_errors(self):
        lines = [
            (
                "ThreadDiag: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
                "role=leader ext_address=a670a314caa9803d rloc16=0x8800 thread_attached=1 commissioned=1"
            ),
            "garbage line",
            (
                "ThreadDiagNeighbor: schema=1 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
                "source=observed-by-parent ext_address=9e7867e18d52c267 rloc16=0xb802 role=child "
                "is_child=1 full_thread_device=0 link_quality_in=3 link_quality_out=-1 "
                "avg_rssi_dbm=-12 last_rssi_dbm=-12 age_s=4"
            ),
        ]
        records, errors = parse_thread_diag_lines(lines)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error"], "unsupported line")

    def test_record_to_dict(self):
        line = (
            "ThreadDiag: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
            "role=leader ext_address=a670a314caa9803d rloc16=0x8800 thread_attached=1 commissioned=1"
        )
        record = parse_thread_diag_line(line)
        assert isinstance(record, ThreadDiagNodeRecord)
        data = record_to_dict(record)
        self.assertEqual(data["kind"], "node")
        self.assertEqual(data["role"], "leader")


if __name__ == "__main__":
    unittest.main()
