import tempfile
import unittest
from pathlib import Path
import os
from app.cameras.recorder import prune, command, StreamProgress


class RetentionTests(unittest.TestCase):
    def test_oldest_removed_to_meet_budget_and_partial_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for name, stamp in [("a.mkv", 10), ("b.mkv", 20), ("active.partial", 1)]:
                path = directory / name
                path.write_bytes(b"1234")
                os.utime(path, (stamp, stamp))
            prune(directory, 4, 100, now=30)
            self.assertFalse((directory / "a.mkv").exists())
            self.assertTrue((directory / "b.mkv").exists())
            self.assertTrue((directory / "active.partial").exists())

    def test_expired_clip_removed_under_budget(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "old.mkv"
            path.write_bytes(b"x")
            os.utime(path, (1, 1))
            prune(Path(root), 100, 10, now=20)
            self.assertFalse(path.exists())

    def test_stream_copy_and_no_audio(self):
        args = command("rtsp://camera/stream", Path("clip.partial"), 60)
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertIn("-an", args)


if __name__ == "__main__":
    unittest.main()


class StreamProgressTests(unittest.TestCase):
    def test_no_video_and_stalled_video_are_connecting(self):
        progress = StreamProgress()
        self.assertEqual(progress.state(0), 'connecting')
        progress.feed(b'out_time_us=0\n', 1)
        self.assertEqual(progress.state(1), 'connecting')
        progress.feed(b'out_time_us=1000000\n', 2)
        self.assertEqual(progress.state(2), 'recording')
        progress.feed(b'out_time_us=1000000\n', 7)
        self.assertEqual(progress.state(8), 'connecting')
        progress.feed(b'out_time_us=2000000\n', 9)
        self.assertEqual(progress.state(9), 'recording')

    def test_partial_lines_and_unknown_timestamps(self):
        progress = StreamProgress()
        progress.feed(b'out_time_us=N/A\nout_time_', 1)
        self.assertEqual(progress.state(1), 'connecting')
        progress.feed(b'us=1000000\n', 2)
        self.assertEqual(progress.state(2), 'recording')
