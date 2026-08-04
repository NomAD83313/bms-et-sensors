import unittest

from app.mscl.mscl_health_service import build_health_payload


class _FakeState:
    def __init__(self):
        self.BASE_STATION = object()
        self.LAST_PING_OK_TS = 0.0
        self.STREAM_PAUSE_UNTIL = 0.0
        self.PING_TTL_SEC = 10.0
        self.CURRENT_PORT = "/dev/ttyUSB1"


class HealthServiceTests(unittest.TestCase):
    def test_table_cases(self):
        now = 1000.0
        cases = [
            {
                "name": "ok",
                "connected": True,
                "last_ping_delta": 2.0,
                "pause_delta": -1.0,
                "expected_status": "ok",
                "expected_reasons": [],
            },
            {
                "name": "base_disconnected",
                "connected": False,
                "last_ping_delta": None,
                "pause_delta": -1.0,
                "expected_status": "degraded",
                "expected_reasons": ["base_disconnected"],
            },
            {
                "name": "ping_stale",
                "connected": True,
                "last_ping_delta": 20.0,
                "pause_delta": -1.0,
                "expected_status": "degraded",
                "expected_reasons": ["ping_stale"],
            },
            {
                "name": "stream_paused",
                "connected": True,
                "last_ping_delta": 2.0,
                "pause_delta": 5.0,
                "expected_status": "degraded",
                "expected_reasons": ["stream_paused"],
            },
            {
                "name": "all_reasons",
                "connected": False,
                "last_ping_delta": 20.0,
                "pause_delta": 5.0,
                "expected_status": "degraded",
                "expected_reasons": ["base_disconnected", "ping_stale", "stream_paused"],
            },
        ]

        for case in cases:
            st = _FakeState()
            st.BASE_STATION = object() if case["connected"] else None
            if case["last_ping_delta"] is None:
                st.LAST_PING_OK_TS = 0.0
            else:
                st.LAST_PING_OK_TS = now - float(case["last_ping_delta"])
            st.STREAM_PAUSE_UNTIL = now + float(case["pause_delta"])

            payload = build_health_payload(
                state=st,
                now=now,
                metric_snapshot_fn=lambda: {"stream_queue_depth": 7},
            )
            self.assertEqual(payload["status"], case["expected_status"])
            self.assertEqual(payload["reasons"], case["expected_reasons"])
            self.assertEqual(payload["stream_queue_depth"], 7)

    def test_pause_remaining(self):
        st = _FakeState()
        now = 1000.0
        st.LAST_PING_OK_TS = now - 1.0
        st.STREAM_PAUSE_UNTIL = now + 3.25
        payload = build_health_payload(state=st, now=now, metric_snapshot_fn=lambda: {})
        self.assertEqual(payload["stream_paused"], True)
        self.assertAlmostEqual(payload["stream_pause_remaining_sec"], 3.25, places=2)


if __name__ == "__main__":
    unittest.main()
