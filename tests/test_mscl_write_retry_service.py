import unittest

from app.mscl.mscl_write_retry_service import run_write_retry_loop


class WriteRetryServiceTests(unittest.TestCase):
    def test_success_first_attempt(self):
        logs = []
        sleeps = []
        out = run_write_retry_loop(
            node_id=16904,
            max_attempts=5,
            log_func=lambda m: logs.append(m),
            sleep_fn=lambda s: sleeps.append(s),
            internal_connect_fn=lambda: (True, "ok"),
            base_connected_fn=lambda: True,
            connected_attempt_fn=lambda: {"success": True},
            metric_inc_fn=lambda _k: None,
            mark_base_disconnected_fn=lambda: None,
        )
        self.assertEqual(out["response"], {"success": True})
        self.assertIsNone(out["error"])
        self.assertEqual(sleeps, [])

    def test_retries_on_disconnected(self):
        logs = []
        sleeps = []
        calls = {"n": 0}

        def _connect():
            calls["n"] += 1
            if calls["n"] < 3:
                return (False, "offline")
            return (True, "ok")

        out = run_write_retry_loop(
            node_id=16904,
            max_attempts=5,
            log_func=lambda m: logs.append(m),
            sleep_fn=lambda s: sleeps.append(s),
            internal_connect_fn=_connect,
            base_connected_fn=lambda: calls["n"] >= 3,
            connected_attempt_fn=lambda: "done",
            metric_inc_fn=lambda _k: None,
            mark_base_disconnected_fn=lambda: None,
        )
        self.assertEqual(out["response"], "done")
        self.assertIsNone(out["error"])
        self.assertGreaterEqual(sleeps.count(0.5), 2)

    def test_eeprom_backoff_and_metric(self):
        sleeps = []
        metrics = []
        disconnects = []
        calls = {"n": 0}

        def _attempt():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("EEPROM read failed")
            return "ok"

        out = run_write_retry_loop(
            node_id=1,
            max_attempts=3,
            log_func=lambda _m: None,
            sleep_fn=lambda s: sleeps.append(s),
            internal_connect_fn=lambda: (True, "ok"),
            base_connected_fn=lambda: True,
            connected_attempt_fn=_attempt,
            metric_inc_fn=lambda k: metrics.append(k),
            mark_base_disconnected_fn=lambda: disconnects.append(True),
        )
        self.assertEqual(out["response"], "ok")
        self.assertIn("eeprom_retries_write", metrics)
        self.assertEqual(len(disconnects), 0)
        self.assertIn(0.5, sleeps)
        self.assertIn(1.0, sleeps)

    def test_marks_disconnected_on_non_eeprom(self):
        sleeps = []
        disconnects = []
        out = run_write_retry_loop(
            node_id=1,
            max_attempts=2,
            log_func=lambda _m: None,
            sleep_fn=lambda s: sleeps.append(s),
            internal_connect_fn=lambda: (True, "ok"),
            base_connected_fn=lambda: True,
            connected_attempt_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            metric_inc_fn=lambda _k: None,
            mark_base_disconnected_fn=lambda: disconnects.append(True),
        )
        self.assertIsNone(out["response"])
        self.assertIn("boom", out["error"])
        self.assertEqual(len(disconnects), 2)


if __name__ == "__main__":
    unittest.main()
