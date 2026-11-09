import unittest

from app.mscl.mscl_write_cache_service import update_write_cache


class WriteCacheServiceTests(unittest.TestCase):
    def test_update_core_fields(self):
        out = update_write_cache(
            cached={},
            sample_rate=112,
            tx_power=10,
            tx_enum=2,
            input_range=7,
            unit=20,
            cjc_unit=21,
            low_pass_filter=3,
            storage_limit_mode=1,
            lost_beacon_timeout=125,
            lost_beacon_enabled=True,
            diagnostic_interval=120,
            diagnostic_enabled=True,
            supports_default_mode=True,
            default_mode=6,
            supports_inactivity_timeout=True,
            inactivity_timeout=3600,
            inactivity_enabled=True,
            supports_check_radio_interval=True,
            check_radio_interval=10,
            supports_transducer_type=True,
            transducer_type=1,
            supports_temp_sensor_options=True,
            sensor_type=2,
            wire_type=3,
            write_hw_effective={"transducer_type": None, "sensor_type": None, "wire_type": None},
            channels=[1, 2],
            now_ts=123.456,
        )
        self.assertEqual(out["current_rate"], 112)
        self.assertEqual(out["current_power"], 10)
        self.assertEqual(out["current_power_enum"], 2)
        self.assertEqual(out["current_input_range"], 7)
        self.assertEqual(out["current_unit"], 20)
        self.assertEqual(out["current_cjc_unit"], 21)
        self.assertEqual(out["current_low_pass"], 3)
        self.assertEqual(out["current_storage_limit_mode"], 1)
        self.assertEqual(out["current_lost_beacon_timeout"], 125)
        self.assertEqual(out["current_diagnostic_interval"], 120)
        self.assertEqual(out["current_default_mode"], 6)
        self.assertEqual(out["current_inactivity_timeout"], 3600)
        self.assertEqual(out["current_check_radio_interval"], 10)
        self.assertEqual(out["current_transducer_type"], 1)
        self.assertEqual(out["current_sensor_type"], 2)
        self.assertEqual(out["current_wire_type"], 3)
        self.assertEqual(out["channels"], [{"id": 1, "enabled": True}, {"id": 2, "enabled": True}])
        self.assertEqual(out["ts"], 123.456)

    def test_hw_effective_overrides_requested(self):
        out = update_write_cache(
            cached={},
            sample_rate=1,
            tx_power=0,
            tx_enum=4,
            input_range=None,
            unit=None,
            cjc_unit=None,
            low_pass_filter=None,
            storage_limit_mode=None,
            lost_beacon_timeout=None,
            lost_beacon_enabled=False,
            diagnostic_interval=None,
            diagnostic_enabled=False,
            supports_default_mode=False,
            default_mode=6,
            supports_inactivity_timeout=False,
            inactivity_timeout=1,
            inactivity_enabled=True,
            supports_check_radio_interval=False,
            check_radio_interval=1,
            supports_transducer_type=True,
            transducer_type=1,
            supports_temp_sensor_options=True,
            sensor_type=2,
            wire_type=3,
            write_hw_effective={"transducer_type": 9, "sensor_type": 8, "wire_type": 7},
            channels=[2],
            now_ts=1.0,
        )
        self.assertEqual(out["current_transducer_type"], 9)
        self.assertEqual(out["current_sensor_type"], 8)
        self.assertEqual(out["current_wire_type"], 7)
        self.assertEqual(out["channels"], [{"id": 1, "enabled": False}, {"id": 2, "enabled": True}])
        self.assertNotIn("current_default_mode", out)

    def test_disabled_timeouts_store_zero(self):
        out = update_write_cache(
            cached={},
            sample_rate=1,
            tx_power=0,
            tx_enum=4,
            input_range=None,
            unit=None,
            cjc_unit=None,
            low_pass_filter=None,
            storage_limit_mode=None,
            lost_beacon_timeout=125,
            lost_beacon_enabled=False,
            diagnostic_interval=120,
            diagnostic_enabled=False,
            supports_default_mode=False,
            default_mode=None,
            supports_inactivity_timeout=True,
            inactivity_timeout=3600,
            inactivity_enabled=False,
            supports_check_radio_interval=False,
            check_radio_interval=None,
            supports_transducer_type=False,
            transducer_type=None,
            supports_temp_sensor_options=False,
            sensor_type=None,
            wire_type=None,
            write_hw_effective={},
            channels=None,
            now_ts=2.0,
        )
        self.assertEqual(out["current_lost_beacon_timeout"], 0)
        self.assertEqual(out["current_diagnostic_interval"], 0)
        self.assertEqual(out["current_inactivity_timeout"], 0)
        self.assertEqual(out["channels"], [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}])


if __name__ == "__main__":
    unittest.main()
