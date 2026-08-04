import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module():
    fake_docker = types.ModuleType("docker")
    fake_docker.DockerClient = lambda *_args, **_kwargs: object()
    fake_errors = types.SimpleNamespace(NotFound=Exception)
    fake_docker.errors = fake_errors
    sys.modules["docker"] = fake_docker

    fake_svcctl_docker = types.ModuleType("svcctl_docker")
    fake_svcctl_docker.container_status = lambda *_args, **_kwargs: "exited"
    fake_svcctl_docker.get_container = lambda *_args, **_kwargs: None
    fake_svcctl_docker.read_since_logs = lambda *_args, **_kwargs: ""
    fake_svcctl_docker.set_container_running = lambda *_args, **_kwargs: "noop"
    fake_svcctl_docker.started_at_epoch = lambda *_args, **_kwargs: None
    fake_svcctl_docker.status_payload = lambda *_args, **_kwargs: {}
    sys.modules["svcctl_docker"] = fake_svcctl_docker

    fake_svcctl_host = types.ModuleType("svcctl_host")
    fake_svcctl_host.cpu_percent = lambda *_args, **_kwargs: (0.0, 0, 0)
    fake_svcctl_host.memory_stats = lambda: {}
    fake_svcctl_host.serial_devices_by_id = lambda: []
    fake_svcctl_host.usb_devices = lambda: []
    sys.modules["svcctl_host"] = fake_svcctl_host

    fake_svcctl_usb = types.ModuleType("svcctl_usb")
    fake_svcctl_usb.build_usb_guard_state = lambda **kwargs: kwargs
    fake_svcctl_usb.usb_device_present = lambda *_args, **_kwargs: False
    sys.modules["svcctl_usb"] = fake_svcctl_usb

    return importlib.import_module("app.svcctl.svcctl_app")


class SvcctlMsclGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_mscl_serial_candidates_prefers_env_then_by_id(self):
        with patch.dict(os.environ, {"MSCL_PORT": "/dev/custom-mscl"}, clear=False):
            with patch.object(self.mod.glob, "glob", return_value=["/dev/serial/by-id/wsda-a", "/dev/serial/by-id/wsda-b"]):
                self.assertEqual(
                    self.mod._mscl_serial_candidates(),
                    ["/dev/custom-mscl", "/dev/serial/by-id/wsda-a", "/dev/serial/by-id/wsda-b"],
                )

    def test_mscl_serial_present_reports_unset_when_no_candidates(self):
        with patch.dict(os.environ, {}, clear=False):
            with patch.object(self.mod.glob, "glob", return_value=[]):
                self.assertEqual(self.mod._mscl_serial_present(), (False, "mscl_port_unset"))

    def test_mscl_serial_present_uses_first_existing_candidate(self):
        with patch.dict(os.environ, {"MSCL_PORT": "/dev/missing-mscl"}, clear=False):
            with patch.object(self.mod.glob, "glob", return_value=["/dev/serial/by-id/wsda-a"]):
                with patch.object(self.mod.Path, "exists", autospec=True) as exists_mock:
                    exists_mock.side_effect = lambda path_obj: str(path_obj) == "/dev/serial/by-id/wsda-a"
                    self.assertEqual(self.mod._mscl_serial_present(), (True, "/dev/serial/by-id/wsda-a"))

    def test_mscl_guard_stops_service_when_serial_missing(self):
        original_allow = self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL
        original_keep = self.mod.KEEP_DEGRADED_SERVICES_RUNNING
        try:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = False
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = False
            with patch.object(self.mod, "_mscl_serial_present", return_value=(False, "mscl_port_unset")):
                with patch.object(self.mod, "_container_status", return_value="running"):
                    stops = []
                    with patch.object(self.mod, "_stop_mscl", side_effect=lambda reason: stops.append(reason)):
                        self.mod._MSCL_PRESENT_SINCE = 123.0
                        self.mod._ensure_mscl_guard_step()
        finally:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = original_allow
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = original_keep
        self.assertEqual(stops, ["serial_missing"])
        self.assertIsNone(self.mod._MSCL_PRESENT_SINCE)

    def test_mscl_guard_keeps_service_when_serial_missing_in_degraded_mode(self):
        original_allow = self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL
        original_keep = self.mod.KEEP_DEGRADED_SERVICES_RUNNING
        try:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = False
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = True
            with patch.object(self.mod, "_mscl_serial_present", return_value=(False, "mscl_port_unset")):
                with patch.object(self.mod, "_container_status", return_value="running"):
                    stops = []
                    with patch.object(self.mod, "_stop_mscl", side_effect=lambda reason: stops.append(reason)):
                        self.mod._MSCL_PRESENT_SINCE = 123.0
                        self.mod._ensure_mscl_guard_step()
        finally:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = original_allow
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = original_keep
        self.assertEqual(stops, [])
        self.assertEqual(self.mod._MSCL_LAST_ACTION, "noop")
        self.assertEqual(self.mod._MSCL_LAST_REASON, "serial_missing_degraded_mode:mscl_port_unset")
        self.assertIsNone(self.mod._MSCL_PRESENT_SINCE)

    def test_mscl_guard_starts_service_without_serial_in_ui_mode(self):
        original_allow = self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL
        try:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = True
            with patch.object(self.mod, "_mscl_serial_present", return_value=(False, "/dev/missing-mscl")):
                with patch.object(self.mod, "_container_status", return_value="exited"):
                    starts = []
                    with patch.object(self.mod, "_start_mscl", side_effect=lambda reason: starts.append(reason)):
                        self.mod._MSCL_PRESENT_SINCE = 123.0
                        self.mod._ensure_mscl_guard_step()
        finally:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = original_allow
        self.assertEqual(starts, ["serial_missing_ui_mode"])
        self.assertIsNone(self.mod._MSCL_PRESENT_SINCE)

    def test_mscl_guard_keeps_running_service_without_serial_in_ui_mode(self):
        original_allow = self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL
        try:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = True
            with patch.object(self.mod, "_mscl_serial_present", return_value=(False, "/dev/missing-mscl")):
                with patch.object(self.mod, "_container_status", return_value="running"):
                    stops = []
                    with patch.object(self.mod, "_stop_mscl", side_effect=lambda reason: stops.append(reason)):
                        self.mod._MSCL_PRESENT_SINCE = 123.0
                        self.mod._ensure_mscl_guard_step()
        finally:
            self.mod.MSCL_UI_ALLOW_WITHOUT_SERIAL = original_allow
        self.assertEqual(stops, [])
        self.assertEqual(self.mod._MSCL_LAST_ACTION, "noop")
        self.assertEqual(self.mod._MSCL_LAST_REASON, "serial_missing_ui_mode:/dev/missing-mscl")
        self.assertIsNone(self.mod._MSCL_PRESENT_SINCE)

    def test_mscl_guard_starts_service_after_stable_serial(self):
        with patch.object(self.mod, "_mscl_serial_present", return_value=(True, "/dev/mscl")):
            with patch.object(self.mod, "_container_status", return_value="exited"):
                starts = []
                with patch.object(self.mod, "_start_mscl", side_effect=lambda reason: starts.append(reason)):
                    self.mod._MSCL_PRESENT_SINCE = 10.0
                    with patch.object(self.mod.time, "time", return_value=20.0):
                        self.mod._ensure_mscl_guard_step()
        self.assertEqual(starts, ["serial_stable"])

    def test_degraded_start_retry_is_throttled(self):
        original_retry = self.mod.DEGRADED_START_RETRY_SEC
        original_starts = dict(self.mod._DEGRADED_LAST_START_AT)
        try:
            self.mod.DEGRADED_START_RETRY_SEC = 60.0
            self.mod._DEGRADED_LAST_START_AT.clear()
            self.assertEqual(self.mod._degraded_start_allowed("mscl-collector", 100.0), (True, 0.0))
            self.assertEqual(self.mod._degraded_start_allowed("mscl-collector", 115.0), (False, 45.0))
            self.assertEqual(self.mod._degraded_start_allowed("mscl-collector", 160.0), (True, 0.0))
        finally:
            self.mod.DEGRADED_START_RETRY_SEC = original_retry
            self.mod._DEGRADED_LAST_START_AT.clear()
            self.mod._DEGRADED_LAST_START_AT.update(original_starts)

    def test_manual_pause_can_be_saved_and_resumed(self):
        original_file = self.mod.MANUAL_PAUSE_FILE
        original_pauses = dict(self.mod._MANUAL_PAUSES)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                self.mod.MANUAL_PAUSE_FILE = Path(tmpdir) / "pauses.json"
                self.mod._MANUAL_PAUSES.clear()

                self.mod._manual_pause_target("mscl")

                self.assertTrue(self.mod._target_manual_paused("mscl"))
                self.assertTrue(self.mod.MANUAL_PAUSE_FILE.exists())

                self.mod._MANUAL_PAUSES.clear()
                self.mod._load_manual_pauses()
                self.assertTrue(self.mod._target_manual_paused("mscl"))

                self.mod._manual_resume_target("mscl")
                self.assertFalse(self.mod._target_manual_paused("mscl"))
        finally:
            self.mod.MANUAL_PAUSE_FILE = original_file
            self.mod._MANUAL_PAUSES.clear()
            self.mod._MANUAL_PAUSES.update(original_pauses)

    def test_mscl_guard_respects_manual_pause(self):
        original_pauses = dict(self.mod._MANUAL_PAUSES)
        try:
            self.mod._MANUAL_PAUSES.clear()
            self.mod._MANUAL_PAUSES["mscl"] = {"reason": "manual_stop", "ts": 1.0}
            with patch.object(self.mod, "_mscl_serial_present", return_value=(True, "/dev/mscl")):
                with patch.object(self.mod, "_container_status", return_value="exited"):
                    starts = []
                    with patch.object(self.mod, "_start_mscl", side_effect=lambda reason: starts.append(reason)):
                        self.mod._MSCL_PRESENT_SINCE = 10.0
                        with patch.object(self.mod.time, "time", return_value=20.0):
                            self.mod._ensure_mscl_guard_step()
        finally:
            self.mod._MANUAL_PAUSES.clear()
            self.mod._MANUAL_PAUSES.update(original_pauses)
        self.assertEqual(starts, [])
        self.assertEqual(self.mod._MSCL_LAST_ACTION, "manual_pause")
        self.assertEqual(self.mod._MSCL_LAST_REASON, "user_stopped")

    def test_redlab_guard_keeps_running_without_usb_in_degraded_mode(self):
        original_keep = self.mod.KEEP_DEGRADED_SERVICES_RUNNING
        try:
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = True
            with patch.object(self.mod, "_redlab_usb_present", return_value=False):
                with patch.object(self.mod, "_container_status", return_value="running"):
                    stops = []
                    with patch.object(self.mod, "_stop_redlab", side_effect=lambda reason: stops.append(reason)):
                        self.mod._REDLAB_PRESENT_SINCE = 123.0
                        self.mod._ensure_redlab_guard_step()
        finally:
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = original_keep
        self.assertEqual(stops, [])
        self.assertEqual(self.mod._REDLAB_LAST_ACTION, "noop")
        self.assertEqual(self.mod._REDLAB_LAST_REASON, "usb_missing_degraded_mode")
        self.assertIsNone(self.mod._REDLAB_PRESENT_SINCE)

    def test_almemo_guard_keeps_running_without_usb_in_degraded_mode(self):
        original_keep = self.mod.KEEP_DEGRADED_SERVICES_RUNNING
        try:
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = True
            with patch.object(self.mod, "_almemo_usb_present", return_value=False):
                with patch.object(self.mod, "_container_status", return_value="running"):
                    stops = []
                    with patch.object(self.mod, "_stop_almemo", side_effect=lambda reason: stops.append(reason)):
                        self.mod._ALMEMO_PRESENT_SINCE = 123.0
                        self.mod._ensure_almemo_guard_step()
        finally:
            self.mod.KEEP_DEGRADED_SERVICES_RUNNING = original_keep
        self.assertEqual(stops, [])
        self.assertEqual(self.mod._ALMEMO_LAST_ACTION, "noop")
        self.assertEqual(self.mod._ALMEMO_LAST_REASON, "usb_missing_degraded_mode")
        self.assertIsNone(self.mod._ALMEMO_PRESENT_SINCE)

    def test_target_map_excludes_matter_thread_stack(self):
        self.assertNotIn("openthread", self.mod.TARGET_MAP)
        self.assertNotIn("matter", self.mod.TARGET_MAP)

    def test_target_map_includes_messkluppe_collector(self):
        self.assertEqual(self.mod.TARGET_MAP["messkluppe"], ["messkluppe-collector"])

if __name__ == "__main__":
    unittest.main()
