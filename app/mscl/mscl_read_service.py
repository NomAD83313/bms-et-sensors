"""Read MSCL node configuration without coupling device work to Flask."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ReadDependencies:
    COMM_PROTOCOL_MAP: Any
    DATA_MODE_LABELS: Any
    DEFAULT_MODE_LABELS: Any
    INPUT_RANGE_LABELS: Any
    LOW_PASS_LABELS: Any
    PRIMARY_INPUT_RANGES: Any
    PRIMARY_UNIT_ORDER: Any
    RTD_SENSOR_LABELS: Any
    RTD_WIRE_LABELS: Any
    SAMPLING_MODE_LABELS: Any
    SAMPLING_MODE_MAP: Any
    STORAGE_LIMIT_LABELS: Any
    TEMP_UNIT_ORDER: Any
    THERMISTOR_SENSOR_LABELS: Any
    THERMOCOUPLE_SENSOR_LABELS: Any
    TRANSDUCER_LABELS: Any
    TX_POWER_ENUM_TO_DBM: Any
    UNIT_LABELS: Any
    _classify_data_type_from_format: Callable
    _feature_supported: Callable
    _filter_default_modes: Callable
    _filter_sample_rates_for_model: Callable
    _get_temp_sensor_options: Callable
    _is_temp_unit: Callable
    _node_state_info: Callable
    _sample_rate_label: Callable
    _unit_family: Callable
    _wt: Callable
    ch1_mask: Callable
    ch2_mask: Callable
    collection_method_text: Callable
    ensure_beacon_on: Callable
    filter_default_modes_for_model: Callable
    internal_connect: Callable
    log: Callable
    mark_base_disconnected: Callable
    metric_inc: Callable
    mscl: Any
    state: Any
    time: Any
    tx_power_options_for_model: Callable


class _ReadAttempt:
    """Keep one attempt's values together; instances are never shared by requests."""

    def __init__(self, node_id, node, cached, current_rate, deps):
        self.node_id = node_id
        self.node = node
        self.cached = cached
        self.current_rate = current_rate
        self.deps = deps
        self.refresh_eeprom = True
        self.read_tag = "READ"

    def read_identity(self):
        try:
            self.active_mask = self.node.getActiveChannels()
        except Exception:
            self.active_mask = None

        # 3. Remaining fields are best-effort (do not fail read on EEPROM errors)
        self.model = self.cached.get("model", "TC-Link-200")
        if self.refresh_eeprom or "model" not in self.cached:
            try:
                self.model = self.node.model()
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: model read failed: {e}")

        self.sn = self.cached.get("sn", "N/A")
        try:
            self.sn = str(self.node.nodeAddress())
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: serial read failed: {e}")

        self.fw = self.cached.get("fw", "N/A")
        if self.refresh_eeprom or "fw" not in self.cached:
            try:
                self.fw = str(self.node.firmwareVersion())
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: firmware read failed: {e}")

        self.current_power = self.cached.get("current_power", 16)
        self.current_power_enum = self.cached.get("current_power_enum")
        if self.refresh_eeprom or "current_power" not in self.cached:
            try:
                p_raw = int(self.node.getTransmitPower())
                self.current_power_enum = p_raw
                self.current_power = self.deps.TX_POWER_ENUM_TO_DBM.get(p_raw, 16)
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: transmit power read failed: {e}")
        self.tx_power_options = self.deps.tx_power_options_for_model(self.model, self.current_power)
        self.comm_protocol = self.cached.get("comm_protocol")
        self.comm_protocol_text = self.cached.get("comm_protocol_text")
        if self.refresh_eeprom or "comm_protocol" not in self.cached:
            try:
                cp = int(self.node.communicationProtocol())
                self.comm_protocol = cp
                self.comm_protocol_text = self.deps.COMM_PROTOCOL_MAP.get(cp, f"Value {cp}")
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: communicationProtocol read failed: {e}")

        # Optional status fields (best-effort)
        try:
            self.region = str(self.node.regionCode())
        except Exception:
            self.region = None
        try:
            self.last_comm = str(self.node.lastCommunicationTime()).split(".")[0]
        except Exception:
            self.last_comm = None
        self.node_state, self.state_text, _ = self.deps._node_state_info(self.node)
        try:
            self.node_address = int(self.node.nodeAddress())
        except Exception:
            self.node_address = None
        try:
            freq_raw = self.node.frequency()
            try:
                freq_ch = int(freq_raw)
                self.frequency = f"{freq_ch} ({2404 + 2 * freq_ch} MHz)"
            except Exception:
                self.frequency = str(freq_raw)
        except Exception:
            self.frequency = None


    def read_storage_status(self):
        self.storage_capacity_raw = self.cached.get("storage_capacity_raw")
        if self.refresh_eeprom or "storage_capacity_raw" not in self.cached:
            try:
                self.storage_capacity_raw = int(self.node.dataStorageSize())
            except Exception:
                pass
        try:
            self.storage_pct = round(float(self.node.percentFull()), 2)
        except Exception:
            self.storage_pct = None
        self.sampling_mode = self.cached.get("sampling_mode")
        self.sampling_mode_raw = self.cached.get("sampling_mode_raw")
        if self.refresh_eeprom or "sampling_mode" not in self.cached:
            try:
                sampling_mode_val = self.node.getSamplingMode()
                try:
                    self.sampling_mode_raw = int(sampling_mode_val)
                except Exception:
                    self.sampling_mode_raw = None
                self.sampling_mode = "sync" if sampling_mode_val == self.deps.mscl.WirelessTypes.samplingMode_sync else "non_sync"
            except Exception:
                pass


    def read_data_modes(self):
        self.current_data_mode = self.cached.get("current_data_mode")
        self.data_mode_options = self.cached.get("data_mode_options", [])
        self.current_collection_method = self.cached.get("current_collection_method")
        if self.refresh_eeprom or "current_data_mode" not in self.cached:
            try:
                self.current_data_mode = int(self.node.getDataMode())
            except Exception:
                pass
        if self.refresh_eeprom or "current_collection_method" not in self.cached:
            try:
                self.current_collection_method = int(self.node.getDataCollectionMethod())
            except Exception:
                pass
        if self.refresh_eeprom or not self.data_mode_options:
            try:
                features = self.node.features()
                modes = []
                try:
                    modes = features.dataModes()
                except Exception:
                    modes = []
                opts = []
                for m in modes:
                    mi = int(m)
                    opts.append({"value": mi, "label": self.deps.DATA_MODE_LABELS.get(mi, f"Value {mi}")})
                self.data_mode_options = opts
            except Exception:
                if not self.data_mode_options:
                    self.data_mode_options = []
        if self.current_data_mode is not None:
            if all(x.get("value") != int(self.current_data_mode) for x in self.data_mode_options):
                self.data_mode_options.insert(
                    0,
                    {
                        "value": int(self.current_data_mode),
                        "label": self.deps.DATA_MODE_LABELS.get(int(self.current_data_mode), f"Value {int(self.current_data_mode)}"),
                    },
                )
        if not self.data_mode_options:
            self.data_mode_options = [
                {"value": 1, "label": self.deps.DATA_MODE_LABELS[1]},
                {"value": 2, "label": self.deps.DATA_MODE_LABELS[2]},
                {"value": 3, "label": self.deps.DATA_MODE_LABELS[3]},
            ]
        self.data_mode_text = (
            self.deps.DATA_MODE_LABELS.get(int(self.current_data_mode), f"Value {int(self.current_data_mode)}")
            if self.current_data_mode is not None
            else None
        )
        self.current_collection_method_text = self.deps.collection_method_text(
            self.current_collection_method,
            self.deps.SAMPLING_MODE_MAP,
            self.deps.SAMPLING_MODE_LABELS,
        )
        self.current_data_format = self.cached.get("current_data_format")
        if self.refresh_eeprom or "current_data_format" not in self.cached:
            try:
                self.current_data_format = int(self.node.getDataFormat())
            except Exception:
                pass
        self.current_data_type = self.deps._classify_data_type_from_format(self.current_data_format)


    def read_input_ranges(self):
        self.current_input_range = self.cached.get("current_input_range")
        self.supported_input_ranges = self.cached.get("supported_input_ranges", [])
        if self.refresh_eeprom or "current_input_range" not in self.cached:
            try:
                self.current_input_range = int(self.node.getInputRange(self.deps.ch1_mask()))
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getInputRange(ch1) failed: {e}")
        if self.refresh_eeprom or not self.supported_input_ranges:
            try:
                features = self.node.features()
                ir_values = []
                try:
                    ir_values = features.inputRanges()
                except Exception:
                    ir_values = []
                self.supported_input_ranges = []
                for ir in ir_values:
                    ir_int = int(ir)
                    self.supported_input_ranges.append({
                        "value": ir_int,
                        "label": self.deps.INPUT_RANGE_LABELS.get(ir_int, f"Value {ir_int}"),
                        "primary": ir_int in self.deps.PRIMARY_INPUT_RANGES,
                    })
                # Stable order: primary (SensorConnect top set) first, then others by value.
                self.supported_input_ranges.sort(
                    key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                )
                if len(self.supported_input_ranges) <= 1:
                    existing = {int(x.get("value")) for x in self.supported_input_ranges if x.get("value") is not None}
                    for v in (99, 100, 101, 102, 103):
                        if v not in existing:
                            self.supported_input_ranges.append({
                                "value": v,
                                "label": self.deps.INPUT_RANGE_LABELS[v],
                                "primary": True,
                            })
                    self.supported_input_ranges.sort(
                        key=lambda x: (0 if x.get("primary") else 1, int(x.get("value", 999999)))
                    )
                if self.current_input_range is not None and all(x.get("value") != int(self.current_input_range) for x in self.supported_input_ranges):
                    self.supported_input_ranges.insert(0, {
                        "value": int(self.current_input_range),
                        "label": self.deps.INPUT_RANGE_LABELS.get(int(self.current_input_range), f"Value {int(self.current_input_range)}"),
                        "primary": int(self.current_input_range) in self.deps.PRIMARY_INPUT_RANGES,
                    })
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/inputRanges failed: {e}")
        # Fallback: keep SensorConnect-like core list visible even when feature read fails.
        if not self.supported_input_ranges:
            self.supported_input_ranges = [
                {"value": 99, "label": self.deps.INPUT_RANGE_LABELS[99], "primary": True},
                {"value": 100, "label": self.deps.INPUT_RANGE_LABELS[100], "primary": True},
                {"value": 101, "label": self.deps.INPUT_RANGE_LABELS[101], "primary": True},
                {"value": 102, "label": self.deps.INPUT_RANGE_LABELS[102], "primary": True},
                {"value": 103, "label": self.deps.INPUT_RANGE_LABELS[103], "primary": True},
            ]
            if self.current_input_range is not None and all(x.get("value") != int(self.current_input_range) for x in self.supported_input_ranges):
                self.supported_input_ranges.insert(0, {
                    "value": int(self.current_input_range),
                    "label": self.deps.INPUT_RANGE_LABELS.get(int(self.current_input_range), f"Value {int(self.current_input_range)}"),
                    "primary": int(self.current_input_range) in self.deps.PRIMARY_INPUT_RANGES,
                })



    def read_low_pass(self):
        self.current_low_pass = self.cached.get("current_low_pass")
        self.low_pass_options = self.cached.get("low_pass_options", [])
        try:
            lp_raw = int(self.node.getLowPassFilter(self.deps.ch1_mask()))
            self.current_low_pass = lp_raw
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getLowPassFilter(ch1) failed: {e}")
        try:
            features = self.node.features()
            lpf = []
            try:
                lpf = features.lowPassFilters()
            except Exception:
                lpf = []
            opts = []
            for v in lpf:
                vi = int(v)
                opts.append({"value": vi, "label": self.deps.LOW_PASS_LABELS.get(vi, f"Value {vi}")})
            if not opts:
                opts = [{"value": 294, "label": self.deps.LOW_PASS_LABELS.get(294, "294 Hz")}]
            self.low_pass_options = opts
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/lowPassFilters failed: {e}")
            if not self.low_pass_options:
                self.low_pass_options = [{"value": 294, "label": self.deps.LOW_PASS_LABELS.get(294, "294 Hz")}]
        if self.current_low_pass is not None and all(x.get("value") != int(self.current_low_pass) for x in self.low_pass_options):
            self.low_pass_options.insert(0, {"value": int(self.current_low_pass), "label": self.deps.LOW_PASS_LABELS.get(int(self.current_low_pass), f"Value {int(self.current_low_pass)}")})
        if self.current_low_pass is None and self.low_pass_options:
            self.current_low_pass = int(self.low_pass_options[0]["value"])



    def read_units(self):
        self.current_unit = self.cached.get("current_unit")
        self.unit_options = self.cached.get("unit_options", [])
        if self.refresh_eeprom or "current_unit" not in self.cached:
            try:
                self.current_unit = int(self.node.getUnit(self.deps.ch1_mask()))
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getUnit(ch1) failed: {e}")
                try:
                    self.current_unit = int(self.node.getUnit())
                except Exception:
                    pass
        if self.refresh_eeprom or not self.unit_options:
            try:
                features = self.node.features()
                values = []
                for getter in (lambda: features.units(self.deps.ch1_mask()), lambda: features.units()):
                    try:
                        values = getter()
                        if values:
                            break
                    except Exception:
                        continue
                self.unit_options = []
                for v in values:
                    vi = int(v)
                    self.unit_options.append({"value": vi, "label": self.deps.UNIT_LABELS.get(vi, f"Value {vi}")})
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/units failed: {e}")
        # SensorConnect-like behavior: keep core engineering units visible.
        if len(self.unit_options) <= 1:
            existing = {int(x.get("value")) for x in self.unit_options if x.get("value") is not None}
            for target_label in self.deps.PRIMARY_UNIT_ORDER:
                for unit_val, unit_label in self.deps.UNIT_LABELS.items():
                    if self.deps._unit_family(unit_label) == target_label and int(unit_val) not in existing:
                        self.unit_options.append({"value": int(unit_val), "label": unit_label})
                        existing.add(int(unit_val))
                        break
        if self.unit_options:
            self.unit_options.sort(
                key=lambda x: (
                    self.deps.PRIMARY_UNIT_ORDER.index(self.deps._unit_family(x.get("label"))) if self.deps._unit_family(x.get("label")) in self.deps.PRIMARY_UNIT_ORDER else 99,
                    str(x.get("label")),
                    int(x.get("value", 999999)),
                )
            )
        if self.current_unit is not None and all(x.get("value") != int(self.current_unit) for x in self.unit_options):
            self.unit_options.insert(0, {"value": int(self.current_unit), "label": self.deps.UNIT_LABELS.get(int(self.current_unit), f"Value {int(self.current_unit)}")})
        if not self.unit_options and self.current_unit is not None:
            self.unit_options = [{"value": int(self.current_unit), "label": self.deps.UNIT_LABELS.get(int(self.current_unit), f"Value {int(self.current_unit)}")}]

        self.current_cjc_unit = self.cached.get("current_cjc_unit")
        self.cjc_unit_options = self.cached.get("cjc_unit_options", [])
        if self.refresh_eeprom or "current_cjc_unit" not in self.cached:
            try:
                self.current_cjc_unit = int(self.node.getUnit(self.deps.ch2_mask()))
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getUnit(ch2) failed: {e}")
        if self.refresh_eeprom or not self.cjc_unit_options:
            try:
                features = self.node.features()
                values = []
                for getter in (lambda: features.units(self.deps.ch2_mask()), lambda: features.units()):
                    try:
                        values = getter()
                        if values:
                            break
                    except Exception:
                        continue
                self.cjc_unit_options = []
                for v in values:
                    vi = int(v)
                    lbl = self.deps.UNIT_LABELS.get(vi, f"Value {vi}")
                    if self.deps._is_temp_unit(lbl):
                        self.cjc_unit_options.append({"value": vi, "label": lbl})
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/units(ch2) failed: {e}")
        if len(self.cjc_unit_options) <= 1:
            existing = {int(x.get("value")) for x in self.cjc_unit_options if x.get("value") is not None}
            for target_label in self.deps.TEMP_UNIT_ORDER:
                for unit_val, unit_label in self.deps.UNIT_LABELS.items():
                    if (target_label.lower() in str(unit_label).lower()) and int(unit_val) not in existing:
                        self.cjc_unit_options.append({"value": int(unit_val), "label": unit_label})
                        existing.add(int(unit_val))
                        break
        if self.cjc_unit_options:
            self.cjc_unit_options.sort(
                key=lambda x: (
                    self.deps.TEMP_UNIT_ORDER.index(next((t for t in self.deps.TEMP_UNIT_ORDER if t.lower() in str(x.get("label", "")).lower()), self.deps.TEMP_UNIT_ORDER[0]))
                    if any(t.lower() in str(x.get("label", "")).lower() for t in self.deps.TEMP_UNIT_ORDER) else 99,
                    str(x.get("label")),
                    int(x.get("value", 999999)),
                )
            )
        if self.current_cjc_unit is not None and all(x.get("value") != int(self.current_cjc_unit) for x in self.cjc_unit_options):
            lbl = self.deps.UNIT_LABELS.get(int(self.current_cjc_unit), f"Value {int(self.current_cjc_unit)}")
            if self.deps._is_temp_unit(lbl):
                self.cjc_unit_options.insert(0, {"value": int(self.current_cjc_unit), "label": lbl})
        if not self.cjc_unit_options and self.current_cjc_unit is not None:
            lbl = self.deps.UNIT_LABELS.get(int(self.current_cjc_unit), f"Value {int(self.current_cjc_unit)}")
            if self.deps._is_temp_unit(lbl):
                self.cjc_unit_options = [{"value": int(self.current_cjc_unit), "label": lbl}]



    def read_storage_limits(self):
        self.current_storage_limit_mode = self.cached.get("current_storage_limit_mode")
        self.storage_limit_options = self.cached.get("storage_limit_options", [])
        try:
            self.current_storage_limit_mode = int(self.node.getStorageLimitMode())
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getStorageLimitMode failed: {e}")
        try:
            features = self.node.features()
            modes = []
            try:
                modes = features.storageLimitModes()
            except Exception:
                modes = []
            opts = []
            for v in modes:
                vi = int(v)
                opts.append({"value": vi, "label": self.deps.STORAGE_LIMIT_LABELS.get(vi, f"Value {vi}")})
            if not opts:
                opts = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
            self.storage_limit_options = opts
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/storageLimitModes failed: {e}")
            if not self.storage_limit_options:
                self.storage_limit_options = [{"value": 0, "label": "Overwrite"}, {"value": 1, "label": "Stop"}]
        if self.current_storage_limit_mode is not None and all(x.get("value") != int(self.current_storage_limit_mode) for x in self.storage_limit_options):
            self.storage_limit_options.insert(0, {"value": int(self.current_storage_limit_mode), "label": self.deps.STORAGE_LIMIT_LABELS.get(int(self.current_storage_limit_mode), f"Value {int(self.current_storage_limit_mode)}")})
        if self.current_storage_limit_mode is None and self.storage_limit_options:
            self.current_storage_limit_mode = int(self.storage_limit_options[0]["value"])

        self.current_lost_beacon_timeout = self.cached.get("current_lost_beacon_timeout")
        try:
            self.current_lost_beacon_timeout = int(self.node.getLostBeaconTimeout())
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getLostBeaconTimeout failed: {e}")
        if self.current_lost_beacon_timeout is None:
            self.current_lost_beacon_timeout = 2
        self.current_lost_beacon_enabled = bool(int(self.current_lost_beacon_timeout) > 0)

        self.current_diagnostic_interval = self.cached.get("current_diagnostic_interval")
        try:
            self.current_diagnostic_interval = int(self.node.getDiagnosticInterval())
        except Exception as e:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getDiagnosticInterval failed: {e}")
        if self.current_diagnostic_interval is None:
            self.current_diagnostic_interval = 60
        self.current_diagnostic_enabled = bool(int(self.current_diagnostic_interval) > 0)



    def read_sensor_options(self):
        self.supports_transducer_type = self.cached.get("supports_transducer_type")
        self.supports_temp_sensor_options = self.cached.get("supports_temp_sensor_options")
        self.current_transducer_type = self.cached.get("current_transducer_type")
        self.current_sensor_type = self.cached.get("current_sensor_type")
        self.current_wire_type = self.cached.get("current_wire_type")
        self.transducer_options = self.cached.get("transducer_options", [])
        self.rtd_sensor_options = self.cached.get("rtd_sensor_options", [])
        self.thermistor_sensor_options = self.cached.get("thermistor_sensor_options", [])
        self.thermocouple_sensor_options = self.cached.get("thermocouple_sensor_options", [])
        self.rtd_wire_options = self.cached.get("rtd_wire_options", [])

        self.supports_default_mode = self.cached.get("supports_default_mode")
        self.supports_inactivity_timeout = self.cached.get("supports_inactivity_timeout")
        self.supports_check_radio_interval = self.cached.get("supports_check_radio_interval")
        self.current_default_mode = self.cached.get("current_default_mode")
        self.current_inactivity_timeout = self.cached.get("current_inactivity_timeout")
        self.current_check_radio_interval = self.cached.get("current_check_radio_interval")
        self.default_mode_options = self.cached.get("default_mode_options", [])

        try:
            features = self.node.features()
        except Exception:
            features = None

        if features is not None:
            self.supports_default_mode = self.deps._feature_supported(features, "supportsDefaultMode")
            self.supports_inactivity_timeout = self.deps._feature_supported(features, "supportsInactivityTimeout")
            self.supports_check_radio_interval = self.deps._feature_supported(features, "supportsCheckRadioInterval")
            self.supports_transducer_type = self.deps._feature_supported(features, "supportsTransducerType")
            self.supports_temp_sensor_options = self.deps._feature_supported(features, "supportsTempSensorOptions")

            # SensorConnect-style behavior: try reads even when supports* says NO.
            try:
                self.current_default_mode = int(self.node.getDefaultMode())
                self.supports_default_mode = True
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getDefaultMode failed: {e}")
            try:
                modes = []
                try:
                    modes = features.defaultModes()
                except Exception:
                    modes = []
                self.default_mode_options = []
                for m in modes:
                    mi = int(m)
                    self.default_mode_options.append({
                        "value": mi,
                        "label": self.deps.DEFAULT_MODE_LABELS.get(mi, f"Value {mi}")
                    })
                self.default_mode_options = self.deps._filter_default_modes(self.default_mode_options)
                if self.default_mode_options:
                    self.supports_default_mode = True
                if not self.default_mode_options:
                    self.default_mode_options = [
                        {"value": 0, "label": "Idle"},
                        {"value": 5, "label": "Sleep"},
                        {"value": 6, "label": "Sample"},
                    ]
                    self.default_mode_options = self.deps._filter_default_modes(self.default_mode_options)
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/defaultModes failed: {e}")
                if not self.default_mode_options:
                    self.default_mode_options = [
                        {"value": 0, "label": "Idle"},
                        {"value": 5, "label": "Sleep"},
                        {"value": 6, "label": "Sample"},
                    ]
                self.default_mode_options = self.deps._filter_default_modes(self.default_mode_options)

            try:
                self.current_inactivity_timeout = int(self.node.getInactivityTimeout())
                self.supports_inactivity_timeout = True
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getInactivityTimeout failed: {e}")
            try:
                self.current_check_radio_interval = int(self.node.getCheckRadioInterval())
                self.supports_check_radio_interval = True
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getCheckRadioInterval failed: {e}")

            try:
                tr_types = []
                try:
                    tr_types = features.transducerTypes()
                except Exception:
                    tr_types = []
                self.transducer_options = []
                for v in tr_types:
                    vi = int(v)
                    self.transducer_options.append({"value": vi, "label": self.deps.TRANSDUCER_LABELS.get(vi, f"Value {vi}")})
                if not self.transducer_options:
                    self.transducer_options = [{"value": int(k), "label": v} for k, v in self.deps.TRANSDUCER_LABELS.items()]
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/transducerTypes failed: {e}")
                if not self.transducer_options:
                    self.transducer_options = [{"value": int(k), "label": v} for k, v in self.deps.TRANSDUCER_LABELS.items()]
            try:
                tc_types = []
                try:
                    tc_types = features.thermocoupleTypes()
                except Exception:
                    tc_types = []
                self.thermocouple_sensor_options = []
                for v in tc_types:
                    vi = int(v)
                    self.thermocouple_sensor_options.append({"value": vi, "label": self.deps.THERMOCOUPLE_SENSOR_LABELS.get(vi, f"Value {vi}")})
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/thermocoupleTypes failed: {e}")

        # SensorConnect-style behavior: read current temp sensor options even if supports* says NO.
        tso, tso_err = self.deps._get_temp_sensor_options(self.node)
        if tso is not None:
            try:
                self.current_transducer_type = int(tso.transducerType())
                self.supports_transducer_type = True
            except Exception:
                pass
            try:
                if self.current_transducer_type == self.deps._wt("transducer_rtd", 1):
                    self.current_sensor_type = int(tso.rtdType())
                elif self.current_transducer_type == self.deps._wt("transducer_thermistor", 2):
                    self.current_sensor_type = int(tso.thermistorType())
                elif self.current_transducer_type == self.deps._wt("transducer_thermocouple", 0):
                    self.current_sensor_type = int(tso.thermocoupleType())
            except Exception:
                pass
            try:
                self.current_wire_type = int(tso.rtdWireType())
            except Exception:
                pass
            self.supports_temp_sensor_options = True
        elif tso_err:
            self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: getTempSensorOptions failed: {tso_err}")

        if not self.transducer_options:
            self.transducer_options = [{"value": int(k), "label": v} for k, v in self.deps.TRANSDUCER_LABELS.items()]
        if self.current_transducer_type is not None and all(x.get("value") != int(self.current_transducer_type) for x in self.transducer_options):
            self.transducer_options.insert(0, {"value": int(self.current_transducer_type), "label": self.deps.TRANSDUCER_LABELS.get(int(self.current_transducer_type), f"Value {int(self.current_transducer_type)}")})
        if self.current_transducer_type is None and self.transducer_options:
            self.current_transducer_type = int(self.transducer_options[0]["value"])

        self.rtd_sensor_options = [{"value": int(k), "label": v} for k, v in self.deps.RTD_SENSOR_LABELS.items()]
        self.thermistor_sensor_options = [{"value": int(k), "label": v} for k, v in self.deps.THERMISTOR_SENSOR_LABELS.items()]
        if not self.thermocouple_sensor_options:
            self.thermocouple_sensor_options = [{"value": int(k), "label": v} for k, v in self.deps.THERMOCOUPLE_SENSOR_LABELS.items()]
        if self.current_transducer_type == self.deps._wt("transducer_thermocouple", 0) and self.current_sensor_type is not None:
            if all(x.get("value") != int(self.current_sensor_type) for x in self.thermocouple_sensor_options):
                self.thermocouple_sensor_options.insert(0, {"value": int(self.current_sensor_type), "label": self.deps.THERMOCOUPLE_SENSOR_LABELS.get(int(self.current_sensor_type), f"Value {int(self.current_sensor_type)}")})
        self.rtd_wire_options = [{"value": int(k), "label": v} for k, v in self.deps.RTD_WIRE_LABELS.items()]
        self.default_mode_options = self.deps.filter_default_modes_for_model(
            self.model,
            self.default_mode_options,
            self.deps.DEFAULT_MODE_LABELS,
            self.current_default_mode,
        )
        self.tx_power_options = self.deps.tx_power_options_for_model(self.model, self.current_power)



    def read_rates_and_channels(self):
        # Rates (when available)
        self.supported_rates = self.cached.get("supported_rates", [])
        if (self.refresh_eeprom or not self.supported_rates) and self.current_rate is not None:
            self.supported_rates = [{"enum_val": int(self.current_rate), "str_val": self.deps._sample_rate_label(self.current_rate)}]
            try:
                features = self.node.features()
                rates = features.sampleRates(self.deps.mscl.WirelessTypes.samplingMode_sync, 1, 0)
                self.supported_rates = []
                for r in rates:
                    rid = int(r)
                    self.supported_rates.append({"enum_val": rid, "str_val": self.deps._sample_rate_label(rid, r)})
            except Exception as e:
                self.deps.log(f"[mscl-web] [{self.read_tag}] warn node_id={self.node_id}: features/sampleRates failed: {e}")
        self.supported_rates = self.deps._filter_sample_rates_for_model(self.model, self.supported_rates, self.current_rate)

        self.channels = []
        if self.active_mask is not None:
            for i in range(1, 3):
                self.channels.append({"id": i, "enabled": self.active_mask.enabled(i)})
        elif isinstance(self.cached.get("channels"), list) and self.cached.get("channels"):
            self.channels = self.cached.get("channels")
        else:
            self.channels = [{"id": 1, "enabled": True}, {"id": 2, "enabled": False}]
        self.current_inactivity_enabled = bool((self.current_inactivity_timeout is not None) and (int(self.current_inactivity_timeout) > 0))



    def build_payload(self):
        payload = dict(
            success=True, model=self.model, sn=self.sn, fw=self.fw,
            region=self.region, last_comm=self.last_comm, state=self.node_state, state_text=self.state_text,
            node_address=self.node_address, frequency=self.frequency,
            storage_pct=self.storage_pct, storage_capacity_raw=self.storage_capacity_raw, sampling_mode=self.sampling_mode, sampling_mode_raw=self.sampling_mode_raw,
            current_data_mode=self.current_data_mode, data_mode_text=self.data_mode_text, data_mode_options=self.data_mode_options,
            current_collection_method=self.current_collection_method,
            current_collection_method_text=self.current_collection_method_text,
            current_data_format=self.current_data_format, current_data_type=self.current_data_type,
            current_input_range=self.current_input_range, supported_input_ranges=self.supported_input_ranges,
            current_unit=self.current_unit, unit_options=self.unit_options,
            current_cjc_unit=self.current_cjc_unit, cjc_unit_options=self.cjc_unit_options,
            current_rate=self.current_rate, current_power=self.current_power, current_power_enum=self.current_power_enum,
            tx_power_options=self.tx_power_options,
            comm_protocol=self.comm_protocol, comm_protocol_text=self.comm_protocol_text,
            supported_rates=self.supported_rates, channels=self.channels,
            current_low_pass=self.current_low_pass, low_pass_options=self.low_pass_options,
            current_storage_limit_mode=self.current_storage_limit_mode, storage_limit_options=self.storage_limit_options,
            current_lost_beacon_timeout=self.current_lost_beacon_timeout,
            current_lost_beacon_enabled=self.current_lost_beacon_enabled,
            current_diagnostic_interval=self.current_diagnostic_interval,
            current_diagnostic_enabled=self.current_diagnostic_enabled,
            supports_default_mode=bool(self.supports_default_mode),
            supports_inactivity_timeout=bool(self.supports_inactivity_timeout),
            supports_check_radio_interval=bool(self.supports_check_radio_interval),
            supports_transducer_type=bool(self.supports_transducer_type),
            supports_temp_sensor_options=bool(self.supports_temp_sensor_options),
            current_default_mode=self.current_default_mode,
            current_inactivity_timeout=self.current_inactivity_timeout,
            current_inactivity_enabled=self.current_inactivity_enabled,
            current_check_radio_interval=self.current_check_radio_interval,
            default_mode_options=self.default_mode_options,
            current_transducer_type=self.current_transducer_type,
            current_sensor_type=self.current_sensor_type,
            current_wire_type=self.current_wire_type,
            transducer_options=self.transducer_options,
            rtd_sensor_options=self.rtd_sensor_options,
            thermistor_sensor_options=self.thermistor_sensor_options,
            thermocouple_sensor_options=self.thermocouple_sensor_options,
            rtd_wire_options=self.rtd_wire_options,
        )
        return payload


