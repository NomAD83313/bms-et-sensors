import unittest

from app.matter.thread_topology import build_thread_topology


class ThreadTopologyTests(unittest.TestCase):
    def test_wifi_matter_nodes_do_not_get_thread_address_conflict_badge_data(self) -> None:
        topology = build_thread_topology(
            [
                {
                    "node_id": 27,
                    "serial_number": "BMS-CAM-9A9324",
                    "product_name": "ESP32-S-CAM",
                    "available": True,
                    "network_type": "WiFi",
                    "air_reboot_supported": True,
                    "uptime_sec": 42,
                    "estimated_last_boot_at": "2026-05-06T07:00:00Z",
                    "thread_role": None,
                    "ext_address": None,
                    "rloc16": None,
                }
            ],
            {},
            {"available": True, "settings": {}},
        )

        node = next(item for item in topology["matter_inventory"] if item.get("matter_node_id") == 27)
        self.assertIsNone(node["address_trusted"])
        self.assertIsNone(node["reported_ext_address"])
        self.assertIsNone(node["reported_rloc16"])
        self.assertTrue(node["air_reboot_supported"])
        self.assertEqual(node["uptime_sec"], 42)
        self.assertEqual(node["estimated_last_boot_at"], "2026-05-06T07:00:00Z")
        self.assertNotIn("address_conflicts", node)
        self.assertEqual(topology["warnings"], [])

    def test_matches_only_by_exact_ext_address(self) -> None:
        matter_nodes = [
            {
                "node_id": 8,
                "serial_number": "BMS-C6Z-5D062C",
                "product_name": "ESP32-C6-Zero",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "8ad02a2e024dd85e",
                "rloc16": "0xbc99",
            }
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "child_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "Extended MAC"],
                        "rows": [["0xbc03", "3", "8ad02a2e024dd85e"]],
                    }
                },
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [],
                                "children": [{"rloc16": "0xbc03", "link_quality_in": 3, "mode": "r"}],
                            }
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        matched = next(node for node in topology["nodes"] if node.get("matter_node_id") == 8)
        self.assertTrue(matched["matched"])
        self.assertEqual(matched["match_rule"], "exact_ext_address")
        self.assertEqual(matched["rloc16"], "0xbc03")
        child = topology["tree"]["groups"][0]["children"][0]["child"]
        self.assertEqual(child["label"], "BMS-C6Z-5D062C")
        self.assertEqual(child["matter_node_id"], 8)

    def test_does_not_match_by_rloc_when_ext_differs(self) -> None:
        matter_nodes = [
            {
                "node_id": 8,
                "serial_number": "BMS-C6Z-5D062C",
                "product_name": "ESP32-C6-Zero",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "8ad02a2e024dd85e",
                "rloc16": "0xac01",
            }
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "child_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "Extended MAC"],
                        "rows": [["0xac01", "3", "a2c99394106019e8"]],
                    }
                },
            },
            "meshdiag": {"topology_children": {"parsed": {"routers": []}}},
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        matter_node = next(node for node in topology["nodes"] if node.get("matter_node_id") == 8)
        otbr_node = next(node for node in topology["nodes"] if node.get("ext_address") == "a2c99394106019e8")
        self.assertFalse(matter_node["matched"])
        self.assertFalse(otbr_node["matched"])
        self.assertTrue(any(warning["type"] == "matter_node_unmatched_in_otbr" for warning in topology["warnings"]))
        self.assertTrue(any(warning["type"] == "otbr_node_unmatched_in_matter" for warning in topology["warnings"]))

    def test_quarantines_duplicate_available_matter_ext_addresses(self) -> None:
        matter_nodes = [
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
            },
            {
                "node_id": 11,
                "serial_number": "BMS-C6DK-499B30",
                "available": True,
                "network_type": "Thread",
                "thread_role": "reed",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
            },
        ]

        topology = build_thread_topology(matter_nodes, {}, {"available": True, "settings": {}})

        inventory_by_id = {node["matter_node_id"]: node for node in topology["matter_inventory"]}
        self.assertFalse(inventory_by_id[10]["address_trusted"])
        self.assertFalse(inventory_by_id[11]["address_trusted"])
        self.assertIsNone(inventory_by_id[10]["ext_address"])
        self.assertIsNone(inventory_by_id[11]["ext_address"])
        self.assertEqual(topology["counters"]["trusted_matter_addresses"], 0)
        self.assertEqual(len([warning for warning in topology["warnings"] if warning["type"] == "matter_thread_address_conflict"]), 2)

    def test_keeps_border_router_upstream_link_without_identity_match(self) -> None:
        matter_nodes = []
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "neighbor_table": {
                    "parsed": {
                        "headers": ["Role", "RLOC16", "Age", "Avg RSSI", "Last RSSI", "Extended MAC"],
                        "rows": [["R", "0xbc00", "7", "-52", "-53", "3e67a1116a46233e"]],
                    }
                },
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [47],
                                "children": [],
                            },
                            {
                                "id": 47,
                                "rloc16": "0xbc00",
                                "ext_address": "3e67a1116a46233e",
                                "flags": [],
                                "links": [43],
                                "children": [{"rloc16": "0xbc03", "link_quality_in": 3, "mode": "r"}],
                            },
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        router_group = next(group for group in topology["tree"]["groups"] if group["parent"]["ext_address"] == "3e67a1116a46233e")
        self.assertEqual(router_group["upstream"]["node"]["label"], "OTBR / RCP")
        self.assertEqual(router_group["upstream"]["relation"], "border_router_neighbor")
        self.assertEqual(router_group["upstream"]["match_rule"], "otbr_neighbor_edge_only")
        self.assertEqual(router_group["upstream"]["average_rssi_dbm"], -52)
        self.assertEqual(router_group["upstream"]["last_rssi_dbm"], -53)
        self.assertFalse(router_group["parent"]["matched"])

    def test_places_direct_otbr_router_neighbor_in_tree_without_children(self) -> None:
        matter_nodes = [
            {
                "node_id": 37,
                "serial_number": "BMS-C6D-499B30",
                "product_name": "ESP32-C6-DevKitC",
                "available": True,
                "network_type": "Thread",
                "thread_role": "reed",
                "ext_address": "56158a1dc0b7b2a4",
                "rloc16": "0xf000",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "f2f681f8f84b7710", "rloc16": "0xb400"}],
                    "route_table": [],
                },
            }
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xb400", "extaddr": "f2f681f8f84b7710", "state": "leader"},
            "tables": {
                "router_table": {
                    "parsed": {
                        "headers": ["ID", "RLOC16", "Next Hop", "Path Cost", "LQ In", "LQ Out", "Age", "Extended MAC", "Link"],
                        "rows": [["60", "0xf000", "63", "0", "3", "3", "0", "56158a1dc0b7b2a4", "1"]],
                    }
                },
                "neighbor_table": {
                    "parsed": {
                        "headers": ["Role", "RLOC16", "Age", "Avg RSSI", "Last RSSI", "Extended MAC"],
                        "rows": [["R", "0xf000", "15", "-40", "-41", "56158a1dc0b7b2a4"]],
                    }
                },
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 45,
                                "rloc16": "0xb400",
                                "ext_address": "f2f681f8f84b7710",
                                "flags": ["me", "leader", "br"],
                                "links": [60],
                                "children": [],
                            },
                            {
                                "id": 60,
                                "rloc16": "0xf000",
                                "ext_address": "56158a1dc0b7b2a4",
                                "flags": [],
                                "links": [45],
                                "children": [],
                            },
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        devkit = next(node for node in topology["nodes"] if node.get("matter_node_id") == 37)
        self.assertTrue(devkit["matched"])
        self.assertEqual(devkit["match_rule"], "exact_ext_address")
        otbr_group = next(group for group in topology["tree"]["groups"] if group["parent"]["node_class"] == "otbr")
        neighbor_link = next(item for item in otbr_group["children"] if item["child"].get("matter_node_id") == 37)
        self.assertEqual(neighbor_link["relation"], "neighbor")
        self.assertEqual(neighbor_link["child"]["label"], "BMS-C6D-499B30")
        self.assertEqual(neighbor_link["last_rssi_dbm"], -41)
        self.assertEqual(topology["counters"]["matched_nodes"], 1)
        self.assertEqual(topology["counters"]["tree_relations"], 1)

    def test_infers_rloc_child_with_parent_neighbor_evidence(self) -> None:
        matter_nodes = [
            {
                "node_id": 8,
                "serial_number": "BMS-C6Z-5D062C",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "8ad02a2e024dd85e",
                "rloc16": "0xbc03",
                "thread_diagnostics": {
                    "neighbor_table": [
                        {"ext_address": "3e67a1116a46233e", "rloc16": "0xbc00", "link_quality_in": 3}
                    ],
                    "route_table": [],
                },
            }
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 47,
                                "rloc16": "0xbc00",
                                "ext_address": "3e67a1116a46233e",
                                "flags": [],
                                "links": [],
                                "children": [{"rloc16": "0xbc03", "link_quality_in": 3, "mode": "r"}],
                            }
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        child = topology["tree"]["groups"][0]["children"][0]["child"]
        self.assertEqual(child["label"], "BMS-C6Z-5D062C")
        self.assertTrue(child["matched"])
        self.assertTrue(child["inferred_match"])
        self.assertEqual(child["matched_by"], "same_rloc_and_parent_neighbor_evidence")
        self.assertTrue(child["candidate_match"])
        self.assertEqual(child["matter_node_id"], 8)
        self.assertEqual(child["candidate_matter_node_id"], 8)
        self.assertEqual(child["candidate_rule"], "same_rloc_and_parent_neighbor_evidence")
        matched_node = next(node for node in topology["nodes"] if node.get("matter_node_id") == 8)
        self.assertTrue(matched_node["matched"])
        self.assertTrue(matched_node["inferred_match"])
        self.assertEqual(matched_node["node_class"], "matched_node")
        self.assertEqual(matched_node["match_rule"], "same_rloc_and_parent_neighbor_evidence")
        self.assertEqual(matched_node["ext_address"], "8ad02a2e024dd85e")
        self.assertEqual(matched_node["rloc16"], "0xbc03")
        self.assertEqual(matched_node["otbr_rloc16"], "0xbc03")
        self.assertFalse(any(node.get("matched") is False and node.get("rloc16") == "0xbc03" for node in topology["nodes"]))
        self.assertEqual(topology["counters"]["matched_nodes"], 1)
        self.assertFalse(any(warning["type"] == "matter_node_unmatched_in_otbr" for warning in topology["warnings"]))

    def test_infers_unique_residual_child_under_same_parent(self) -> None:
        matter_nodes = [
            {
                "node_id": 8,
                "serial_number": "BMS-C6Z-5D062C",
                "product_name": "ESP32-C6-Zero",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "8ad02a2e024dd85e",
                "rloc16": "0xac01",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "5a1b48b61db2c630", "rloc16": "0xac00"}],
                    "route_table": [],
                },
            },
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "product_name": "ESP32-C6-Pico",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "5a1b48b61db2c630", "rloc16": "0xac00"}],
                    "route_table": [],
                },
            },
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "child_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "Extended MAC"],
                        "rows": [
                            ["0xac01", "3", "8ad02a2e024dd85e"],
                            ["0xac02", "3", "a2c99394106019e8"],
                        ],
                    }
                },
                "neighbor_table": {
                    "parsed": {
                        "headers": ["Role", "RLOC16", "Age", "Avg RSSI", "Last RSSI", "Extended MAC"],
                        "rows": [
                            ["C", "0xac01", "77", "-56", "-57", "8ad02a2e024dd85e"],
                            ["C", "0xac02", "77", "-46", "-41", "a2c99394106019e8"],
                        ],
                    }
                },
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [],
                                "children": [
                                    {"rloc16": "0xac01", "link_quality_in": 3, "mode": "r"},
                                    {"rloc16": "0xac02", "link_quality_in": 3, "mode": "r"},
                                ],
                            }
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        pico_child = next(
            item
            for item in topology["tree"]["groups"][0]["children"]
            if item["child"]["rloc16"] == "0xac02"
        )
        self.assertEqual(pico_child["child"]["label"], "BMS-C6P-53AC5C")
        self.assertEqual(pico_child["child"]["matter_node_id"], 10)
        self.assertTrue(pico_child["child"]["matched"])
        self.assertTrue(pico_child["child"]["inferred_match"])
        self.assertEqual(pico_child["child"]["matched_by"], "unique_residual_parent_child")
        self.assertEqual(pico_child["average_rssi_dbm"], -46)
        self.assertEqual(pico_child["last_rssi_dbm"], -41)
        matched_pico = next(node for node in topology["nodes"] if node.get("matter_node_id") == 10)
        self.assertTrue(matched_pico["matched"])
        self.assertTrue(matched_pico["inferred_match"])
        self.assertEqual(matched_pico["node_class"], "matched_node")
        self.assertEqual(matched_pico["match_rule"], "unique_residual_parent_child")
        self.assertEqual(matched_pico["ext_address"], "a2c99394106019e8")
        self.assertEqual(matched_pico["matter_ext_address"], "eaeacdeeedabe086")
        self.assertEqual(matched_pico["otbr_ext_address"], "a2c99394106019e8")
        self.assertEqual(topology["counters"]["matched_nodes"], 2)
        self.assertFalse(any(warning["type"] == "matter_node_unmatched_in_otbr" for warning in topology["warnings"]))
        self.assertFalse(
            any(
                warning["type"] == "otbr_node_unmatched_in_matter"
                and warning.get("ext_address") == "a2c99394106019e8"
                for warning in topology["warnings"]
            )
        )

    def test_infers_router_from_neighbor_set_even_when_reported_ext_is_quarantined(self) -> None:
        matter_nodes = [
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
            },
            {
                "node_id": 13,
                "serial_number": "BMS-C6D-499B30",
                "available": True,
                "network_type": "Thread",
                "thread_role": "router",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [
                        {"ext_address": "5a1b48b61db2c630", "rloc16": "0xac00"},
                        {"ext_address": "7a44d6c3808e6c5a", "rloc16": "0x0400"},
                        {
                            "ext_address": "8ad02a2e024dd85e",
                            "rloc16": "0x0409",
                            "average_rssi_dbm": -18,
                            "last_rssi_dbm": -17,
                            "link_quality_in": 3,
                            "is_child": True,
                        },
                    ],
                    "route_table": [],
                },
            },
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "LQ Out", "Extended MAC"],
                        "rows": [["0x0400", "3", "3", "7a44d6c3808e6c5a"]],
                    }
                },
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [1],
                                "children": [],
                            },
                            {
                                "id": 1,
                                "rloc16": "0x0400",
                                "ext_address": "7a44d6c3808e6c5a",
                                "flags": [],
                                "links": [43],
                                "children": [{"rloc16": "0x0409", "link_quality_in": 3, "mode": "r"}],
                            },
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        router = next(node for node in topology["nodes"] if node.get("matter_node_id") == 13)
        self.assertTrue(router["matched"])
        self.assertTrue(router["inferred_match"])
        self.assertEqual(router["serial_number"], "BMS-C6D-499B30")
        self.assertEqual(router["ext_address"], "7a44d6c3808e6c5a")
        self.assertEqual(router["role"], "router")
        self.assertEqual(router["reported_ext_address"], "eaeacdeeedabe086")
        self.assertEqual(router["matched_by"], "router_neighbor_set_evidence")
        router_group = next(group for group in topology["tree"]["groups"] if group["parent"]["matter_node_id"] == 13)
        self.assertEqual(router_group["parent"]["label"], "BMS-C6D-499B30")
        router_child = router_group["children"][0]
        self.assertEqual(router_child["average_rssi_dbm"], -18)
        self.assertEqual(router_child["last_rssi_dbm"], -17)
        self.assertEqual(router_child["rssi_source"], "matter_neighbor_table")
        self.assertEqual(router_child["rssi_observer_label"], "BMS-C6D-499B30")
        self.assertFalse(
            any(
                warning["type"] == "otbr_node_unmatched_in_matter"
                and warning.get("ext_address") == "7a44d6c3808e6c5a"
                for warning in topology["warnings"]
            )
        )

    def test_infers_quarantined_child_from_unique_parent_child_evidence(self) -> None:
        matter_nodes = [
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "5a1b48b61db2c630", "rloc16": "0xac00"}],
                    "route_table": [],
                },
            },
            {
                "node_id": 13,
                "serial_number": "BMS-C6D-499B30",
                "available": True,
                "network_type": "Thread",
                "thread_role": "router",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
            },
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {"parsed": {"headers": [], "rows": []}},
                "child_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "Extended MAC"],
                        "rows": [["0xac01", "3", "a2c99394106019e8"]],
                    }
                },
                "neighbor_table": {
                    "parsed": {
                        "headers": ["Role", "RLOC16", "Age", "Avg RSSI", "Last RSSI", "Extended MAC"],
                        "rows": [["C", "0xac01", "77", "-36", "-36", "a2c99394106019e8"]],
                    }
                },
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [],
                                "children": [{"rloc16": "0xac01", "link_quality_in": 3, "mode": "r"}],
                            }
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        child = next(node for node in topology["nodes"] if node.get("matter_node_id") == 10)
        self.assertTrue(child["matched"])
        self.assertTrue(child["inferred_match"])
        self.assertEqual(child["serial_number"], "BMS-C6P-53AC5C")
        self.assertEqual(child["ext_address"], "a2c99394106019e8")
        self.assertEqual(child["reported_ext_address"], "eaeacdeeedabe086")
        self.assertEqual(child["matched_by"], "quarantined_child_parent_evidence")
        tree_child = topology["tree"]["groups"][0]["children"][0]["child"]
        self.assertEqual(tree_child["matter_node_id"], 10)
        self.assertEqual(tree_child["label"], "BMS-C6P-53AC5C")
        self.assertFalse(
            any(
                warning["type"] == "otbr_node_unmatched_in_matter"
                and warning.get("ext_address") == "a2c99394106019e8"
                for warning in topology["warnings"]
            )
        )

    def test_infers_quarantined_child_after_trusted_sibling_rloc_children_are_removed(self) -> None:
        matter_nodes = [
            {
                "node_id": 7,
                "serial_number": "BMS-C6Z-5D06E8",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "96a85f6951924c10",
                "rloc16": "0x0403",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "7a44d6c3808e6c5a", "rloc16": "0x0400"}],
                    "route_table": [],
                },
            },
            {
                "node_id": 9,
                "serial_number": "BMS-C6Z-5BAFA0",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "fa9a89c68007086f",
                "rloc16": "0x0402",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "7a44d6c3808e6c5a", "rloc16": "0x0400"}],
                    "route_table": [],
                },
            },
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "7a44d6c3808e6c5a", "rloc16": "0x0400"}],
                    "route_table": [],
                },
            },
            {
                "node_id": 13,
                "serial_number": "BMS-C6D-499B30",
                "available": True,
                "network_type": "Thread",
                "thread_role": "router",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [
                        {"ext_address": "7a44d6c3808e6c5a", "rloc16": "0x0400"},
                        {"ext_address": "96a85f6951924c10", "rloc16": "0x0403"},
                        {"ext_address": "fa9a89c68007086f", "rloc16": "0x0402"},
                    ],
                    "route_table": [],
                },
            },
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "leader"},
            "tables": {
                "router_table": {
                    "parsed": {
                        "headers": ["RLOC16", "LQ In", "LQ Out", "Extended MAC"],
                        "rows": [["0x0400", "3", "3", "7a44d6c3808e6c5a"]],
                    }
                },
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "leader", "br"],
                                "links": [1],
                                "children": [],
                            },
                            {
                                "id": 1,
                                "rloc16": "0x0400",
                                "ext_address": "7a44d6c3808e6c5a",
                                "flags": [],
                                "links": [43],
                                "children": [
                                    {"rloc16": "0x040d", "link_quality_in": 3, "mode": "r"},
                                    {"rloc16": "0x0402", "link_quality_in": 3, "mode": "r"},
                                    {"rloc16": "0x0403", "link_quality_in": 3, "mode": "r"},
                                ],
                            },
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        pico = next(node for node in topology["nodes"] if node.get("matter_node_id") == 10)
        self.assertTrue(pico["matched"])
        self.assertEqual(pico["serial_number"], "BMS-C6P-53AC5C")
        self.assertEqual(pico["rloc16"], "0x040d")
        self.assertEqual(pico["matched_by"], "quarantined_child_parent_evidence")

    def test_infers_trusted_child_from_unique_rloc_only_parent_child_evidence(self) -> None:
        matter_nodes = [
            {
                "node_id": 10,
                "serial_number": "BMS-C6P-53AC5C",
                "product_name": "ESP32-C6-Pico",
                "available": True,
                "network_type": "Thread",
                "thread_role": "child",
                "ext_address": "eaeacdeeedabe086",
                "rloc16": "0xe086",
                "thread_diagnostics": {
                    "neighbor_table": [{"ext_address": "0259a5e541d506a0", "rloc16": "0xb000"}],
                    "route_table": [{"ext_address": "0259a5e541d506a0", "rloc16": "0xb000"}],
                },
            },
            {
                "node_id": 15,
                "serial_number": "BMS-C6DK-499B30",
                "product_name": "ESP32-C6-DevKitC",
                "available": True,
                "network_type": "Thread",
                "thread_role": "leader",
                "ext_address": "0259a5e541d506a0",
                "rloc16": "0xb000",
                "thread_diagnostics": {
                    "neighbor_table": [
                        {
                            "ext_address": "a2c99394106019e8",
                            "rloc16": "0xb002",
                            "average_rssi_dbm": -41,
                            "last_rssi_dbm": -41,
                            "link_quality_in": 3,
                            "is_child": True,
                        }
                    ],
                    "route_table": [],
                },
            },
        ]
        otbr_diag_snapshot = {
            "available": True,
            "settings": {"rloc16": "0xac00", "extaddr": "5a1b48b61db2c630", "state": "router"},
            "tables": {
                "router_table": {
                    "parsed": {
                        "headers": ["ID", "RLOC16", "Next Hop", "Path Cost", "LQ In", "LQ Out", "Age", "Extended MAC", "Link"],
                        "rows": [["44", "0xb000", "63", "0", "3", "3", "1", "0259a5e541d506a0", "1"]],
                    }
                },
                "neighbor_table": {
                    "parsed": {
                        "headers": ["Role", "RLOC16", "Age", "Avg RSSI", "Last RSSI", "Extended MAC"],
                        "rows": [["R", "0xb000", "1", "-14", "-15", "0259a5e541d506a0"]],
                    }
                },
                "child_table": {"parsed": {"headers": [], "rows": []}},
            },
            "meshdiag": {
                "topology_children": {
                    "parsed": {
                        "routers": [
                            {
                                "id": 43,
                                "rloc16": "0xac00",
                                "ext_address": "5a1b48b61db2c630",
                                "flags": ["me", "br"],
                                "links": [44],
                                "children": [],
                            },
                            {
                                "id": 44,
                                "rloc16": "0xb000",
                                "ext_address": "0259a5e541d506a0",
                                "flags": ["leader"],
                                "links": [43],
                                "children": [{"rloc16": "0xb002", "link_quality_in": 3, "mode": "r"}],
                            },
                        ]
                    }
                }
            },
        }

        topology = build_thread_topology(matter_nodes, {}, otbr_diag_snapshot)

        pico = next(node for node in topology["nodes"] if node.get("matter_node_id") == 10)
        self.assertTrue(pico["matched"])
        self.assertTrue(pico["inferred_match"])
        self.assertEqual(pico["rloc16"], "0xb002")
        self.assertEqual(pico["matter_ext_address"], "eaeacdeeedabe086")
        self.assertEqual(pico["matched_by"], "unique_residual_parent_child")
        self.assertFalse(any(warning["type"] == "matter_node_unmatched_in_otbr" for warning in topology["warnings"]))


if __name__ == "__main__":
    unittest.main()
