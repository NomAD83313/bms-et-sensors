import unittest
from unittest.mock import Mock

from app.matter.matter_polling import MatterPollingService, PollingConfig


class MatterPollingServiceTests(unittest.TestCase):
    def test_poll_once_writes_battery_and_environment_attribute_records(self) -> None:
        values = {
            "1/513/0": None,
            "1/513/18": None,
            "5/47/11": 4162,
            "5/47/12": 190,
            "5/47/26": 3,
            "1/1026/0": 3155,
            "2/1029/0": 4036,
            "3/1027/0": 100,
            "3/1027/16": 10059,
            "3/1068/0": None,
            "3/1066/0": None,
            "3/1069/0": None,
        }
        records: list[dict] = []
        service = MatterPollingService(
            PollingConfig(interval_sec=60.0, primary_node_id=1, battery_endpoint_id=5),
            read_attribute=lambda _node_id, path: values[path],
            get_node_snapshot=lambda: [{"node_id": 1}],
            record_event=records.append,
            log=Mock(),
        )

        service.poll_once()

        self.assertEqual(len(records), 7)
        self.assertEqual(records[1]["event_type"], "poll_attribute")
        self.assertEqual(records[1]["tags"]["cluster_id"], "47")
        self.assertEqual(records[1]["tags"]["attribute_id"], "12")
        self.assertEqual(records[1]["fields"]["value"], 190.0)
        self.assertEqual(records[3]["tags"]["cluster_id"], "1026")
        self.assertEqual(records[4]["tags"]["cluster_id"], "1029")
        self.assertEqual(records[5]["tags"]["cluster_id"], "1027")
        self.assertEqual(records[6]["tags"]["cluster_id"], "1027")
        self.assertEqual(records[6]["tags"]["attribute_id"], "16")
        self.assertEqual(records[6]["fields"]["value"], 10059.0)

    def test_target_node_ids_include_primary_and_unique_discovered_nodes(self) -> None:
        service = MatterPollingService(
            PollingConfig(interval_sec=60.0, primary_node_id=1, battery_endpoint_id=5),
            read_attribute=Mock(),
            get_node_snapshot=lambda: [{"node_id": 1}, {"node_id": 7}, {"node_id": 7}, {"node_id": 0}],
            record_event=Mock(),
            log=Mock(),
        )

        self.assertEqual(service.target_node_ids(), [1, 7])

    def test_disabled_polling_performs_no_io(self) -> None:
        read_attribute = Mock()
        get_node_snapshot = Mock()
        record_event = Mock()
        service = MatterPollingService(
            PollingConfig(interval_sec=0.0, primary_node_id=1, battery_endpoint_id=5),
            read_attribute=read_attribute,
            get_node_snapshot=get_node_snapshot,
            record_event=record_event,
            log=Mock(),
        )

        service.poll_once()

        read_attribute.assert_not_called()
        get_node_snapshot.assert_not_called()
        record_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
