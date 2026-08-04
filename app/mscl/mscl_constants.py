import sys
import re

mscl_path = '/usr/lib/python3.12/dist-packages'
if mscl_path not in sys.path:
    sys.path.append(mscl_path)
import MSCL as mscl  # type: ignore  # noqa: E402

def _build_rate_map():
    # Read sample-rate enums from installed MSCL to avoid hardcoded mismatch
    # across MSCL versions (enum ids are not guaranteed stable).
    def _label_from_name(name):
        if not name.startswith("sampleRate_"):
            return None
        suffix = name.split("sampleRate_", 1)[1]

        m = re.fullmatch(r"(\d+)kHz", suffix)
        if m:
            return f"{int(m.group(1))} kHz"

        m = re.fullmatch(r"(\d+)Hz", suffix)
        if m:
            return f"{int(m.group(1))} Hz"

        m = re.fullmatch(r"(\d+)Sec", suffix)
        if m:
            n = int(m.group(1))
            return f"every {n} second" + ("" if n == 1 else "s")

        m = re.fullmatch(r"(\d+)Min", suffix)
        if m:
            n = int(m.group(1))
            return f"every {n} minute" + ("" if n == 1 else "s")

        m = re.fullmatch(r"(\d+)Hours?", suffix)
        if m:
            n = int(m.group(1))
            return f"every {n} hour" + ("" if n == 1 else "s")

        return None

    out = {}
    for name in dir(mscl.WirelessTypes):
        if not name.startswith("sampleRate_"):
            continue
        label = _label_from_name(name)
        if not label:
            continue
        try:
            out[int(getattr(mscl.WirelessTypes, name))] = label
        except Exception:
            continue
    return out


RATE_MAP = _build_rate_map()
TC_LINK_200_RATE_ENUMS = {rid for rid, lbl in RATE_MAP.items() if lbl in {"1 Hz", "2 Hz", "4 Hz", "8 Hz", "16 Hz", "32 Hz", "64 Hz", "128 Hz"}}
COMM_PROTOCOL_MAP = {
    0: "LXRS",
    1: "LXRS+",
}
TX_POWER_ENUM_TO_DBM = {
    0: 20,
    1: 16,
    2: 10,
    3: 5,
    4: 0,
}
INPUT_RANGE_LABELS = {
    99: "+/-1.35 V or 0 to 1 mega-ohms (Gain: 1)",
    100: "+/-1.25 V or 0 to 10000 ohms (Gain: 2)",
    101: "+/-625 mV or 0 to 3333.3 ohms (Gain: 4)",
    102: "+/-312.5 mV or 0 to 1428.6 ohms (Gain: 8)",
    103: "+/-156.25 mV or 0 to 666.67 ohms (Gain: 16)",
    104: "+/-78.125 mV or 0 to 322.58 ohms (Gain: 32)",
    105: "+/-39.0625 mV or 0 to 158.73 ohms (Gain: 64)",
    106: "+/-19.5313 mV or 0 to 78.74 ohms (Gain: 128)",
    0: "+/-14.545 mV",
    1: "+/-10.236 mV",
    2: "+/-7.608 mV",
    3: "+/-4.046 mV",
    4: "+/-2.008 mV",
}
PRIMARY_INPUT_RANGES = {99, 100, 101, 102, 103}
LOW_PASS_LABELS = {
    294: "294 Hz",
    291: "12.66 Hz (92db 50/60 Hz rejection)",
    289: "2.6 Hz (120db 50/60 Hz rejection)",
    12: "12.66 Hz (92db 50/60 Hz rejection)",
    2: "2.6 Hz (120db 50/60 Hz rejection)",
}
STORAGE_LIMIT_LABELS = {
    0: "Overwrite",
    1: "Stop",
}
DEFAULT_MODE_LABELS = {
    0: "Idle",
    1: "Low Duty Cycle",
    5: "Sleep",
    6: "Sample",
}
DATA_MODE_LABELS = {
    1: "Live Radio",
    2: "Datalog Only",
    3: "Live Radio + Datalog",
}


def _wt(name: str, default=None):
    return getattr(mscl.WirelessTypes, name, default)


TRANSDUCER_LABELS = {
    _wt("transducer_thermocouple", 0): "Thermocouple",
    _wt("transducer_rtd", 1): "RTD",
    _wt("transducer_thermistor", 2): "Thermistor",
}

