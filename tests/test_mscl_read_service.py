import json
import sys
import threading
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.mscl.mscl_read_service import ReadDependencies, read_node_config
from app.mscl.mscl_ui_helpers import collection_method_text, filter_default_modes_for_model, tx_power_options_for_model
from tests.isolated_import import load_isolated_module


class FakeNode:
    def __init__(self):
        self.calls = []
        self.getSampleRate = Mock(return_value=112)
        self.readWriteRetries = Mock()

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            if name == "model":
                return "TC-Link-200-OEM"
            if name == "nodeAddress":
                return 16904
            if name == "firmwareVersion":
                return "1.2"
            raise RuntimeError("unsupported")
        return call


def build_dependencies(node=None, cached=None):
    node = node or FakeNode()
    sdk = SimpleNamespace(WirelessTypes=SimpleNamespace(samplingMode_sync=1))
    sdk.WirelessNode = Mock(return_value=node)
    # Loading constants must not install the fake SDK or alter the process path.
    with patch.object(sys, "path", list(sys.path)):
        constants = load_isolated_module("app.mscl.mscl_constants", {"MSCL": sdk})
    values = {field.name: getattr(constants, field.name) for field in fields(ReadDependencies) if hasattr(constants, field.name)}
    lock = threading.RLock()
    state = SimpleNamespace(OP_LOCK=lock, NODE_READ_CACHE={16904: cached or {}}, BASE_STATION=object())
    values.update(
        state=state, mscl=sdk,
        time=SimpleNamespace(sleep=Mock(), time=lambda: 123.0),
        internal_connect=Mock(return_value=(True, "connected")),
        ensure_beacon_on=Mock(), log=Mock(), mark_base_disconnected=Mock(), metric_inc=Mock(),
        ch1_mask=lambda: 1, ch2_mask=lambda: 2,
        _classify_data_type_from_format=lambda value: "float",
        _feature_supported=lambda features, name: False,
        _filter_default_modes=lambda options: options,
        _filter_sample_rates_for_model=lambda model, rates, rate: rates,
        _get_temp_sensor_options=lambda node: (None, None),
        _node_state_info=lambda node: (None, "Unknown", None),
        _sample_rate_label=lambda rate, rate_obj=None: "1 Hz",
        collection_method_text=collection_method_text,
        filter_default_modes_for_model=filter_default_modes_for_model,
        tx_power_options_for_model=tx_power_options_for_model,
    )
    return ReadDependencies(**values), node


class MsclReadServiceTests(unittest.TestCase):
    def test_read_preserves_payload_for_optional_unsupported_parameters(self):
        deps, node = build_dependencies()
        payload = read_node_config(16904, deps)
        expected = json.loads(Path(__file__).with_name("fixtures").joinpath("mscl_read_payload.json").read_text())
        self.assertEqual(payload, expected)
        self.assertEqual(deps.state.NODE_READ_CACHE[16904], dict(payload, ts=123.0))
        node.readWriteRetries.assert_called_once_with(15)
        self.assertLess(node.calls.index("getActiveChannels"), node.calls.index("getInputRange"))
        self.assertLess(node.calls.index("getUnit"), node.calls.index("getStorageLimitMode"))

    def test_supported_features_and_active_channels_are_read(self):
        deps, node = build_dependencies()
        features = Mock()
        for name in ("dataModes", "inputRanges", "lowPassFilters", "units", "storageLimitModes", "defaultModes", "transducerTypes", "thermocoupleTypes"):
            getattr(features, name).return_value = []
        features.sampleRates.return_value = [112]
        node.features = lambda: features
        node.getActiveChannels = lambda: SimpleNamespace(enabled=lambda channel: channel == 1)
        node.getUnit = lambda mask=None: 0
        node.getInputRange = lambda mask: 99
        node.getDefaultMode = lambda: 6
        node.getInactivityTimeout = lambda: 60
        node.getCheckRadioInterval = lambda: 10
        node.getSamplingMode = lambda: 1
        payload = read_node_config(16904, deps)
        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["channels"], [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}])
        self.assertEqual(payload["sampling_mode"], "sync")
        self.assertTrue(payload["supports_default_mode"])
        self.assertTrue(payload["supports_inactivity_timeout"])
        self.assertEqual(payload["supported_rates"], [{"enum_val": 112, "str_val": "1 Hz"}])
        features.sampleRates.assert_called_once_with(1, 1, 0)

    def test_missing_base_preserves_five_attempts_and_error(self):
        deps, _ = build_dependencies()
        deps.state.BASE_STATION = None
        deps.internal_connect.return_value = (False, "missing")
        self.assertEqual(read_node_config(16904, deps), {"success": False, "error": "Base station not connected: missing"})
        self.assertEqual(deps.internal_connect.call_count, 5)
        self.assertEqual(deps.time.sleep.call_args_list, [unittest.mock.call(0.5)] * 5)
        deps.mscl.WirelessNode.assert_not_called()

    def test_sample_rate_eeprom_failure_keeps_cached_rate(self):
        deps, node = build_dependencies(cached={"current_rate": 112, "current_unit": 0})
        node.getSampleRate.side_effect = RuntimeError("EEPROM failed")
        payload = read_node_config(16904, deps)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["current_rate"], 112)
        self.assertEqual(payload["current_unit"], 0)
        self.assertEqual(node.getSampleRate.call_count, 2)
        deps.metric_inc.assert_called_once_with("eeprom_retries_read")
        deps.mark_base_disconnected.assert_not_called()

    def test_sample_rate_second_read_recovers(self):
        deps, node = build_dependencies()
        node.getSampleRate.side_effect = [RuntimeError("EEPROM failed"), 112]
        self.assertTrue(read_node_config(16904, deps)["success"])
        self.assertEqual(node.getSampleRate.call_count, 2)
        deps.time.sleep.assert_called_once_with(1.0)

    def test_non_eeprom_failure_marks_disconnected_and_exhausts_attempts(self):
        deps, node = build_dependencies()
        node.getSampleRate.side_effect = RuntimeError("radio failed")
        self.assertEqual(read_node_config(16904, deps), {"success": False, "error": "radio failed"})
        self.assertEqual(node.getSampleRate.call_count, 10)
        self.assertEqual(deps.mark_base_disconnected.call_count, 5)

    def test_eeprom_exception_in_attempt_preserves_exponential_backoff(self):
        deps, node = build_dependencies()
        class BrokenMask:
            def enabled(self, _channel):
                raise RuntimeError("EEPROM mask failed")
        node.getActiveChannels = lambda: BrokenMask()
        self.assertFalse(read_node_config(16904, deps)["success"])
        self.assertEqual([call.args[0] for call in deps.time.sleep.call_args_list], [0.5, 1.0, 2.0, 4.0, 4.0])
        deps.mark_base_disconnected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
