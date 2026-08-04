from __future__ import annotations

from typing import Any

OPTRIS_BURST_CHANNEL_CODES = {
    "target_avg": 0x01,
    "target_act": 0x02,
    "internal": 0x03,
    "int": 0x03,
    "box": 0x04,
    "epsilon": 0x05,
    "transmission": 0x06,
    "process_avg": 0x07,
    "process_act": 0x08,
    "ambient": 0x0C,
}

CLASSIC_CT_BURST_CHANNEL_CODES = {
    "target": 0x1,
    "object": 0x1,
    "head": 0x2,
    "box": 0x3,
    "current_target": 0x4,
    "target_act": 0x4,
    "epsilon": 0x5,
    "transmission": 0x6,
}

OPTRIS_IR_FACTOR_MIN = 0.05
OPTRIS_IR_FACTOR_MAX = 1.10


def optris_checksum(payload: bytes) -> int:
    value = 0
    for item in payload:
        value ^= item
    return value


def _with_optional_checksum(payload: bytes, enabled: bool) -> bytes:
    if not enabled:
        return payload
    return payload + bytes([optris_checksum(payload)])


def build_read_serial_number_command() -> bytes:
    return b"\x0e"


def parse_serial_number_response(data: bytes) -> str | None:
    raw = bytes(data or b"")
    if len(raw) < 3:
        return None
    return str(int.from_bytes(raw[:3], "big", signed=False))


def _ir_factor_word(value: float) -> int:
    factor = float(value)
    if factor < OPTRIS_IR_FACTOR_MIN or factor > OPTRIS_IR_FACTOR_MAX:
        raise ValueError(f"IR factor must be between {OPTRIS_IR_FACTOR_MIN:.3f} and {OPTRIS_IR_FACTOR_MAX:.3f}")
    return int(round(factor * 1000.0))


def parse_optris_ir_factor_response(data: bytes) -> float | None:
    raw = bytes(data or b"")
    if len(raw) < 2:
        return None
    return int.from_bytes(raw[:2], "big", signed=False) / 1000.0


def build_optris_read_emissivity_command() -> bytes:
    return b"\x04"


def build_optris_read_transmissivity_command() -> bytes:
    return b"\x05"


def build_optris_set_emissivity_command(value: float) -> bytes:
    word = _ir_factor_word(value)
    payload = bytes([0x84, (word >> 8) & 0xFF, word & 0xFF])
    return payload + bytes([optris_checksum(payload)])


def build_optris_set_transmissivity_command(value: float) -> bytes:
    word = _ir_factor_word(value)
    payload = bytes([0x85, (word >> 8) & 0xFF, word & 0xFF])
    return payload + bytes([optris_checksum(payload)])


def _ambient_temperature_word(value_c: float) -> int:
    value = float(value_c)
    if value < -100.0 or value > 900.0:
        raise ValueError("ambient compensation temperature must be between -100.0 and 900.0 °C")
    return int(round((value + 100.0) * 10.0))


def build_optris_set_ambient_source_command(source: str) -> bytes:
    values = {
        "fixed": 0,
        "internal": 1,
        "head": 1,
        "mv_input": 2,
        "external": 2,
    }
    key = str(source or "").strip().lower()
    if key not in values:
        raise ValueError("ambient compensation source must be fixed, internal, or external")
    word = values[key]
    payload = bytes([0x13, 0x00, (word >> 8) & 0xFF, word & 0xFF])
    return payload + bytes([optris_checksum(payload)])


def build_optris_set_ambient_fixed_temperature_command(value_c: float) -> bytes:
    word = _ambient_temperature_word(value_c)
    payload = bytes([0x13, 0x01, (word >> 8) & 0xFF, word & 0xFF])
    return payload + bytes([optris_checksum(payload)])


def _normalize_hex_command(value: str) -> bytes:
    clean = str(value or "").strip().replace(" ", "")
    if not clean:
        return b""
    return bytes.fromhex(clean)


def normalize_hex_commands(values: tuple[str, ...] | list[str]) -> list[bytes]:
    return [command for command in (_normalize_hex_command(value) for value in values) if command]


def _burst_channel_code(value: str | int) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    if text.isdigit():
        return int(text, 10)
    if text not in OPTRIS_BURST_CHANNEL_CODES:
        raise ValueError(f"unknown burst channel: {value}")
    return OPTRIS_BURST_CHANNEL_CODES[text]


