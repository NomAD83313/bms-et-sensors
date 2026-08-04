import json
import unittest

from app.matter.matter_nodes import _overlay_thread_attributes, _refresh_thread_snapshots, normalize_matter_node_payload


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if not self.messages:
            raise TimeoutError("no more websocket messages")
        return json.dumps(self.messages.pop(0))


class MatterNodeNormalizationTests(unittest.TestCase):
    def test_normalize_payload_extracts_thread_diagnostics(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 7,
                    "available": True,
                    "last_interview": 1710000000,
                    "attributes": {
                        "0/40/1": "BMS DOA",
                        "0/40/3": "ESP32-C6-DevKitC",
                        "0/40/15": "BMS-C6DK-499B30",
                        "0/49/65532": 0x02,
                        "0/53/0": 15,
                        "0/53/1": 5,
                        "0/53/63": "B60D8C1122334455",
                        "0/53/64": 0x4400,
                        "0/53/7": [
                            {
                                "ExtAddress": "AA77BB8899001122",
                                "Rloc16": "0x2400",
                                "LinkQualityIn": 3,
                                "AverageRssi": -49,
                                "LastRssi": -50,
                                "Age": 8,
                            }
                        ],
                        "0/53/8": [
                            {
                                "ExtAddress": "CC11DD2233445566",
                                "Rloc16": 0x3400,
                                "NextHop": 34,
                                "PathCost": 1,
                                "LQIIn": 3,
                                "LQIOut": 3,
                                "Age": 4,
                            }
                        ],
                    },
                }
            ]
        )

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node["network_type"], "Thread")
        self.assertEqual(node["thread_role"], "router")
        self.assertEqual(node["ext_address"], "b60d8c1122334455")
        self.assertEqual(node["rloc16"], "0x4400")
        self.assertEqual(node["thread_diagnostics"]["neighbor_count"], 1)
        self.assertEqual(node["thread_diagnostics"]["route_count"], 1)
        self.assertEqual(node["thread_diagnostics"]["neighbor_table"][0]["ext_address"], "aa77bb8899001122")
        self.assertEqual(node["thread_diagnostics"]["route_table"][0]["rloc16"], "0x3400")

    def test_normalize_payload_extracts_standard_controls(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 27,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-S-CAM",
                        "0/40/15": "BMS-CAM-9A9324",
                        "0/49/65532": 0x01,
                        "1/3/65529": [0],
                        "1/6/0": False,
                        "1/6/65529": [0, 1, 2],
                    },
                }
            ]
        )

        self.assertEqual(nodes[0]["standard_controls"], [
            {
                "endpoint_id": 1,
                "cluster_id": 6,
                "cluster_name": "OnOff",
                "commands": ["Off", "On", "Toggle"],
                "on": False,
            },
            {
                "endpoint_id": 1,
                "cluster_id": 3,
                "cluster_name": "Identify",
                "commands": ["Identify"],
            },
        ])

    def test_normalize_payload_extracts_c3_air_reboot_capability(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 4,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C3-SuperMini",
                        "0/49/65532": 0x01,
                        "0/51/8": True,
                        "0/51/65529": [0, 1],
                    },
                }
            ]
        )

        self.assertTrue(nodes[0]["air_reboot_supported"])

    def test_normalize_payload_extracts_c6_multinode_air_reboot_capability(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 8,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C6-Zero Multinode",
                        "0/49/65532": 0x02,
                        "0/51/8": True,
                        "0/51/65529": [0, 1],
                    },
                }
            ]
        )

        self.assertTrue(nodes[0]["air_reboot_supported"])

    def test_normalize_payload_extracts_c6_pico_air_reboot_capability(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 6,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C6-Pico",
                        "0/51/8": True,
                        "0/51/65529": [0, 1],
                    },
                }
            ]
        )

        self.assertTrue(nodes[0]["air_reboot_supported"])

    def test_normalize_payload_extracts_c6_devkitc_air_reboot_capability(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 37,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C6-DevKitC",
                        "0/51/8": True,
                        "0/51/65529": [0, 1],
                    },
                }
            ]
        )

        self.assertTrue(nodes[0]["air_reboot_supported"])

    def test_normalize_payload_extracts_general_diagnostics_runtime(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 4,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C3-SuperMini",
                        "0/51/1": 3,
                        "0/51/2": 125,
                        "0/51/3": 12,
                        "0/51/4": 6,
                    },
                }
            ]
        )

        self.assertEqual(nodes[0]["reboot_count"], 3)
        self.assertEqual(nodes[0]["uptime_sec"], 125)
        self.assertEqual(nodes[0]["total_operational_hours"], 12)
        self.assertEqual(nodes[0]["boot_reason"], 6)
        self.assertEqual(nodes[0]["boot_reason_label"], "software-reset")
        self.assertIsNotNone(nodes[0]["diagnostics_observed_at"])
        self.assertIsNotNone(nodes[0]["estimated_last_boot_at"])

    def test_normalize_payload_does_not_advertise_air_reboot_when_disabled(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 4,
                    "available": True,
                    "attributes": {
                        "0/40/3": "ESP32-C3-SuperMini",
                        "0/49/65532": 0x01,
                        "0/51/8": False,
                        "0/51/65529": [0, 1],
                    },
                }
            ]
        )

        self.assertFalse(nodes[0]["air_reboot_supported"])

    def test_normalize_payload_extracts_numeric_thread_diagnostics_fields(self):
        nodes = normalize_matter_node_payload(
            [
                {
                    "node_id": 7,
                    "available": True,
                    "last_interview": 1710000000,
                    "attributes": {
                        "0/40/1": "BMS DOA",
                        "0/40/3": "ESP32-C6-Zero",
                        "0/40/15": "BMS-C6Z-5D06E8",
                        "0/49/65532": 0x02,
                        "0/53/0": 20,
                        "0/53/1": 3,
                        "0/53/63": 10856031807721327632,
                        "0/53/64": 53252,
                        "0/53/7": [
                            {
                                "0": 13759774649136931311,
                                "1": 14,
                                "2": 53248,
                                "5": 3,
                                "6": -18,
                                "7": -19,
                                "8": 0,
                                "9": 0,
                                "10": True,
                                "11": True,
                                "12": True,
                                "13": False,
                            }
                        ],
                        "0/53/8": [
                            {
                                "0": 13759774649136931311,
                                "1": 53248,
                                "2": 52,
                                "3": 0,
                                "4": 0,
                                "5": 3,
                                "6": 3,
                                "7": 14,
                                "8": True,
                                "9": True,
                            }
                        ],
                    },
                }
            ]
        )

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node["thread_role"], "child")
        self.assertEqual(node["ext_address"], "96a85f6951924c10")
        self.assertEqual(node["rloc16"], "0xd004")
        neighbor = node["thread_diagnostics"]["neighbor_table"][0]
        self.assertEqual(neighbor["ext_address"], "bef48a24173b61ef")
        self.assertEqual(neighbor["rloc16"], "0xd000")
        self.assertEqual(neighbor["link_quality_in"], 3)
        self.assertEqual(neighbor["average_rssi_dbm"], -18)
        self.assertEqual(neighbor["last_rssi_dbm"], -19)
        self.assertFalse(neighbor["is_child"])
        route = node["thread_diagnostics"]["route_table"][0]
        self.assertEqual(route["ext_address"], "bef48a24173b61ef")
        self.assertEqual(route["rloc16"], "0xd000")
        self.assertEqual(route["router_id"], 52)
        self.assertEqual(route["next_hop"], 0)
        self.assertEqual(route["path_cost"], 0)
        self.assertEqual(route["link_quality_in"], 3)
        self.assertEqual(route["link_quality_out"], 3)
        self.assertEqual(route["age_sec"], 14)

    def test_overlay_thread_attributes_refreshes_stale_snapshot_values(self):
        base = {
            "node_id": 8,
            "available": True,
            "network_type": "Thread",
            "ext_address": "8ad02a2e024dd85e",
            "rloc16": "0xac03",
            "thread_diagnostics": {
                "neighbor_table": [],
                "route_table": [],
                "neighbor_count": 0,
                "route_count": 0,
            },
        }

        refreshed = _overlay_thread_attributes(
            base,
            {
                "0/53/63": 10002541149485389918,
                "0/53/64": 50180,
                "0/53/7": [
                    {
                        "0": 4496739849122227006,
                        "1": 6,
                        "2": 50176,
                        "5": 3,
                        "6": -30,
                        "7": -30,
                        "8": 0,
                        "9": 0,
                        "10": True,
                        "11": True,
                        "12": True,
                        "13": False,
                    }
                ],
                "0/53/8": [
                    {
                        "0": 4496739849122227006,
                        "1": 50176,
                        "2": 49,
                        "3": 0,
                        "4": 0,
                        "5": 3,
                        "6": 3,
                        "7": 6,
                        "8": True,
                        "9": True,
                    }
                ],
            },
        )

        self.assertEqual(refreshed["ext_address"], "8ad02a2e024dd85e")
        self.assertEqual(refreshed["rloc16"], "0xc404")
        self.assertEqual(refreshed["thread_diagnostics"]["neighbor_count"], 1)
        self.assertEqual(refreshed["thread_diagnostics"]["neighbor_table"][0]["ext_address"], "3e67a1116a46233e")
        self.assertEqual(refreshed["thread_diagnostics"]["neighbor_table"][0]["rloc16"], "0xc400")
        self.assertEqual(refreshed["thread_diagnostics"]["route_table"][0]["router_id"], 49)

    def test_refresh_thread_snapshots_ignores_interleaved_ws_messages(self):
        ws = FakeWebSocket(
            [
                {"message_id": "999", "result": {"0/53/63": "8ad02a2e024dd85e"}},
                {"event": "node_updated", "result": {"0/53/63": "8ad02a2e024dd85e"}},
                {"message_id": "2", "result": {"0/53/63": "fa9a89c68007086f"}},
                {"message_id": "3", "result": {"0/53/64": 48129}},
                {"message_id": "4", "result": {"0/53/7": []}},
                {"message_id": "5", "result": {"0/53/8": []}},
            ]
        )

        refreshed = _refresh_thread_snapshots(
            ws,
            [
                {
                    "node_id": 9,
                    "serial_number": "BMS-C6Z-5BAFA0",
                    "network_type": "Thread",
                    "ext_address": "fa9a89c68007086f",
                    "rloc16": "0xbc01",
                    "thread_diagnostics": {"neighbor_table": [], "route_table": []},
                }
            ],
        )

        self.assertEqual(refreshed[0]["serial_number"], "BMS-C6Z-5BAFA0")
        self.assertEqual(refreshed[0]["ext_address"], "fa9a89c68007086f")
        self.assertEqual(refreshed[0]["rloc16"], "0xbc01")
        self.assertEqual([item["message_id"] for item in ws.sent], ["2", "3", "4", "5"])


if __name__ == "__main__":
    unittest.main()
