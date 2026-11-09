import unittest

from app.mscl.mscl_write_config_service import build_write_config


class _FakeCfg:
    def __init__(self):
        self.calls = []

    def samplingMode(self, v):
        self.calls.append(("samplingMode", v))

    def sampleRate(self, v):
        self.calls.append(("sampleRate", v))

    def transmitPower(self, v):
        self.calls.append(("transmitPower", v))

    def activeChannels(self, v):
        self.calls.append(("activeChannels", v))

    def defaultMode(self, v):
        self.calls.append(("defaultMode", v))


class _FakeWirelessTypes:
    samplingMode_sync = 1


class _FakeTempSensorOptions:
    @staticmethod
    def RTD(wire, sensor):
        return ("RTD", wire, sensor)

    @staticmethod
    def Thermistor(sensor):
        return ("Thermistor", sensor)

    @staticmethod
    def Thermocouple(sensor):
        return ("Thermocouple", sensor)


class _FakeMscl:
    WirelessTypes = _FakeWirelessTypes
    TempSensorOptions = _FakeTempSensorOptions

    @staticmethod
    def WirelessNodeConfig():
        return _FakeCfg()


class WriteConfigServiceTests(unittest.TestCase):
    def _build(self, include_default_mode, default_mode):
        logs = []
        out = build_write_config(
            mscl_mod=_FakeMscl,
            node=object(),
            node_id=16904,
            log_func=lambda m: logs.append(m),
            sample_rate=112,
            tx_enum=2,
            full_mask="mask",
            input_range=None,
            unit=None,
            cjc_unit=None,
            low_pass_filter=None,
            storage_limit_mode=None,
            lost_beacon_timeout=None,
            lost_beacon_enabled=False,
            diagnostic_interval=None,
            diagnostic_enabled=False,
            include_default_mode=include_default_mode,
            default_mode=default_mode,
            inactivity_timeout=None,
            inactivity_enabled=False,
            check_radio_interval=None,
            data_mode=None,
            transducer_type=None,
            sensor_type=None,
            wire_type=None,
            supports_default_mode=False,
            supports_inactivity_timeout=False,
            supports_check_radio_interval=False,
            supports_transducer_type=False,
            supports_temp_sensor_options=False,
            ch1_mask_fn=lambda: "ch1",
            ch2_mask_fn=lambda: "ch2",
            get_temp_sensor_options_fn=lambda _n: (None, "n/a"),
            set_temp_sensor_options_fn=lambda _cfg, _tso: (False, "n/a"),
            wt_fn=lambda _name, default: default,
            data_mode_labels={},
            unit_labels={},
        )
        return out, logs

    def test_build_base_fields(self):
        out, _logs = self._build(include_default_mode=False, default_mode=None)
        calls = out["cfg"].calls
        self.assertIn(("samplingMode", 1), calls)
        self.assertIn(("sampleRate", 112), calls)
        self.assertIn(("transmitPower", 2), calls)
        self.assertIn(("activeChannels", "mask"), calls)

    def test_default_mode_enabled(self):
        out, _logs = self._build(include_default_mode=True, default_mode=6)
        calls = out["cfg"].calls
        self.assertIn(("defaultMode", 6), calls)
        self.assertTrue(out["supports_default_mode"])

    def test_default_mode_skipped_when_disabled(self):
        out, _logs = self._build(include_default_mode=False, default_mode=6)
        calls = out["cfg"].calls
        self.assertNotIn(("defaultMode", 6), calls)


if __name__ == "__main__":
    unittest.main()
