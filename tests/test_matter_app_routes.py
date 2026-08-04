import unittest
from unittest.mock import patch

try:
    from app.matter import matter_app
except ModuleNotFoundError as exc:
    matter_app = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


@unittest.skipIf(matter_app is None, f"matter_app import unavailable: {_IMPORT_ERROR}")
class MatterAppRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        matter_app._reset_matter_nodes_snapshot_cache()

    def test_openthread_diag_route_returns_local_proxy_payload(self) -> None:
        diag = {
            "available": True,
            "errors": [],
            "settings": {"state": "leader"},
        }

        with patch.object(matter_app, "_fetch_otbr_diag_snapshot", return_value=diag):
            client = matter_app.app.test_client()
            response = client.get("/openthread/diag")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["diag"]["settings"]["state"], "leader")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate, max-age=0")

    def test_topology_route_uses_short_lived_matter_snapshot_cache(self) -> None:
        snapshot = [{"node_id": 7, "serial_number": "BMS-C6Z-5D06E8"}]
        topology = {"nodes": [], "edges": [], "tree": {}, "warnings": []}
        matter_app._last_good_matter_nodes_snapshot = list(snapshot)
        matter_app._last_good_matter_nodes_snapshot_at = matter_app.time.time()

        with (
            patch.object(matter_app, "MATTER_NODE_SNAPSHOT_TTL_SEC", 2.0),
            patch.object(matter_app, "fetch_matter_node_snapshot") as fetch_snapshot,
            patch.object(matter_app._thread_diag_store, "snapshot", return_value={}),
            patch.object(matter_app, "_fetch_otbr_diag_snapshot", return_value={}),
            patch.object(matter_app, "build_thread_topology", return_value=topology),
        ):
            client = matter_app.app.test_client()
            response = client.get("/thread-topology")

        self.assertEqual(response.status_code, 200)
        fetch_snapshot.assert_not_called()
        self.assertEqual(response.get_json()["matter_nodes"], snapshot)

    def test_cold_topology_route_returns_quick_empty_snapshot_while_refresh_starts(self) -> None:
        topology = {"nodes": [], "edges": [], "tree": {}, "warnings": []}

        def mark_snapshot_refresh_started(**_kwargs) -> bool:
            matter_app._matter_nodes_snapshot_refresh_inflight = True
            return True

        with (
            patch.object(matter_app, "_trigger_matter_node_snapshot_refresh", side_effect=mark_snapshot_refresh_started) as trigger_refresh,
            patch.object(matter_app._thread_diag_store, "snapshot", return_value={}),
            patch.object(matter_app, "_fetch_otbr_diag_snapshot", return_value={}),
            patch.object(matter_app, "build_thread_topology", return_value=topology) as build_topology,
        ):
            client = matter_app.app.test_client()
            response = client.get("/thread-topology")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["matter_nodes"], [])
        self.assertTrue(response.get_json()["matter_snapshot_pending"])
        build_topology.assert_called_once()
        trigger_refresh.assert_called_once_with(force=True, blocking=False)

    def test_stale_cached_snapshot_is_returned_while_background_refresh_is_triggered(self) -> None:
        cached_snapshot = [{"node_id": 8, "serial_number": "BMS-C6Z-5D062C"}]
        matter_app._last_good_matter_nodes_snapshot = list(cached_snapshot)
        matter_app._last_good_matter_nodes_snapshot_at = 10.0

        with (
            patch.object(matter_app, "MATTER_NODE_SNAPSHOT_TTL_SEC", 2.0),
            patch.object(matter_app.time, "time", return_value=20.0),
            patch.object(matter_app, "_trigger_matter_node_snapshot_refresh", return_value=True) as trigger_refresh,
        ):
            snapshot = matter_app._get_matter_node_snapshot_cached()

        self.assertEqual(snapshot, cached_snapshot)
        trigger_refresh.assert_called_once_with()

    def test_poll_node_snapshot_writes_battery_and_environment_attribute_records(self) -> None:
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
        }

        with (
            patch.object(matter_app, "MATTER_POLL_INTERVAL_SEC", 60.0),
            patch.object(matter_app, "MATTER_POLL_NODE_ID", 1),
            patch.object(matter_app, "MATTER_POLL_BATTERY_ENDPOINT_ID", 5),
            patch.object(matter_app, "_poll_target_node_ids", return_value=[1]),
            patch.object(matter_app, "_read_attribute_once", side_effect=lambda _node_id, path: values[path]),
            patch.object(matter_app, "_write_event", return_value=True) as write_event,
        ):
            matter_app._poll_node_snapshot_once()

        records = [call.args[0] for call in write_event.call_args_list]
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

    def test_standard_matter_command_route_proxies_advertised_command(self) -> None:
        with (
            patch.object(matter_app, "_node_supports_standard_command", return_value=True) as supports_command,
            patch.object(matter_app, "_matter_ws_request", return_value={"message_id": "1", "result": None}) as ws_request,
            patch.object(matter_app, "_trigger_matter_node_snapshot_refresh") as trigger_refresh,
        ):
            client = matter_app.app.test_client()
            response = client.post(
                "/nodes/27/commands",
                json={"endpoint_id": 1, "cluster_id": 6, "command_name": "On"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        supports_command.assert_called_once_with(27, 1, 6, "On")
        ws_request.assert_called_once_with(
            "device_command",
            {
                "node_id": 27,
                "endpoint_id": 1,
                "cluster_id": 6,
                "command_name": "On",
                "payload": {},
            },
        )
        trigger_refresh.assert_called_once_with(force=True, blocking=False)

    def test_standard_matter_command_route_rejects_unadvertised_command(self) -> None:
        with patch.object(matter_app, "_node_supports_standard_command", return_value=False):
            client = matter_app.app.test_client()
            response = client.post(
                "/nodes/27/commands",
                json={"endpoint_id": 1, "cluster_id": 6, "command_name": "On"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "command_not_advertised_by_node")

    def test_standard_matter_command_route_rejects_non_standard_command(self) -> None:
        client = matter_app.app.test_client()
        response = client.post(
            "/nodes/27/commands",
            json={"endpoint_id": 1, "cluster_id": 999, "command_name": "VendorThing"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unsupported_standard_command")

    def test_air_reboot_route_proxies_bms_test_event_trigger(self) -> None:
        with (
            patch.object(matter_app, "_node_supports_air_reboot", return_value=True) as supports_reboot,
            patch.object(matter_app, "_matter_ws_request", return_value={"message_id": "1", "result": None}) as ws_request,
            patch.object(matter_app, "_trigger_matter_node_snapshot_refresh") as trigger_refresh,
        ):
            client = matter_app.app.test_client()
            response = client.post("/nodes/4/air-reboot")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        supports_reboot.assert_called_once_with(4)
        ws_request.assert_called_once_with(
            "device_command",
            {
                "node_id": 4,
                "endpoint_id": 0,
                "cluster_id": 51,
                "command_name": "TestEventTrigger",
                "payload": {
                    "enableKey": matter_app.MATTER_NODE_REBOOT_ENABLE_KEY_B64,
                    "eventTrigger": matter_app.MATTER_NODE_REBOOT_EVENT_TRIGGER,
                },
            },
        )
        trigger_refresh.assert_called_once_with(force=True, blocking=False)

    def test_air_reboot_route_rejects_unadvertised_node(self) -> None:
        with patch.object(matter_app, "_node_supports_air_reboot", return_value=False):
            client = matter_app.app.test_client()
            response = client.post("/nodes/4/air-reboot")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "air_reboot_not_advertised_by_node")

    def test_matter_server_restart_requires_host_script(self) -> None:
        client = matter_app.app.test_client()
        response = client.post("/control/matter-server/restart")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"], "host_restart_required")
        self.assertIn("./scripts/restart-matter-server.sh", payload["details"])


if __name__ == "__main__":
    unittest.main()