def build_optris_burst_value_command(channels: tuple[str, ...] | list[str]) -> bytes:
    codes = [_burst_channel_code(value) for value in channels]
    if len(codes) > 16:
        raise ValueError("burst channel list must contain at most 16 entries")
    payload = bytes([0x51, *codes, *([0x00] * (16 - len(codes)))])
    return payload + bytes([optris_checksum(payload)])


def build_optris_burst_mode_command(enabled: bool, interval_ms: int = 0) -> bytes:
    interval = max(0, min(int(interval_ms), 0xFFFF))
    payload = bytes([0x52, 0x01 if enabled else 0x00, (interval >> 8) & 0xFF, interval & 0xFF])
    return payload + bytes([optris_checksum(payload)])


def build_optris_burst_start_commands(channels: tuple[str, ...] | list[str], interval_ms: int) -> list[bytes]:
    return [
        build_optris_burst_value_command(channels),
        build_optris_burst_mode_command(True, interval_ms),
    ]


def build_optris_burst_stop_commands() -> list[bytes]:
    return [build_optris_burst_mode_command(False, 0)]


def _classic_ct_channel_code(value: str | int) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.startswith("0x"):
        return int(text, 16)
    if text.isdigit():
        return int(text, 10)
    if text not in CLASSIC_CT_BURST_CHANNEL_CODES:
        raise ValueError(f"unknown classic CT burst channel: {value}")
    return CLASSIC_CT_BURST_CHANNEL_CODES[text]


def build_classic_ct_burst_string_command(
    channels: tuple[str, ...] | list[str], checksum_enabled: bool = True
) -> bytes:
    codes = [_classic_ct_channel_code(value) for value in channels]
    if len(codes) > 8:
        raise ValueError("classic CT burst channel list must contain at most 8 entries")
    padded = codes + ([0x0] * (8 - len(codes)))
    payload = bytes([
        0x51,
        (padded[0] << 4) | padded[1],
        (padded[2] << 4) | padded[3],
        (padded[4] << 4) | padded[5],
        (padded[6] << 4) | padded[7],
    ])
    return _with_optional_checksum(payload, checksum_enabled)


def build_classic_ct_burst_start_commands(
    channels: tuple[str, ...] | list[str], checksum_enabled: bool = True
) -> list[bytes]:
    start_payload = bytes([0x52, 0x01])
    return [
        build_classic_ct_burst_string_command(channels, checksum_enabled),
        _with_optional_checksum(start_payload, checksum_enabled),
    ]


def build_classic_ct_burst_stop_commands(checksum_enabled: bool = True) -> list[bytes]:
    stop_payload = bytes([0x52, 0x00])
    return [_with_optional_checksum(stop_payload, checksum_enabled)]


def extract_binary_frames(blob: bytes) -> list[bytes]:
    BINARY_FRAME_MARKER = b"\xaa\xaa"
    FRAME_SIZE = 10
    WORD_COUNT = 4
    DUPLICATE_WORD_TOLERANCE = 5
    raw = bytes(blob or b"")
    frames: list[bytes] = []
    if len(raw) < FRAME_SIZE:
        return frames
    for marker_idx in range(0, len(raw) - FRAME_SIZE + 1):
        if raw[marker_idx:marker_idx + 2] != BINARY_FRAME_MARKER:
            continue
        frame = raw[marker_idx:marker_idx + FRAME_SIZE]
        if len(frame) != FRAME_SIZE:
            continue
        words = [
            int.from_bytes(frame[idx:idx + 2], "big", signed=False)
            for idx in range(2, FRAME_SIZE, 2)
        ]
        if len(words) != WORD_COUNT:
            continue
        # Both controllers currently duplicate the object temperature
        # in the first and last 16-bit words of the frame.
        if abs(words[0] - words[3]) > DUPLICATE_WORD_TOLERANCE:
            continue
        frames.append(frame)
    return frames


def extract_binary_frame(blob: bytes) -> bytes | None:
    frames = extract_binary_frames(blob)
    if not frames:
        return None
    return frames[-1]


