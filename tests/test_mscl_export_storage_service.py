import unittest

from app.mscl.mscl_export_storage_service import execute_export_storage_connected


class _FakeBaseStation:
    def __init__(self):
        self._timeout = 1000
        self._retries = 10

    def timeout(self, value=None):
        if value is None:
            return self._timeout
        self._timeout = int(value)
        return self._timeout

    def readWriteRetries(self, value=None):
        if value is None:
            return self._retries
        self._retries = int(value)
        return self._retries


class _FakeState:
    def __init__(self):
        self.BASE_STATION = _FakeBaseStation()


class _FakeDownloader:
    def __init__(self, mode):
        self.mode = mode
        self.calls = 0
        self._done = False

    def complete(self):
        return self._done

    def getNextData(self):
        self.calls += 1
        if self.mode == "always_fail":
            raise RuntimeError("Failed to download data from the Node.")
        self._done = True
        return [{"sweep": 1}, {"sweep": 2}]

    def percentComplete(self):
        return 57.9

    def sessionIndex(self):
        return 0

    def sampleRate(self):
        return "Every 2 min"


class _FakeWirelessNode:
    def __init__(self, _node_id, _base_station, attempt_no):
        self._attempt_no = int(attempt_no)

    def readWriteRetries(self, _value):
        return None

    def ping(self):
        return True

    def getNumDatalogSessions(self):
        return 1


class _FakeMscl:
    def __init__(self, per_attempt_mode):
        self._per_attempt_mode = list(per_attempt_mode)
        self.node_attempt = 0
        self.downloader_create_count = 0

    def WirelessNode(self, node_id, base_station):
        self.node_attempt += 1
        return _FakeWirelessNode(node_id, base_station, self.node_attempt)

    def DatalogDownloader(self, _node):
        self.downloader_create_count += 1
        idx = self.downloader_create_count - 1
        mode = self._per_attempt_mode[idx] if idx < len(self._per_attempt_mode) else self._per_attempt_mode[-1]
        return _FakeDownloader(mode)


class ExportStorageServiceTests(unittest.TestCase):
    def _base_kwargs(self, mscl_mod, send_idle_fn, logs):
        return dict(
            node_id=16904,
            export_format="none",
            ingest_influx=False,
            ui_from_raw=None,
            ui_to_raw=None,
            ui_window_from_ns=None,
            ui_window_to_ns=None,
            host_hours=None,
            state_module=_FakeState(),
            mscl_mod=mscl_mod,
            ensure_beacon_on_fn=lambda: None,
            pause_stream_reader_fn=lambda _sec, _reason="": None,
            send_idle_sensorconnect_style_fn=send_idle_fn,
            coerce_logged_sweeps_fn=lambda batch: list(batch),
            logged_sweep_rows_fn=lambda node_id, session_index, sample_rate_text, sweep: [
                {
                    "timestamp_utc": "2026-03-03T11:00:00.000000000Z",
                    "timestamp_ns": 1_772_462_400_000_000_000,
                    "node_id": int(node_id),
                    "session_index": session_index,
                    "sample_rate": sample_rate_text,
                    "channel": "ch1",
                    "channel_id": 1,
                    "value": float(sweep.get("sweep", 0.0)),
                    "tick": 1,
                    "cal_applied": True,
                }
            ],
            resolve_export_time_window_fn=lambda **_k: (None, None, None),
            filter_rows_by_host_window_fn=lambda rows, **_k: rows,
            backfill_rows_to_influx_stream_fn=lambda **_k: {"written": 0, "skipped_existing": 0},
            metric_inc_fn=lambda _name, _amount=1: None,
            log_func=lambda m: logs.append(str(m)),
            source_node_export="mscl_node_export",
            jsonify_fn=lambda **payload: payload,
            response_cls=lambda body, mimetype=None, headers=None: {
                "body": body,
                "mimetype": mimetype,
                "headers": headers or {},
            },
            send_file_fn=lambda *a, **k: {"send_file": True, "args": a, "kwargs": k},
        )

    def test_retries_after_transient_download_limit(self):
        logs = []
        mscl = _FakeMscl(per_attempt_mode=["always_fail", "success"])

        out = execute_export_storage_connected(
            **self._base_kwargs(
                mscl_mod=mscl,
                send_idle_fn=lambda *_a, **_k: {"state_confirmed": True, "reason": "ok"},
                logs=logs,
            )
        )

        self.assertTrue(out["success"])
        self.assertEqual(out["point_count"], 2)
        self.assertEqual(mscl.downloader_create_count, 2)
        self.assertTrue(any("restarting downloader" in line for line in logs))

    def test_requires_idle_confirmation_before_download(self):
        logs = []
        mscl = _FakeMscl(per_attempt_mode=["success"])
        idle_attempts = {"n": 0}

        def _idle_fn(*_a, **_k):
            idle_attempts["n"] += 1
            if idle_attempts["n"] == 1:
                return {"state_confirmed": False, "reason": "status.result=canceled"}
            return {"state_confirmed": True, "reason": "confirmed"}

        out = execute_export_storage_connected(
            **self._base_kwargs(
                mscl_mod=mscl,
                send_idle_fn=_idle_fn,
                logs=logs,
            )
        )

        self.assertTrue(out["success"])
        self.assertEqual(idle_attempts["n"], 2)
        self.assertEqual(mscl.downloader_create_count, 1)
        self.assertTrue(any("Idle not confirmed before export attempt 1" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
