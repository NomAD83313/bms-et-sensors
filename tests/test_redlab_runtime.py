import threading
import unittest
from types import SimpleNamespace

from app.redlab.redlab_settings import ReadSettings, RedLabSettings
from unittest.mock import Mock, patch

from tests.redlab_fakes import load_redlab_module


class RedLabRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_redlab_module("app.redlab.redlab_runtime")

    def setUp(self):
        self.writer = Mock()
        self.runtime = self.mod.RedLabRuntime(
            SimpleNamespace(unique_id="test", product_name="USB-TC"), self.writer,
            settings=RedLabSettings(), read_settings=ReadSettings(RedLabSettings()),
        )

    def test_read_failure_releases_device_and_signals_writer(self):
        device = Mock()
        self.runtime.daq_device = device
        self.runtime.ai_device = Mock()
        self.runtime.ai_config = Mock()
        with patch.object(self.runtime, "connect"), patch.object(self.mod, "load_active_channels", return_value={0}):
            with patch.object(self.mod, "read_batch", side_effect=OSError("USB disconnected")):
                self.runtime.run()
        device.disconnect.assert_called_once()
        device.release.assert_called_once()
        self.assertIsNone(self.runtime.ai_device)
        self.assertTrue(self.runtime.writer.writer_stop_event.is_set())
        self.assertEqual(self.runtime.health_snapshot()["status"], "error")

    def test_bootstrap_preserves_warmup_and_filters_before_first_write(self):
        self.runtime.ai_device = Mock()
        self.runtime.ai_config = Mock()
        batches = [{"values": {0: 23.5}, "ts_ns": 10}]
        writes = []

        def capture(*args, **kwargs):
            writes.append((args, kwargs))
            self.runtime.stop_event.set()

        with patch.object(self.runtime, "connect"), patch.object(self.mod, "load_active_channels", return_value={0}):
            with patch.object(self.mod, "read_batch", return_value=({0: 23.5}, 0)):
                with patch.object(self.mod.time, "monotonic", side_effect=[0, 1, 2, 10, 11, 12, 13]):
                    with patch.object(self.mod, "BOOTSTRAP_BATCHES", 2), patch.object(self.mod.time, "sleep"):
                        with patch.object(self.mod, "filter_bootstrap_batches", return_value=(batches, 1)) as filtered:
                            with patch.object(self.runtime, "write_runtime_points", side_effect=capture):
                                self.runtime.run()
        self.assertEqual(len(filtered.call_args.args[0]), 2)
        self.assertEqual(len(writes), 1)
        self.assertIs(writes[0][0][0], batches)

    def test_stop_wakes_idle_writer(self):
        thread = threading.Thread(target=self.runtime.writer.run, daemon=True)
        thread.start()
        try:
            self.runtime.stop("test shutdown")
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.runtime.health_snapshot()["status"], "offline")
        finally:
            self.runtime.writer.writer_stop_event.set()
            with self.runtime.writer.write_queue_lock:
                self.runtime.writer.write_queue_lock.notify_all()


    def test_run_passes_channel_state_path_to_load_active_channels(self):
        self.runtime.daq_device = Mock()
        self.runtime.ai_device = Mock()
        self.runtime.ai_config = Mock()
        with patch.object(self.runtime, "connect"), patch.object(self.mod, "load_active_channels", return_value=set()) as load_mock:
            with patch.object(self.mod.time, "sleep", side_effect=lambda s: self.runtime.stop_event.set()):
                self.runtime.run()
        load_mock.assert_called_with(self.runtime.settings.channel_state_path)


if __name__ == "__main__":
    unittest.main()