def parse_binary_frame(blob: bytes) -> dict[str, Any] | None:
    frame = extract_binary_frame(blob)
    if frame is None:
        return None
    raw_words = [int.from_bytes(frame[idx:idx + 2], "big", signed=False) for idx in (2, 4, 6, 8)]
    temperatures_c = [((value - 1000.0) / 10.0) for value in raw_words]
    object_primary_c = temperatures_c[0]
    sensor_head_c = temperatures_c[1]
    controller_box_c = temperatures_c[2]
    object_duplicate_c = temperatures_c[3]
    object_temperature_c = object_duplicate_c
    return {
        "frame_hex": frame.hex(),
        "marker_hex": frame[0:2].hex(),
        "raw_frame_words": raw_words,
        "raw_words": raw_words[1:4],
        "channel_1_c": sensor_head_c,
        "channel_2_c": controller_box_c,
        "channel_3_c": object_temperature_c,
        "sensor_head_temperature_c": sensor_head_c,
        "controller_box_temperature_c": controller_box_c,
        "object_primary_temperature_c": object_primary_c,
        "object_duplicate_temperature_c": object_duplicate_c,
        "object_temperature_c": object_temperature_c,
        "labels": {
            "channel_1": "THead",
            "channel_2": "TBox",
            "channel_3": "TObj",
        },
        "value_c": object_temperature_c,
        "ok": True,
    }


def parse_binary_frames(blob: bytes) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for frame in extract_binary_frames(blob):
        item = parse_binary_frame(frame)
        if item is not None:
            parsed.append(item)
    return parsed


def parse_burst_word_frames(blob: bytes, channels: tuple[str, ...] | list[str]) -> tuple[list[dict[str, Any]], bytes]:
    channel_names = tuple(str(value).strip().lower() for value in channels if str(value).strip())
    frame_size = len(channel_names) * 2
    if frame_size <= 0:
        return [], bytes(blob or b"")
    raw = bytes(blob or b"")
    parsed: list[dict[str, Any]] = []
    consumed = 0
    for offset in range(0, len(raw) - frame_size + 1, frame_size):
        frame = raw[offset:offset + frame_size]
        if frame.startswith(b"\xaa\xaa"):
            consumed = offset + 2
            continue
        raw_words = [int.from_bytes(frame[idx:idx + 2], "big", signed=False) for idx in range(0, frame_size, 2)]
        temperatures_c = [((value - 1000.0) / 10.0) for value in raw_words]
        if any(value < -100.0 or value > 2000.0 for value in temperatures_c):
            consumed = offset + frame_size
            continue
        values_by_channel = {
            channel: value
            for channel, value in zip(channel_names, temperatures_c)
        }
        object_value = _object_value_from_channels(channel_names, temperatures_c)
        sensor_head_value = values_by_channel.get("internal")
        if sensor_head_value is None:
            sensor_head_value = values_by_channel.get("int")
        parsed.append({
            "frame_hex": frame.hex(),
            "raw_frame_words": raw_words,
            "raw_words": raw_words,
            "values_by_channel": values_by_channel,
            "object_temperature_c": object_value,
            "sensor_head_temperature_c": sensor_head_value,
            "controller_box_temperature_c": values_by_channel.get("box"),
            "value_c": object_value,
            "ok": object_value is not None,
        })
        consumed = offset + frame_size
    return parsed, raw[consumed:]


def parse_marked_burst_word_frames(
    blob: bytes, channels: tuple[str, ...] | list[str]
) -> tuple[list[dict[str, Any]], bytes]:
    channel_names = tuple(str(value).strip().lower() for value in channels if str(value).strip())
    payload_size = len(channel_names) * 2
    frame_size = 2 + payload_size
    if payload_size <= 0:
        return [], bytes(blob or b"")
    raw = bytes(blob or b"")
    parsed: list[dict[str, Any]] = []
    cursor = 0
    while True:
        marker_index = raw.find(b"\xaa\xaa", cursor)
        if marker_index < 0:
            return parsed, raw[-1:] if raw.endswith(b"\xaa") else b""
        if len(raw) - marker_index < frame_size:
            return parsed, raw[marker_index:]
        marked_frame = raw[marker_index:marker_index + frame_size]
        values, remainder = parse_burst_word_frames(marked_frame[2:], channel_names)
        if values and not remainder:
            values[0]["frame_hex"] = marked_frame.hex()
            values[0]["marker_hex"] = "aaaa"
            parsed.extend(values)
        cursor = marker_index + frame_size


def _object_value_from_channels(channel_names: tuple[str, ...], temperatures_c: list[float]) -> float | None:
    for channel, value in reversed(list(zip(channel_names, temperatures_c))):
        if any(token in channel for token in ("target", "object", "process")):
            return value
    return temperatures_c[0] if temperatures_c else None


def parse_poll_response(data: bytes) -> dict[str, Any] | None:
    if len(data) < 2:
        return None
    word = int.from_bytes(data[:2], "big", signed=False)
    value_c = (word - 1000.0) / 10.0
    return {
        "frame_hex": data[:2].hex(),
        "raw_frame_words": [word],
        "raw_words": [word],
        "object_temperature_c": value_c,
        "value_c": value_c,
        "ok": True,
    }
