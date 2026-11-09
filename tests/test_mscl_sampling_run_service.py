import unittest

from app.mscl.mscl_sampling_run_service import start_sampling_run


class _FakeState:
    def __init__(self):
        self.BASE_STATION = object()
        self.NODE_READ_CACHE = {}
        self.SAMPLE_STOP_TOKENS = {}
        self.SAMPLE_RUNS = {}


class _FakeWirelessTypes:
    samplingMode_sync = 1


class _FakeChannelMask:
    def __init__(self):
        self.enabled_ids = []

    def enable(self, cid):
        self.enabled_ids.append(cid)


class _FakeCfg:
    def samplingMode(self, _value):
        return None

    def unlimitedDuration(self, _value):
        return None

    def activeChannels(self, _mask):
        return None

    def sampleRate(self, _value):
        return None


class _FakeIssues:
    def __iter__(self):
        return iter([])


class _FakeNode:
    def __init__(self, _node_id, _base):
        pass

    def readWriteRetries(self, _value):
        return None

    def getActiveChannels(self):
        class _Mask:
            @staticmethod
            def enabled(_cid):
                return False

        return _Mask()

    def verifyConfig(self, _cfg, _issues):
        return True

    def applyConfig(self, _cfg):
        return None

    def getSampleRate(self):
        return 112

    def getDataMode(self):
        return 1

    def getDataCollectionMethod(self):
        return 1

    def getSamplingMode(self):
        return 1


class _FakeMscl:
    WirelessTypes = _FakeWirelessTypes
    ChannelMask = _FakeChannelMask
    ConfigIssues = _FakeIssues
    WirelessNodeConfig = _FakeCfg
    WirelessNode = _FakeNode


class SamplingRunServiceTests(unittest.TestCase):
    def test_disconnected(self):
        state = _FakeState()
        state.BASE_STATION = None
        out = start_sampling_run(
            node_id=1,
            body={},
            internal_connect=lambda: (False, "offline"),
            state=state,
            ensure_beacon_on=lambda: None,
            mscl_mod=_FakeMscl,
            log_func=lambda _m: None,
            rate_map={},
            sampling_mode_labels={"transmit": "Transmit"},
            duration_to_seconds_fn=lambda _v, _u, _c: 0,
            set_sampling_mode_fn=lambda _cfg, _mode: (1, None),
            set_sampling_data_type_fn=lambda _cfg, _data_type: ("float", None),
            start_sampling_via_sync_network_fn=lambda _node, _node_id: "sync-network",
            schedule_idle_after_fn=lambda *_args: None,
        )
        self.assertFalse(out["success"])
        self.assertIn("Base station not connected", out["error"])

    def test_success_sets_run(self):
        state = _FakeState()
        out = start_sampling_run(
            node_id=16904,
            body={"log_transmit_mode": "transmit", "continuous": True},
            internal_connect=lambda: (True, "ok"),
            state=state,
            ensure_beacon_on=lambda: None,
            mscl_mod=_FakeMscl,
            log_func=lambda _m: None,
            rate_map={112: "64 Hz"},
            sampling_mode_labels={"transmit": "Transmit"},
            duration_to_seconds_fn=lambda _v, _u, _c: 0,
            set_sampling_mode_fn=lambda _cfg, _mode: (1, None),
            set_sampling_data_type_fn=lambda _cfg, _data_type: ("float", None),
            start_sampling_via_sync_network_fn=lambda _node, _node_id: "sync-network",
            schedule_idle_after_fn=lambda *_args: None,
        )
        self.assertTrue(out["success"])
        self.assertEqual(out["run"]["state"], "running")
        self.assertEqual(state.SAMPLE_RUNS[16904]["state"], "running")

    def test_calibrated_data_type_is_applied(self):
        state = _FakeState()
        seen = {"data_type": None}

        def _set_data_type(_cfg, data_type):
            seen["data_type"] = data_type
            return ("calibrated", None)

        out = start_sampling_run(
            node_id=16904,
            body={"log_transmit_mode": "transmit", "continuous": True, "data_type": "calibrated"},
            internal_connect=lambda: (True, "ok"),
            state=state,
            ensure_beacon_on=lambda: None,
            mscl_mod=_FakeMscl,
            log_func=lambda _m: None,
            rate_map={112: "64 Hz"},
            sampling_mode_labels={"transmit": "Transmit"},
            duration_to_seconds_fn=lambda _v, _u, _c: 0,
            set_sampling_mode_fn=lambda _cfg, _mode: (1, None),
            set_sampling_data_type_fn=_set_data_type,
            start_sampling_via_sync_network_fn=lambda _node, _node_id: "sync-network",
            schedule_idle_after_fn=lambda *_args: None,
        )
        self.assertTrue(out["success"])
        self.assertEqual(seen["data_type"], "calibrated")
        self.assertEqual(out["run"]["data_type"], "calibrated")
        self.assertEqual(out["run"]["data_type_value"], "calibrated")


if __name__ == "__main__":
    unittest.main()
