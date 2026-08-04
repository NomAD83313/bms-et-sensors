import unittest
from unittest.mock import patch

from app.messkluppe import messkluppe_app
from app.messkluppe.messkluppe_protocol import TASK_IDLE, TASK_LIVE_DATA, make_id_task, words16_to_legacy_payload


class MesskluppeAppControlTests(unittest.TestCase):
    def setUp(self):
        self.client = messkluppe_app.app.test_client()

    def test_start_logging_updates_state_in_fake_mode(self):
        response = self.client.post("/api/clip/start-logging", json={"sample_rate": 500, "logging_time": 12})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"]["clip_mode"], "logging")
        self.assertEqual(payload["status"]["clip_task"], 20)
        self.assertTrue(payload["status"]["logging"])
        self.assertEqual(payload["status"]["sample_rate"], 500)
        self.assertEqual(payload["status"]["logging_time"], 12)
        self.assertEqual(payload["command"]["action"], "start_logging")
        self.assertTrue(payload["command"]["payload_hex"].startswith("fc030000"))

    def test_reset_mode_returns_to_idle(self):
        self.client.post("/api/clip/start-logging", json={"sample_rate": 500, "logging_time": 12})

        response = self.client.post("/api/clip/reset-mode")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"]["clip_mode"], "idle")
        self.assertEqual(payload["status"]["clip_task"], 0)
        self.assertFalse(payload["status"]["logging"])

    def test_live_start_accepts_known_display_modes(self):
        response = self.client.post("/api/clip/live/start", json={"display": "raw"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"]["clip_mode"], "live")
        self.assertEqual(payload["status"]["clip_task"], 60)
        self.assertTrue(payload["status"]["live_mode"])
        self.assertEqual(payload["status"]["live_display"], "raw")

    def test_deep_sleep_controls_state(self):
        start = self.client.post("/api/clip/deep-sleep/start")
        stop = self.client.post("/api/clip/deep-sleep/stop")

        self.assertEqual(start.status_code, 200)
        self.assertTrue(start.get_json()["status"]["deep_sleep"])
        self.assertEqual(stop.status_code, 200)
        self.assertFalse(stop.get_json()["status"]["deep_sleep"])

    def test_files_endpoint_returns_contract_shape(self):
        response = self.client.get("/api/clip/files")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("files", payload)
        self.assertIn("status", payload)

    def test_index_exposes_legacy_control_surface(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Start Logging", html)
        self.assertIn("Online Files", html)
        self.assertIn("startLoggingBtn", html)
        self.assertIn("Mock once", html)
        self.assertIn("api/mock-node/start", html)
        self.assertIn("api/clip/start-logging", html)
        self.assertIn("api/clip/files/delete-all", html)
        self.assertIn("Radio Diagnostics", html)
        self.assertIn("api/radio/diagnose", html)
        self.assertIn("Radio listening", html)

    def test_radio_diagnostics_endpoint_updates_state(self):
        diag = {
            "ok": True,
            "configured": {"spi_bus": 0, "spi_device": 0, "ce_gpio": 25},
            "checks": {"spi_open": {"ok": True}},
            "registers": {"rf_ch": 111},
            "details": {"connected_hint": True},
            "duration_ms": 1.0,
            "error": "",
        }

        with patch.object(messkluppe_app, "run_radio_diagnostics", return_value=diag):
            response = self.client.post("/api/radio/diagnose")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["radio"], diag)
        self.assertEqual(payload["status"]["radio_last_diag"], diag)

    def test_radio_diagnostics_uses_runtime_state_while_rx_loop_is_active(self):
        with messkluppe_app._state_lock:
            old_values = {
                key: messkluppe_app._state.get(key)
                for key in ("radio_listening", "radio_rx_ready", "radio_runtime", "radio_rx_packets", "radio_rx_empty_reads")
            }
            messkluppe_app._state.update(
                radio_listening=True,
                radio_rx_ready=True,
                radio_runtime={"status": 14, "fifo_status": 17},
                radio_rx_packets=0,
                radio_rx_empty_reads=3,
            )
        try:
            with patch.object(messkluppe_app, "run_radio_diagnostics") as diag_mock:
                response = self.client.post("/api/radio/diagnose")
        finally:
            with messkluppe_app._state_lock:
                messkluppe_app._state.update(old_values)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["radio"]["checks"]["radio_rx_loop"]["message"], "radio_rx_loop_active")
        self.assertEqual(payload["radio"]["registers"]["fifo_status"], 17)
        diag_mock.assert_not_called()

    @patch.object(messkluppe_app, "_write_record", return_value=True)
    def test_mock_node_once_ingests_decoded_payload(self, _write_record):
        response = self.client.post("/api/mock-node/once")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sample"]["file_id"], "mock-node")
        self.assertEqual(payload["status"]["last_record"]["tags"]["file_id"], "mock-node")
        self.assertGreaterEqual(payload["status"]["packets_received"], 1)

    @patch.object(messkluppe_app, "_write_record", return_value=True)
    def test_ingest_payload_decodes_ping_as_status_record(self, _write_record):
        payload = words16_to_legacy_payload([
            make_id_task(1, TASK_IDLE),
            0x0004,
            0x93AE,
            100,
            9500,
            3,
            *([0] * 10),
        ])

        self.assertTrue(messkluppe_app.ingest_payload(payload, file_id="radio"))

        fields = messkluppe_app._state_snapshot()["last_record"]["fields"]
        self.assertEqual(fields["ping_ms"], 100)
        self.assertEqual(fields["file_count"], 3)
        self.assertNotIn("force_x_raw", fields)

    @patch.object(messkluppe_app, "_write_record", return_value=True)
    def test_ingest_payload_decodes_live_sensor_layout(self, _write_record):
        payload = words16_to_legacy_payload([
            make_id_task(1, TASK_LIVE_DATA),
            0x0003,
            0x0768,
            423,
            326,
            307,
            309,
            299,
            270,
            2276,
            4076,
            77,
            0,
            1,
            3,
            777,
        ])

        self.assertTrue(messkluppe_app.ingest_payload(payload, file_id="radio"))

        record = messkluppe_app._state_snapshot()["last_record"]
        self.assertEqual(record["tags"]["packet_task"], "60")
        self.assertEqual(record["fields"]["force_x_raw"], 307)
        self.assertEqual(record["fields"]["accel_x_raw"], 270)
        self.assertEqual(record["fields"]["yaw_raw"], 4076)
        self.assertEqual(record["fields"]["yaw_deg"], 47.6)

    def test_status_includes_radio_runtime_telemetry(self):
        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("radio_listening", payload)
        self.assertIn("radio_rx_packets", payload)
        self.assertIn("radio_rx_empty_reads", payload)
        self.assertIn("radio_rx_last_error", payload)
        self.assertIn("radio_rx_last_payload_hex", payload)
        self.assertIn("radio_rx_recent_payloads", payload)
        self.assertIn("radio_tx_commands", payload)
        self.assertIn("radio_tx_last_payload_hex", payload)
        self.assertIn("radio_tx_auto_repeats", payload)

    def test_radio_recent_payloads_endpoint_returns_ring_buffer(self):
        with messkluppe_app._state_lock:
            old_values = {
                key: messkluppe_app._state.get(key)
                for key in ("radio_rx_recent_payloads", "radio_rx_last_payload_hex")
            }
            messkluppe_app._state.update(
                radio_rx_recent_payloads=[
                    {"ts": 1.0, "payload_hex": "00", "size": 1, "ok": True, "error": ""},
                    {"ts": 2.0, "payload_hex": "0102", "size": 2, "ok": False, "error": "decode"},
                ],
                radio_rx_last_payload_hex="0102",
            )
        try:
            response = self.client.get("/api/radio/recent-payloads")
        finally:
            with messkluppe_app._state_lock:
                messkluppe_app._state.update(old_values)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["last_payload_hex"], "0102")
        self.assertEqual(payload["payloads"][1]["error"], "decode")

    def test_remember_radio_payload_keeps_bounded_recent_payloads(self):
        with messkluppe_app._state_lock:
            old_values = {
                key: messkluppe_app._state.get(key)
                for key in ("radio_rx_recent_payloads", "radio_rx_last_payload_hex", "radio_rx_last_at")
            }
            messkluppe_app._state.update(radio_rx_recent_payloads=[], radio_rx_last_payload_hex="", radio_rx_last_at=None)
        try:
            for value in range(messkluppe_app.MESSKLUPPE_RADIO_RECENT_PAYLOADS + 2):
                messkluppe_app._remember_radio_payload(bytes([value]))
            snap = messkluppe_app._state_snapshot()
        finally:
            with messkluppe_app._state_lock:
                messkluppe_app._state.update(old_values)

        self.assertEqual(len(snap["radio_rx_recent_payloads"]), messkluppe_app.MESSKLUPPE_RADIO_RECENT_PAYLOADS)
        self.assertEqual(snap["radio_rx_last_payload_hex"], f"{messkluppe_app.MESSKLUPPE_RADIO_RECENT_PAYLOADS + 1:02x}")

    def test_radio_recent_commands_endpoint_returns_tx_audit_trail(self):
        with patch.object(messkluppe_app, "MESSKLUPPE_INPUT_MODE", "mock"):
            self.client.post("/api/clip/deep-sleep/start")

        response = self.client.get("/api/radio/recent-commands")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["count"], 1)
        self.assertEqual(payload["commands"][-1]["action"], "start_deep_sleep")
        self.assertTrue(payload["commands"][-1]["payload_hex"].startswith("f2030000"))

    def test_file_list_builds_legacy_tx_payload(self):
        with patch.object(messkluppe_app, "MESSKLUPPE_INPUT_MODE", "mock"):
            response = self.client.get("/api/clip/files")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"]["action"], "list_files")
        self.assertTrue(payload["command"]["payload_hex"].startswith("06040000"))

    def test_file_list_enables_radio_repeat_until_node_replies(self):
        old_values = {}
        with messkluppe_app._state_lock:
            for key in ("radio_repeat_action", "radio_repeat_payload"):
                old_values[key] = messkluppe_app._state.get(key)
            messkluppe_app._state.update(radio_repeat_action="", radio_repeat_payload=None)
        try:
            with patch.object(messkluppe_app, "MESSKLUPPE_INPUT_MODE", "mock"):
                response = self.client.get("/api/clip/files")
            self.assertEqual(response.status_code, 200)
            snap = messkluppe_app._state_snapshot()
            self.assertEqual(snap["radio_repeat_action"], "list_files")
            self.assertEqual(snap["radio_repeat_payload"], {})
        finally:
            with messkluppe_app._state_lock:
                messkluppe_app._state.update(old_values)

    def test_radio_mode_queues_ack_payload_for_command(self):
        tx_result = {"ok": True, "pipe": 1, "payload_hex": "24040000c3ac4508", "size": 8}
        with (
            patch.object(messkluppe_app, "MESSKLUPPE_INPUT_MODE", "radio"),
            patch.object(messkluppe_app, "_queue_radio_ack_payload", return_value=tx_result) as queue_mock,
        ):
            response = self.client.post("/api/clip/live/start", json={"display": "linearForce"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"]["detail"], "ack_payload_queued_pipe_1")
        self.assertEqual(payload["command"]["tx_result"], tx_result)
        queue_mock.assert_called_once()

    def test_repeat_live_ack_payload_records_auto_repeat(self):
        class FakeRadio:
            config = {"payload_size": 32}

            def write_ack_payload(self, payload, *, pipe=1, flush_tx=True, pad_to=None):
                if pad_to:
                    payload = payload.ljust(pad_to, b"\x00")
                return {"ok": True, "pipe": pipe, "payload_hex": payload.hex(), "size": len(payload)}

        before = messkluppe_app._state_snapshot().get("radio_tx_auto_repeats", 0)

        messkluppe_app._repeat_live_ack_payload(FakeRadio())

        snap = messkluppe_app._state_snapshot()
        self.assertEqual(snap["radio_tx_auto_repeats"], before + 1)
        self.assertEqual(snap["radio_tx_last_action"], "start_live")
        self.assertEqual(snap["radio_tx_last_result"]["pipe"], 1)

    def test_send_live_ack_payload_uses_legacy_dynamic_ack_size_by_default(self):
        calls = []

        class FakeRadio:
            config = {"payload_size": 32}

            def write_ack_payload(self, payload, *, pipe=1, flush_tx=True, pad_to=None):
                calls.append({"pipe": pipe, "flush_tx": flush_tx, "pad_to": pad_to})
                if pad_to:
                    payload = payload.ljust(pad_to, b"\x00")
                return {"ok": True, "pipe": pipe, "payload_hex": payload.hex(), "size": len(payload)}

        messkluppe_app._send_live_ack_payload(FakeRadio(), detail="ack_payload_after_rx_pipe_1", flush_tx=False)

        self.assertEqual(calls, [{"pipe": 1, "flush_tx": False, "pad_to": None}])
        self.assertEqual(messkluppe_app._state_snapshot()["radio_tx_last_action"], "start_live")
        self.assertEqual(messkluppe_app._state_snapshot()["last_command"]["detail"], "ack_payload_after_rx_pipe_1")

    def test_ack_payload_padding_can_match_static_payload_size(self):
        class FakeRadio:
            config = {"payload_size": 32}

        with patch.object(messkluppe_app, "MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE", True):
            self.assertEqual(messkluppe_app._ack_payload_pad_to(FakeRadio()), 32)


if __name__ == "__main__":
    unittest.main()
