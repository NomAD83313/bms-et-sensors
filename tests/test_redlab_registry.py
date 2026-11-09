import unittest
from types import SimpleNamespace
from dataclasses import replace

from app.redlab.redlab_settings import ReadSettings, RedLabSettings
from unittest.mock import Mock, patch

from tests.redlab_fakes import load_redlab_module


class EndLoop(BaseException):
    pass


class RedLabRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_redlab_module("app.redlab.redlab_registry")

    def setUp(self):
        settings = RedLabSettings()
        self.registry = self.mod.RedLabRegistry(settings=settings, read_settings=ReadSettings(settings), write_api=Mock())

    def test_disconnected_runtime_is_stopped_and_removed(self):
        runtime = Mock()
        runtime.health_snapshot.return_value = {"status": "running"}
        self.registry.runtimes["redlab_test"] = runtime
        with patch.object(self.registry, "settings", replace(self.registry.settings, reenumeration_recovery=False)):
            with patch.object(self.registry, "_valid_inventory_descriptors", return_value=[]):
                with patch.object(self.mod.time, "sleep", side_effect=EndLoop):
                    with self.assertRaises(EndLoop):
                        self.registry.run()
        runtime.stop.assert_called_once_with("device disconnected")
        self.assertEqual(self.registry.runtimes, {})

    def test_failed_runtime_is_replaced_when_device_is_present(self):
        previous = Mock()
        previous.thread.is_alive.return_value = False
        previous.health_snapshot.return_value = {"status": "error"}
        self.registry.runtimes["redlab_test"] = previous
        replacement = Mock()
        replacement.health_snapshot.return_value = {"status": "starting", "device_id": "redlab_test"}
        descriptor = SimpleNamespace(unique_id="test")
        with patch.object(self.registry, "settings", replace(self.registry.settings, reenumeration_recovery=False)):
            with patch.object(self.registry, "_valid_inventory_descriptors", return_value=[descriptor]):
                with patch.object(self.registry, "runtime_factory", return_value=replacement):
                    with patch.object(self.mod.time, "sleep", side_effect=EndLoop):
                        with self.assertRaises(EndLoop):
                            self.registry.run()
        self.assertIs(self.registry.runtimes["redlab_test"], replacement)
        replacement.start.assert_called_once()

    def test_usb_reenumeration_requires_confirmation_and_second_probe(self):
        sleeps = 0

        def sleep(_seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= 3:
                raise EndLoop

        with patch.object(self.registry, "settings", replace(self.registry.settings, reenumeration_recovery=True)):
            with patch.object(self.registry, "settings", replace(self.registry.settings, reenumeration_confirmations=2)):
                with patch.object(self.registry, "_valid_inventory_descriptors", return_value=[]) as inventory:
                    with patch.object(self.registry, "_fresh_inventory_device_ids", return_value={"redlab_test"}) as fresh:
                        with patch.object(self.registry, "_restart_cooldown_remaining", return_value=0):
                            with patch.object(self.registry, "_restart_process_for_usb_reenumeration") as restart:
                                with patch.object(self.mod.time, "sleep", side_effect=sleep):
                                    with self.assertRaises(EndLoop):
                                        self.registry.run()
        restart.assert_called_once_with({"redlab_test"})
        self.assertEqual(inventory.call_count, 3)
        self.assertEqual(fresh.call_count, 3)

    def test_fresh_inventory_failure_does_not_raise_or_trigger_restart(self):
        with patch.object(self.mod.subprocess, "check_output", side_effect=OSError("probe failed")):
            self.assertEqual(self.registry._fresh_inventory_device_ids(), set())


    def test_restart_cooldown_prevents_process_restart(self):
        self.registry.settings = replace(self.registry.settings, reenumeration_confirmations=1)
        with (
            patch.object(self.registry, "_valid_inventory_descriptors", return_value=[]),
            patch.object(self.registry, "_fresh_inventory_device_ids", return_value={"redlab_test"}),
            patch.object(self.registry, "_restart_cooldown_remaining", return_value=30),
            patch.object(self.registry, "_restart_process_for_usb_reenumeration") as restart,
            patch.object(self.mod.time, "sleep", side_effect=EndLoop),
        ):
            with self.assertRaises(EndLoop):
                self.registry.run()
        restart.assert_not_called()

    def test_reenumeration_settles_during_debounce_without_restart(self):
        self.registry.settings = replace(self.registry.settings, reenumeration_confirmations=1)
        with (
            patch.object(self.registry, "_valid_inventory_descriptors", return_value=[]),
            patch.object(self.registry, "_fresh_inventory_device_ids", side_effect=[{"redlab_test"}, set()]),
            patch.object(self.registry, "_restart_cooldown_remaining", return_value=0),
            patch.object(self.registry, "_restart_process_for_usb_reenumeration") as restart,
            patch.object(self.mod.time, "sleep", side_effect=[None, EndLoop]),
        ):
            with self.assertRaises(EndLoop):
                self.registry.run()
        restart.assert_not_called()

    def test_fresh_probe_keeps_attached_runtime_when_cached_inventory_lags(self):
        runtime = Mock()
        runtime.health_snapshot.return_value = {"status": "running", "device_id": "redlab_test"}
        self.registry.runtimes["redlab_test"] = runtime
        with (
            patch.object(self.registry, "_valid_inventory_descriptors", return_value=[]),
            patch.object(self.registry, "_fresh_inventory_device_ids", return_value={"redlab_test"}),
            patch.object(self.mod.time, "sleep", side_effect=EndLoop),
        ):
            with self.assertRaises(EndLoop):
                self.registry.run()
        runtime.stop.assert_not_called()
        self.assertIs(self.registry.runtimes["redlab_test"], runtime)


if __name__ == "__main__":
    unittest.main()
