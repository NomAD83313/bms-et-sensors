import unittest

from app.matter.matter_payload import extract_event


class MatterPayloadTests(unittest.TestCase):
    def test_extract_event_collects_tags_and_numeric_fields(self):
        payload = {
            "event_type": "attribute_updated",
            "node_id": 12,
            "endpoint_id": 1,
            "data": {
                "temperature_c": 24.5,
                "humidity_pct": "43.2",
                "battery": {"percent": 97},
            },
        }

        event = extract_event(payload)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["event_type"], "attribute_updated")
        self.assertEqual(event["tags"]["node_id"], "12")
        self.assertEqual(event["tags"]["endpoint_id"], "1")
        self.assertEqual(event["fields"]["data_temperature_c"], 24.5)
        self.assertEqual(event["fields"]["data_humidity_pct"], 43.2)
        self.assertEqual(event["fields"]["data_battery_percent"], 97.0)

    def test_extract_event_ignores_non_numeric_values(self):
        payload = {
            "type": "message",
            "connected": True,
            "meta": {"name": "sensor-a", "status": "ok"},
            "readings": {"temp": "x", "humidity": "bad"},
        }

        event = extract_event(payload)
        self.assertIsNone(event)

    def test_extract_event_uses_unknown_event_type_when_missing(self):
        payload = {"node": "abc", "value": 10}
        event = extract_event(payload)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["event_type"], "unknown")
        self.assertEqual(event["tags"]["node_id"], "abc")
        self.assertEqual(event["fields"]["value"], 10.0)


class MatterPayloadAttributeUpdatedTests(unittest.TestCase):
    """Tests for python-matter-server attribute_updated format: [node_id, "ep/cluster/attr", value]."""

    def test_simple_numeric_value_sets_proper_tags(self):
        payload = {
            "event": "attribute_updated",
            "event_type": "attribute_updated",
            "data": [17, "2/1026/0", 3800],
        }
        event = extract_event(payload)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["event_type"], "attribute_updated")
        self.assertEqual(event["tags"]["node_id"], "17")
        self.assertEqual(event["tags"]["endpoint_id"], "2")
        self.assertEqual(event["tags"]["cluster_id"], "1026")
        self.assertEqual(event["tags"]["attribute_id"], "0")
        self.assertEqual(event["fields"]["value"], 3800.0)

    def test_struct_value_walks_numeric_leaves(self):
        payload = {
            "event": "attribute_updated",
            "event_type": "attribute_updated",
            "data": [17, "0/40/0", {"revision": 1, "version": 5}],
        }
        event = extract_event(payload)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["tags"]["node_id"], "17")
        self.assertEqual(event["tags"]["cluster_id"], "40")
        self.assertIn("revision", event["fields"])
        self.assertIn("version", event["fields"])

    def test_non_list_data_falls_through_to_generic_walk(self):
        payload = {
            "event_type": "attribute_updated",
            "node_id": 12,
            "endpoint_id": 1,
            "data": {"temperature_c": 24.5},
        }
        event = extract_event(payload)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["tags"]["node_id"], "12")
        self.assertIn("data_temperature_c", event["fields"])

    def test_invalid_attribute_path_falls_through(self):
        payload = {
            "event": "attribute_updated",
            "event_type": "attribute_updated",
            "data": [17, "bad-path", 100],
        }
        event = extract_event(payload)
        # falls through to generic walk, finds numeric values
        self.assertIsNotNone(event)

    def test_non_numeric_string_value_returns_none(self):
        payload = {
            "event": "attribute_updated",
            "event_type": "attribute_updated",
            "data": [17, "1/6/0", "on"],
        }
        event = extract_event(payload)
        # format recognized, but "on" is not numeric → None, no fallthrough
        self.assertIsNone(event)

    def test_boolean_value_stored_as_0_1(self):
        for raw, expected in [(True, 1.0), (False, 0.0)]:
            with self.subTest(raw=raw):
                payload = {
                    "event": "attribute_updated",
                    "event_type": "attribute_updated",
                    "data": [17, "1/6/0", raw],
                }
                event = extract_event(payload)
                self.assertIsNotNone(event)
                assert event is not None
                self.assertEqual(event["tags"]["node_id"], "17")
                self.assertEqual(event["tags"]["endpoint_id"], "1")
                self.assertEqual(event["tags"]["cluster_id"], "6")
                self.assertEqual(event["fields"]["value"], expected)


if __name__ == "__main__":
    unittest.main()

