import importlib.util
import unittest
from unittest.mock import Mock, patch

from app.redlab.redlab_settings import ReadSettings, RedLabSettings
from tests.redlab_fakes import load_redlab_module

_INFLUXDB_AVAILABLE = importlib.util.find_spec("influxdb_client") is not None


class RedLabWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_redlab_module("app.redlab.redlab_writer")

    def setUp(self):
        self.health = {}
        settings = RedLabSettings(bucket="bucket", org="org")
        self.queue = self.mod.RedLabWriter(
            "redlab_test", "test", Mock(), settings=settings,
            read_settings=ReadSettings(settings), set_health=lambda **updates: self.health.update(updates),
        )

    def test_queue_overflow_drops_oldest_batches_and_keeps_limit(self):
        with patch.object(self.queue, "settings", RedLabSettings(write_queue_max_batches=2)):
            self.queue.enqueue([{"values": {0: 1}}], "first", 0, None)
            self.queue.enqueue([{"values": {0: 2}}, {"values": {0: 3}}], "second", 0, None)
        self.assertEqual(self.queue._queued_batch_count(), 2)
        self.assertEqual(self.queue.write_queue[0][1], "second")


    def test_writer_retries_same_batch_then_drains_queue_on_stop(self):
        batches = [{"values": {0: 23.5}, "ts_ns": 1}]
        self.queue.enqueue(batches, "sample", 2, 1.5)
        self.queue.writer_stop_event.set()
        with patch.object(self.queue, "_write_batches", side_effect=[OSError("offline"), ["CH0:23.5"]]) as write:
            with patch.object(self.mod.time, "sleep"):
                self.queue.run()
        self.assertEqual(write.call_count, 2)
        self.assertIs(write.call_args_list[0].args[0], write.call_args_list[1].args[0])
        health = self.health
        self.assertEqual(health["write_queue_depth"], 0)
        self.assertTrue(health["influx_ok"])
        self.assertEqual(health["skipped_channels"], 2)


    def test_overflow_during_write_does_not_remove_replacement_batch(self):
        self.queue.enqueue([{"values": {0: 1}}], "first", 0, None)
        written = []

        def write(batches):
            written.append(batches[0]["values"][0])
            if len(written) == 1:
                self.queue.enqueue([{"values": {0: 2}}], "second", 0, None)
            self.queue.writer_stop_event.set()
            return []

        with patch.object(self.queue, "settings", RedLabSettings(write_queue_max_batches=1)):
            with patch.object(self.queue, "_write_batches", side_effect=write):
                self.queue.run()
        self.assertEqual(written, [1, 2])
        self.assertEqual(self.queue._queued_batch_count(), 0)


    def test_writer_idle_stop_exits(self):
        self.queue.writer_stop_event.set()
        with patch.object(self.queue, "_write_batches") as write:
            self.queue.run()
        write.assert_not_called()


    @unittest.skipUnless(_INFLUXDB_AVAILABLE, "influxdb_client not installed")
    def test_records_preserve_device_channel_and_sample_timestamp(self):
        with patch.object(self.mod, "build_points", wraps=self.mod.build_points):
            self.queue._write_batches([{"values": {0: 23.5}, "ts_ns": 123}])
        args = self.queue.write_api.write.call_args.args
        self.assertEqual(args[:2], ("bucket", "org"))
        self.assertEqual(args[2][0].to_line_protocol(), "redlab,channel=ch0,device=redlab_test value=23.5 123")


    @unittest.skipUnless(_INFLUXDB_AVAILABLE, "influxdb_client not installed")
    def test_writer_uses_configured_channel_names(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "redlab_channels.json"
            state_file.write_text(json.dumps({"redlab_test|ch0": {"enabled": True, "name": "Exhaust"}}), encoding="utf-8")
            self.queue.settings = RedLabSettings(channel_state_path=state_file, bucket="bucket", org="org")
            self.queue._write_batches([{"values": {0: 45.0}, "ts_ns": 456}])
            args = self.queue.write_api.write.call_args.args
            self.assertIn("channel_name=Exhaust", args[2][0].to_line_protocol())


if __name__ == "__main__":
    unittest.main()
