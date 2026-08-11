import json
import unittest
from unittest.mock import Mock

from app.matter.matter_commands import MatterCommandClient, supports_air_reboot, supports_standard_command


class MatterCommandClientTests(unittest.TestCase):
    def test_request_ignores_unrelated_messages_and_closes_connection(self) -> None:
        ws = Mock()
        ws.recv.side_effect = [
            "server-info",
            json.dumps({"message_id": "other", "result": None}),
            json.dumps({"message_id": "123", "result": {"ok": True}}),
        ]
        connection_factory = Mock(return_value=ws)
        client = MatterCommandClient("ws://matter", connection_factory=connection_factory, time_fn=lambda: 1.0)

        result = client.request("device_command", {"node_id": 7}, message_id="123")

        self.assertEqual(result["result"], {"ok": True})
        connection_factory.assert_called_once_with("ws://matter", timeout=8.0)
        self.assertEqual(json.loads(ws.send.call_args.args[0])["command"], "device_command")
        ws.close.assert_called_once_with()

    def test_read_attribute_returns_none_when_transport_fails(self) -> None:
        client = MatterCommandClient("ws://matter", connection_factory=Mock(side_effect=OSError("offline")))

        self.assertIsNone(client.read_attribute(1, "1/513/0"))

    def test_capability_helpers_require_available_advertised_node(self) -> None:
        nodes = [
            {
                "node_id": 7,
                "available": True,
                "air_reboot_supported": True,
                "standard_controls": [{"endpoint_id": 1, "cluster_id": 6, "commands": ["On"]}],
            }
        ]

        self.assertTrue(supports_standard_command(nodes, 7, 1, 6, "On"))
        self.assertFalse(supports_standard_command(nodes, 7, 1, 6, "Off"))
        self.assertTrue(supports_air_reboot(nodes, 7))
        self.assertFalse(supports_air_reboot(nodes, 8))


if __name__ == "__main__":
    unittest.main()
