import unittest
from pathlib import Path
import tempfile

from app.matter.thread_diag import parse_thread_diag_line
from app.matter.thread_diag_store import ThreadDiagStore


class ThreadDiagStoreTests(unittest.TestCase):
    def test_ingest_and_snapshot(self):
        store = ThreadDiagStore()
        records = [
            parse_thread_diag_line(
                "ThreadDiag: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
                "role=leader ext_address=a670a314caa9803d rloc16=0x8800 thread_attached=1 commissioned=1"
            ),
            parse_thread_diag_line(
                "ThreadDiagParent: schema=1 serial=BMS-C6Z-5BAFA0 product=ESP32-C6-Zero "
                "source=reported-by-node parent_ext_address=a670a314caa9803d parent_rloc16=0x8800 "
                "link_quality_in=3 link_quality_out=3 parent_avg_rssi_dbm=-53 parent_last_rssi_dbm=-53"
            ),
            parse_thread_diag_line(
                "ThreadDiagNeighbor: schema=1 serial=BMS-C6DK-499B30 product=ESP32-C6-DevKitC "
                "source=observed-by-parent ext_address=9e7867e18d52c267 rloc16=0xb802 role=child "
                "is_child=1 full_thread_device=0 link_quality_in=3 link_quality_out=-1 "
                "avg_rssi_dbm=-12 last_rssi_dbm=-12 age_s=4"
            ),
        ]
        store.ingest_records([record for record in records if record is not None], observed_at=100.0)
        snap = store.snapshot(now=130.0, stale_after_sec=20.0)
        self.assertEqual(snap["node_count"], 1)
        self.assertEqual(snap["parent_count"], 1)
        self.assertEqual(snap["neighbor_count"], 1)
        self.assertEqual(snap["age_sec"], 30.0)
        self.assertTrue(snap["nodes"][0]["stale"])
        self.assertEqual(snap["parents"][0]["parent_rloc16"], "0x8800")
        self.assertEqual(snap["neighbors"][0]["ext_address"], "9e7867e18d52c267")

    def test_newer_record_overwrites_same_serial(self):
        store = ThreadDiagStore()
        first = parse_thread_diag_line(
            "ThreadDiag: schema=1 serial=BMS-C6Z-5BAFA0 product=ESP32-C6-Zero "
            "role=child ext_address=020bf76b928401cd rloc16=0x8802 thread_attached=1 commissioned=1"
        )
        second = parse_thread_diag_line(
            "ThreadDiag: schema=1 serial=BMS-C6Z-5BAFA0 product=ESP32-C6-Zero "
            "role=child ext_address=020bf76b928401cd rloc16=0x9001 thread_attached=1 commissioned=1"
        )
        assert first is not None
        assert second is not None
        store.ingest_records([first], observed_at=100.0)
        store.ingest_records([second], observed_at=120.0)
        snap = store.snapshot(now=121.0)
        self.assertEqual(snap["node_count"], 1)
        self.assertEqual(snap["nodes"][0]["rloc16"], "0x9001")
        self.assertEqual(snap["nodes"][0]["age_sec"], 1.0)

    def test_store_persists_and_restores_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "thread-diag.json"
            store = ThreadDiagStore(state_file=str(state_path))
            record = parse_thread_diag_line(
                "ThreadDiag: schema=1 serial=BMS-C6DK-48DF4C product=ESP32-C6-DevKitC "
                "role=leader ext_address=a670a314caa9803d rloc16=0x8800 thread_attached=1 commissioned=1"
            )
            assert record is not None
            store.ingest_records([record], observed_at=100.0)
            self.assertTrue(state_path.exists())

            restored = ThreadDiagStore(state_file=str(state_path))
            snap = restored.snapshot(now=105.0)
            self.assertEqual(snap["node_count"], 1)
            self.assertEqual(snap["nodes"][0]["serial"], "BMS-C6DK-48DF4C")
            self.assertEqual(snap["age_sec"], 5.0)


if __name__ == "__main__":
    unittest.main()