RTD_SENSOR_LABELS = {
    _wt("rtd_uncompensated", 0): "Uncompensated (Resistance)",
    _wt("rtd_pt10", 1): "PT10",
    _wt("rtd_pt50", 2): "PT50",
    _wt("rtd_pt100", 3): "PT100",
    _wt("rtd_pt200", 4): "PT200",
    _wt("rtd_pt500", 5): "PT500",
    _wt("rtd_pt1000", 6): "PT1000",
}

THERMISTOR_SENSOR_LABELS = {
    _wt("thermistor_uncompensated", 0): "Uncompensated",
    _wt("thermistor_44004_44033", 1): "44004 / 44033",
    _wt("thermistor_44005_44030", 2): "44005 / 44030",
    _wt("thermistor_44007_44034", 3): "44007 / 44034",
    _wt("thermistor_44006_44031", 4): "44006 / 44031",
    _wt("thermistor_44008_44032", 5): "44008 / 44032",
    _wt("thermistor_ysi_400", 6): "YSI 400",
}

THERMOCOUPLE_SENSOR_LABELS = {
    0: "Type J",
    1: "Type K",
    2: "Type T",
    3: "Type E",
    4: "Type R",
    5: "Type S",
    6: "Type B",
    7: "Type N",
}

RTD_WIRE_LABELS = {
    _wt("rtd_2wire", 0): "2 Wire",
    _wt("rtd_3wire", 1): "3 Wire",
    _wt("rtd_4wire", 2): "4 Wire",
}


def _build_unit_labels():
    labels = {}
    try:
        for name in dir(mscl.WirelessTypes):
            if not name.startswith("unit_"):
                continue
            try:
                val = int(getattr(mscl.WirelessTypes, name))
            except Exception:
                continue
            suffix = name.split("unit_", 1)[1].replace("_", " ").strip().lower()
            compact = suffix.replace(" ", "")
            if "milliohm" in compact:
                label = "Milliohm"
            elif "kiloohm" in compact:
                label = "Kiloohm"
            elif compact.endswith("ohm") or "resistance" in compact:
                label = "Ohm"
            else:
                label = suffix.title() if suffix else f"Value {val}"
            labels[val] = label
    except Exception:
        pass
    return labels


UNIT_LABELS = _build_unit_labels()
PRIMARY_UNIT_ORDER = ["Ohm", "Milliohm", "Kiloohm"]
TEMP_UNIT_ORDER = ["Celsius", "Fahrenheit", "Kelvin"]

SAMPLING_MODE_MAP = {
    "log": int(getattr(mscl.WirelessTypes, "collectionMethod_logOnly", 1)),
    "transmit": int(getattr(mscl.WirelessTypes, "collectionMethod_transmitOnly", 2)),
    "log_and_transmit": int(getattr(mscl.WirelessTypes, "collectionMethod_logAndTransmit", 3)),
}
SAMPLING_MODE_LABELS = {
    "log": "Log",
    "transmit": "Transmit",
    "log_and_transmit": "Log and Transmit",
}


def _unit_family(label: str | None) -> str | None:
    s = str(label or "").lower().replace(" ", "")
    if "milliohm" in s:
        return "Milliohm"
    if "kiloohm" in s:
        return "Kiloohm"
    if "ohm" in s:
        return "Ohm"
    return None


def _is_temp_unit(label: str | None) -> bool:
    s = str(label or "").lower()
    return ("celsius" in s) or ("fahrenheit" in s) or ("kelvin" in s)


__all__ = [
    "RATE_MAP",
    "TC_LINK_200_RATE_ENUMS",
    "COMM_PROTOCOL_MAP",
    "TX_POWER_ENUM_TO_DBM",
    "INPUT_RANGE_LABELS",
    "PRIMARY_INPUT_RANGES",
    "LOW_PASS_LABELS",
    "STORAGE_LIMIT_LABELS",
    "DEFAULT_MODE_LABELS",
    "DATA_MODE_LABELS",
    "TRANSDUCER_LABELS",
    "RTD_SENSOR_LABELS",
    "THERMISTOR_SENSOR_LABELS",
    "THERMOCOUPLE_SENSOR_LABELS",
    "RTD_WIRE_LABELS",
    "UNIT_LABELS",
    "PRIMARY_UNIT_ORDER",
    "TEMP_UNIT_ORDER",
    "_wt",
    "_unit_family",
    "_is_temp_unit",
    "SAMPLING_MODE_MAP",
    "SAMPLING_MODE_LABELS",
    "mscl",
]
