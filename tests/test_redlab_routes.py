import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
if _FLASK_AVAILABLE:
    from flask import Flask

from app.redlab.redlab_settings import ReadSettings, RedLabSettings
from tests.redlab_fakes import load_redlab_module


@unittest.skipUnless(_FLASK_AVAILABLE, "flask not installed")
class RedLabRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = load_redlab_module()
        cls.routes = load_redlab_module("app.redlab.redlab_routes")
        cls.registry_module = load_redlab_module("app.redlab.redlab_registry")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = RedLabSettings(channel_state_path=Path(directory.name) / "channels.json")
        self.read_settings = ReadSettings(self.settings)
        self.registry = self.registry_module.RedLabRegistry(settings=self.settings, read_settings=self.read_settings)
        self.app = Flask("redlab-test")
        self.routes.register_routes(self.app, self.registry, self.read_settings, self.settings)
        self.client = self.app.test_client()

    def test_import_creates_no_hardware_sessions_or_runtime_threads(self):
        self.assertEqual(self.main.registry.runtimes, {})
        self.main.registry.runtime_factory.__init__.__globals__["DaqDevice"].assert_not_called()
        self.assertIsNone(self.main.HEALTH_SERVER_THREAD)

    def test_public_route_methods_and_paths_are_unchanged(self):
        actual = sorted(
            (method, rule.rule)
            for rule in self.app.url_map.iter_rules() if rule.endpoint != "static"
            for method in rule.methods - {"HEAD", "OPTIONS"}
        )
        self.assertEqual(actual, [('GET', '/'), ('GET', '/api/config/loop_sleep'), ('GET', '/api/config/read_mode'), ('GET', '/api/config/tc'), ('GET', '/api/device/<device_id>/config/loop_sleep'), ('GET', '/api/device/<device_id>/config/read_mode'), ('GET', '/api/device/<device_id>/config/tc'), ('GET', '/api/device/<device_id>/health'), ('GET', '/api/devices'), ('GET', '/api/health'), ('GET', '/api/redlab/channels'), ('GET', '/health'), ('GET', '/healthz'), ('POST', '/api/config/loop_sleep'), ('POST', '/api/config/read_mode'), ('POST', '/api/config/tc'), ('POST', '/api/device/<device_id>/config/loop_sleep'), ('POST', '/api/device/<device_id>/config/read_mode'), ('POST', '/api/device/<device_id>/config/tc'), ('POST', '/api/redlab/channels')])

    def test_health_aliases_keep_same_payload(self):
        self.registry.set_health(status="running", connected=True)
        for path in ("/health", "/healthz", "/api/health"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, self.registry.get_health())

    def test_missing_runtime_keeps_legacy_and_device_error_contracts(self):
        self.assertEqual(self.client.get("/api/config/tc").json, {"ok": False, "error": "device not connected"})
        for path in ("tc", "read_mode", "loop_sleep"):
            for method in (self.client.get, self.client.post):
                response = method("/api/device/redlab_missing/config/" + path)
                self.assertEqual(response.status_code, 409)
                self.assertFalse(response.json["configurable"])

    def test_device_cadence_routes_preserve_shared_settings(self):
        for device_id in ("redlab_a", "redlab_b"):
            self.registry.runtimes[device_id] = Mock()
        response = self.client.post("/api/device/redlab_a/config/read_mode", json={"read_mode": " SINGLE "})
        self.assertEqual(response.json, {"ok": True, "read_mode": "single"})
        self.assertEqual(self.client.get("/api/device/redlab_b/config/read_mode").json["read_mode"], "single")
        response = self.client.post("/api/device/redlab_a/config/loop_sleep", json={"loop_sleep_seconds": "0,25"})
        self.assertEqual(response.json, {"ok": True, "loop_sleep_seconds": 0.25})
        self.assertEqual(self.client.get("/api/config/loop_sleep").json["loop_sleep_seconds"], 0.25)
        self.assertEqual(self.registry.get_health()["loop_sleep_seconds"], 0.25)

    def test_invalid_cadence_does_not_change_settings(self):
        for value in (None, "bad", 0.01, 61):
            response = self.client.post("/api/config/loop_sleep", json={"loop_sleep_seconds": value})
            self.assertFalse(response.json["ok"])
        for value in (None, "burst"):
            response = self.client.post("/api/config/read_mode", json={"read_mode": value})
            self.assertFalse(response.json["ok"])
        self.assertEqual(self.read_settings.get_loop_sleep(), 0.5)
        self.assertEqual(self.read_settings.get_read_mode(), "list")

    def test_device_tc_routes_target_requested_runtime(self):
        runtime = Mock()
        runtime.tc_config_payload.return_value = {"ok": True, "channels": [{"channel": 0, "tc_type": "K"}]}
        runtime.apply_tc_updates.return_value = {"ok": True, "applied": []}
        self.registry.runtimes["redlab_b"] = runtime
        self.assertEqual(self.client.get("/api/device/redlab_b/config/tc").json, runtime.tc_config_payload.return_value)
        updates = [{"channel": 0, "tc_type": "J"}]
        self.client.post("/api/device/redlab_b/config/tc", json={"updates": updates})
        runtime.apply_tc_updates.assert_called_once_with(updates)

    def test_device_health_distinguishes_absent_and_standby(self):
        with patch.object(self.registry, "device_inventory", return_value=[]):
            self.assertEqual(self.client.get("/api/device/redlab_b/health").status_code, 404)
        with patch.object(self.registry, "device_inventory", return_value=[{
            "device_id": "redlab_b", "unique_id": "b", "product_name": "USB-TC",
        }]):
            response = self.client.get("/api/device/redlab_b/health")
            self.assertEqual(response.json["status"], "standby")
            self.assertFalse(response.json["configurable"])

    def test_channels_round_trip_uses_injected_path(self):
        response = self.client.post("/api/redlab/channels", json={"ch0": {"enabled": False, "name": " A "}})
        self.assertEqual(response.json["channels"]["ch0"], {"enabled": False, "name": "A"})
        self.assertEqual(self.client.get("/api/redlab/channels").json, response.json)
        self.assertTrue(self.settings.channel_state_path.exists())
        self.assertEqual(self.client.post("/api/redlab/channels", json=["invalid"]).status_code, 400)

    def test_inventory_failure_preserves_http_error(self):
        with patch.object(self.registry, "device_inventory", side_effect=OSError("USB error")):
            response = self.client.get("/api/devices")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json, {"ok": False, "devices": [], "count": 0, "error": "USB error"})


if __name__ == "__main__":
    unittest.main()
