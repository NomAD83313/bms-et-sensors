import unittest

from app.pyrometers.pyrometers_stream_state import stream_is_stale


class PyrometersStreamStateTest(unittest.TestCase):
    def test_stream_is_not_stale_before_first_open(self):
        self.assertFalse(
            stream_is_stale(
                now_monotonic=10.0,
                stale_after_sec=3.0,
                last_valid_frame_monotonic=None,
                stream_opened_monotonic=None,
            )
        )

    def test_stream_becomes_stale_after_open_without_valid_frames(self):
        self.assertTrue(
            stream_is_stale(
                now_monotonic=13.1,
                stale_after_sec=3.0,
                last_valid_frame_monotonic=None,
                stream_opened_monotonic=10.0,
            )
        )

    def test_valid_frame_refreshes_stale_timer(self):
        self.assertFalse(
            stream_is_stale(
                now_monotonic=13.1,
                stale_after_sec=3.0,
                last_valid_frame_monotonic=12.0,
                stream_opened_monotonic=10.0,
            )
        )

    def test_stale_check_can_be_disabled(self):
        self.assertFalse(
            stream_is_stale(
                now_monotonic=20.0,
                stale_after_sec=0.0,
                last_valid_frame_monotonic=10.0,
                stream_opened_monotonic=10.0,
            )
        )


if __name__ == "__main__":
    unittest.main()
