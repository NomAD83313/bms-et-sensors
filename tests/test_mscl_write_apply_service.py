import unittest

from app.mscl.mscl_write_apply_service import apply_write_connected


class _FakeMask:
    def __init__(self):
        self.enabled = []

    def enable(self, ch_id):
        self.enabled.append(int(ch_id))


class _FakeFeatures:
    def supportsDefaultMode(self):
        return True

    def supportsInactivityTimeout(self):
        return True

    def supportsCheckRadioInterval(self):
        return True

    def supportsTransducerType(self):
        return True

    def supportsTempSensorOptions(self):
        return True


class _FakeNode:
    def __init__(self, fail_default_once=False):
        self._fail_default_once = bool(fail_default_once)
        self._failed_once = False
        self.retries = None
        self.applied = []

    def readWriteRetries(self, retries):
        self.retries = int(retries)

    def features(self):
        return _FakeFeatures()

    def applyConfig(self, cfg):
        if getattr(cfg, "include_default_mode", False) and self._fail_default_once and not self._failed_once:
            self._failed_once = True
            raise RuntimeError("Default Mode is not supported")
        self.applied.append(cfg)


class _FakeCfg:
    def __init__(self, include_default_mode):
        self.include_default_mode = bool(include_default_mode)


class _FakeMscl:
    NODE = None

    @classmethod
    def WirelessNode(cls, _node_id, _base_station):
        return cls.NODE

    @staticmethod
    def ChannelMask():
        return _FakeMask()


def _parsed_payload():
    return {
        "sample_rate": 112,
        "tx_power": 10,
        "channels": [1, 2],
        "input_range": 3,
        "unit": 1,
        "cjc_unit": 2,
        "low_pass_filter": 4,
        "storage_limit_mode": 1,
        "lost_beacon_timeout": 120,
        "diagnostic_interval": 120,
        "lost_beacon_enabled": True,
        "diagnostic_enabled": True,
        "default_mode": 6,
        "inactivity_timeout": 3600,
        "inactivity_enabled": True,
        "check_radio_interval": 10,
        "data_mode": 1,
        "transducer_type": 1,
        "sensor_type": 0,
        "wire_type": 0,
    }


class WriteApplyServiceTests(unittest.TestCase):
    def test_apply_write_connected_happy_path(self):
        _FakeMscl.NODE = _FakeNode(fail_default_once=False)
        cache = {16904: {"model": "TC-Link-200-OEM"}}
        logs = []

        def build_cfg(**kwargs):
            return {
                "cfg": _FakeCfg(kwargs["include_default_mode"]),
                "supports_default_mode": True,
                "supports_inactivity_timeout": True,
                "supports_check_radio_interval": True,
                "supports_transducer_type": True,
                "supports_temp_sensor_options": True,
                "write_hw_effective": {"transducer_type": 1, "sensor_type": 0, "wire_type": 0},
            }

        def update_cache(**kwargs):
            out = dict(kwargs["cached"])
            out["updated"] = True
            out["current_rate"] = int(kwargs["sample_rate"])
            return out

        resp = apply_write_connected(
            node_id=16904,
            data={"node_id": 16904},
            base_station=object(),
            node_read_cache=cache,
            ensure_beacon_on_fn=lambda: None,
            mscl_mod=_FakeMscl,
            normalize_write_payload_fn=lambda **_k: _parsed_payload(),
            normalize_tx_power_fn=lambda *_a, **_k: {"tx_power": 10, "tx_enum": 2, "warning": None},
            is_tc_link_200_model_fn=lambda _m: True,
            feature_supported_fn=lambda features, method: bool(getattr(features, method)()),
            build_write_config_fn=build_cfg,
            update_write_cache_fn=update_cache,
            ch1_mask_fn=lambda: "ch1",
            ch2_mask_fn=lambda: "ch2",
            get_temp_sensor_options_fn=lambda _n: (None, "n/a"),
            set_temp_sensor_options_fn=lambda _cfg, _tso: (False, "n/a"),
            wt_fn=lambda _name, default: default,
            data_mode_labels={},
            unit_labels={},
            log_func=lambda msg: logs.append(msg),
            now_ts_fn=lambda: 1000.0,
            jsonify_fn=lambda **kwargs: kwargs,
        )

        self.assertEqual(resp, {"success": True})
        self.assertEqual(_FakeMscl.NODE.retries, 15)
        self.assertEqual(len(_FakeMscl.NODE.applied), 1)
        self.assertTrue(cache[16904]["updated"])
        self.assertEqual(cache[16904]["current_rate"], 112)

    def test_apply_write_connected_retries_without_default_mode(self):
        _FakeMscl.NODE = _FakeNode(fail_default_once=True)
        cache = {16904: {"model": "TC-Link-200-OEM"}}
        build_calls = []
        logs = []

        def build_cfg(**kwargs):
            build_calls.append(bool(kwargs["include_default_mode"]))
            return {
                "cfg": _FakeCfg(kwargs["include_default_mode"]),
                "supports_default_mode": not bool(kwargs["include_default_mode"]),
                "supports_inactivity_timeout": True,
                "supports_check_radio_interval": True,
                "supports_transducer_type": True,
                "supports_temp_sensor_options": True,
                "write_hw_effective": {"transducer_type": 1, "sensor_type": 0, "wire_type": 0},
            }

        def update_cache(**kwargs):
            out = dict(kwargs["cached"])
            out["current_rate"] = int(kwargs["sample_rate"])
            return out

        resp = apply_write_connected(
            node_id=16904,
            data={"node_id": 16904},
            base_station=object(),
            node_read_cache=cache,
            ensure_beacon_on_fn=lambda: None,
            mscl_mod=_FakeMscl,
            normalize_write_payload_fn=lambda **_k: _parsed_payload(),
            normalize_tx_power_fn=lambda *_a, **_k: {"tx_power": 10, "tx_enum": 2, "warning": None},
            is_tc_link_200_model_fn=lambda _m: True,
            feature_supported_fn=lambda features, method: bool(getattr(features, method)()),
            build_write_config_fn=build_cfg,
            update_write_cache_fn=update_cache,
            ch1_mask_fn=lambda: "ch1",
            ch2_mask_fn=lambda: "ch2",
            get_temp_sensor_options_fn=lambda _n: (None, "n/a"),
            set_temp_sensor_options_fn=lambda _cfg, _tso: (False, "n/a"),
            wt_fn=lambda _name, default: default,
            data_mode_labels={},
            unit_labels={},
            log_func=lambda msg: logs.append(msg),
            now_ts_fn=lambda: 1000.0,
            jsonify_fn=lambda **kwargs: kwargs,
        )

        self.assertEqual(resp, {"success": True})
        self.assertEqual(build_calls, [True, False])
        self.assertEqual(len(_FakeMscl.NODE.applied), 1)
        self.assertTrue(any("retry without Default Mode" in msg for msg in logs))


if __name__ == "__main__":
    unittest.main()