def read_node_config(node_id: int, deps: ReadDependencies) -> dict[str, Any]:
    read_tag = "READ"
    max_attempts = 5
    last_err = None
    deps.log(f"[mscl-web] [{read_tag}] request node_id={node_id}")
    with deps.state.OP_LOCK:
        cached = deps.state.NODE_READ_CACHE.get(node_id, {})
        refresh_eeprom = True
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                deps.log(f"[mscl-web] [{read_tag}] retry {attempt}/{max_attempts} node_id={node_id}")
            ok, msg = deps.internal_connect()
            if not ok or deps.state.BASE_STATION is None:
                last_err = f"Base station not connected: {msg}"
                deps.log(f"[mscl-web] [{read_tag}] failed: {last_err}")
                deps.time.sleep(0.5)
                continue
            try:
                deps.ensure_beacon_on()
                node = deps.mscl.WirelessNode(node_id, deps.state.BASE_STATION)
                node.readWriteRetries(15)
                # 2. Critical values (use cache-first for EEPROM-heavy fields)
                current_rate = cached.get("current_rate")
                if refresh_eeprom or current_rate is None:
                    try:
                        current_rate = int(node.getSampleRate())
                    except Exception as e:
                        last_err = str(e)
                        deps.log(f"[mscl-web] [{read_tag}] warn node_id={node_id}: getSampleRate failed (1st): {last_err}")
                        deps.time.sleep(1.0)
                        try:
                            current_rate = int(node.getSampleRate())
                        except Exception as e2:
                            last_err = str(e2)
                            deps.log(f"[mscl-web] [{read_tag}] error node_id={node_id}: getSampleRate failed (2nd): {last_err}")
                            if "EEPROM" in last_err:
                                deps.metric_inc("eeprom_retries_read")
                            if "EEPROM" not in last_err:
                                deps.mark_base_disconnected()
                                deps.time.sleep(0.5)
                                continue
                read_attempt = _ReadAttempt(node_id, node, cached, current_rate, deps)
                read_attempt.read_identity()
                read_attempt.read_storage_status()
                read_attempt.read_data_modes()
                read_attempt.read_input_ranges()
                read_attempt.read_low_pass()
                read_attempt.read_units()
                read_attempt.read_storage_limits()
                read_attempt.read_sensor_options()
                read_attempt.read_rates_and_channels()
                payload = read_attempt.build_payload()
                deps.state.NODE_READ_CACHE[node_id] = dict(payload, ts=deps.time.time())
                deps.log(f"[mscl-web] [{read_tag}] success node_id={node_id} sample_rate={payload.get('current_rate')} fw={payload.get('fw')}")
                return payload
            except Exception as e:
                last_err = str(e)
                deps.log(f"[mscl-web] [{read_tag}] error node_id={node_id}: {e}")
                if "EEPROM" in last_err:
                    deps.metric_inc("eeprom_retries_read")
                    backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
                    deps.time.sleep(backoff)
                    continue
                deps.mark_base_disconnected()
                deps.time.sleep(0.5)
                continue
    if last_err:
        deps.log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: {last_err}")
    else:
        deps.log(f"[mscl-web] [{read_tag}] failed node_id={node_id}: Read failed")
    return {"success": False, "error": last_err or "Read failed"}
