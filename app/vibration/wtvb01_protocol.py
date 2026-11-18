"""WitMotion WTVB01-485 Modbus RTU protocol: frames, CRC and register decoding.

Source: "WTVB01-485 Data Sheet & User Manual V260508", sections 10.1-10.4.
Pure functions only; no serial I/O here.

Manual notes applied deliberately:
- The summary table (10.3) lists 0x50 as PIX, but the detailed section 10.4.11
  places CFX..DRMSX contiguously at 0x47..0x52, matching the Y/Z blocks. The
  detailed layout is used.
- Velocity is documented as 0..600 mm/s with a /100 scale (up to 60000 raw),
  which does not fit a signed 16-bit value. Non-negative quantities are
  therefore decoded as unsigned; acceleration, temperature, kurtosis and
  skewness are signed (the manual decodes every value as `short`, and a real
  sensor returned negative skewness).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FUNC_READ = 0x03
FUNC_WRITE = 0x06

DEFAULT_ADDRESS = 0x50
DEFAULT_BAUD = 9600
MIN_ADDRESS = 0x01
MAX_ADDRESS = 0x7F
MAX_READ_REGISTERS = 64

REG_SAVE = 0x00
REG_BAUD = 0x04
REG_ADDRESS = 0x1A
REG_UNLOCK = 0x69

UNLOCK_KEY = 0xB588
SAVE_VALUE = 0x0000
REBOOT_VALUE = 0x00FF

# Section 10.4.2: BAUD[3:0] codes.
BAUD_CODES = {4800: 0x01, 9600: 0x02, 19200: 0x03, 38400: 0x04, 57600: 0x05, 115200: 0x06, 230400: 0x07}
SUPPORTED_BAUDS = tuple(sorted(BAUD_CODES))


class ProtocolError(Exception):
    """Malformed, mismatched or corrupted response."""


class ModbusException(ProtocolError):
    """The device answered with a Modbus exception code."""

    def __init__(self, code: int):
        super().__init__(f"Modbus exception 0x{code:02X}")
        self.code = code


@dataclass(frozen=True)
class Field:
    register: int
    name: str
    scale: float
    signed: bool
    unit: str


def _axis_fields(start: int, template: list[tuple[str, float, bool, str]]) -> list[Field]:
    return [Field(start + index, name, scale, signed, unit) for index, (name, scale, signed, unit) in enumerate(template)]


def _stats_template(axis: str) -> list[tuple[str, float, bool, str]]:
    # Section 10.4.11-10.4.13: twelve values per axis, all scaled by 1/1000.
    return [
        (f"crest_factor_{axis}", 1000, False, ""),
        (f"kurtosis_{axis}", 1000, True, ""),
        (f"acc_abs_mean_{axis}_g", 1000, False, "g"),
        (f"acc_variance_{axis}", 1000, False, "g^2"),
        (f"acc_rms_{axis}_g", 1000, False, "g"),
        (f"waveform_index_{axis}", 1000, False, ""),
        (f"peak_index_{axis}", 1000, False, ""),
        (f"pulse_index_{axis}", 1000, False, ""),
        (f"skewness_{axis}", 1000, True, ""),
        (f"velocity_rms_{axis}_mm_s", 1000, False, "mm/s"),
        (f"velocity_kurtosis_{axis}", 1000, True, ""),
        (f"displacement_rms_{axis}_mm", 1000, False, "mm"),
    ]


def _energy_template(axis: str) -> list[tuple[str, float, bool, str]]:
    # Section 10.4.15-10.4.17: 8 focus points, 6 bands and the total, raw values.
    points = [(f"energy_{axis}_point{i}", 1, False, "") for i in range(1, 9)]
    bands = [(f"energy_{axis}_band{i}", 1, False, "") for i in range(1, 7)]
    return points + bands + [(f"energy_{axis}_total", 1, False, "")]


BASIC_FIELDS = [
    Field(0x34, "acc_x_g", 32768 / 16, True, "g"),
    Field(0x35, "acc_y_g", 32768 / 16, True, "g"),
    Field(0x36, "acc_z_g", 32768 / 16, True, "g"),
    Field(0x3A, "velocity_x_mm_s", 100, False, "mm/s"),
    Field(0x3B, "velocity_y_mm_s", 100, False, "mm/s"),
    Field(0x3C, "velocity_z_mm_s", 100, False, "mm/s"),
    Field(0x40, "temperature_c", 100, True, "°C"),
    Field(0x41, "displacement_x_um", 1, False, "µm"),
    Field(0x42, "displacement_y_um", 1, False, "µm"),
    Field(0x43, "displacement_z_um", 1, False, "µm"),
    Field(0x44, "frequency_x_hz", 10, False, "Hz"),
    Field(0x45, "frequency_y_hz", 10, False, "Hz"),
    Field(0x46, "frequency_z_hz", 10, False, "Hz"),
]
STATS_FIELDS = (
    _axis_fields(0x47, _stats_template("x"))
    + _axis_fields(0x53, _stats_template("y"))
    + _axis_fields(0x5F, _stats_template("z"))
    + [
        Field(0x6B, "fault_x", 1, False, ""),
        Field(0x6C, "fault_y", 1, False, ""),
        Field(0x6D, "fault_z", 1, False, ""),
    ]
)
ENERGY_FIELDS = (
    _axis_fields(0x6E, _energy_template("x"))
    + _axis_fields(0x7D, _energy_template("y"))
    + _axis_fields(0x8C, _energy_template("z"))
)

# Read blocks: (first register, count, fields). Each block is one 0x03 request.
BLOCK_BASIC = (0x34, 0x46 - 0x34 + 1, BASIC_FIELDS)
BLOCK_STATS = (0x47, 0x6D - 0x47 + 1, STATS_FIELDS)
BLOCK_ENERGY = (0x6E, 0x9A - 0x6E + 1, ENERGY_FIELDS)

READ_PROFILES = {
    "basic": (BLOCK_BASIC,),
    "extended": (BLOCK_BASIC, BLOCK_STATS),
    "full": (BLOCK_BASIC, BLOCK_STATS, BLOCK_ENERGY),
}
DEFAULT_READ_PROFILE = "extended"

# Measured on a WTVB01-485: statistics refresh every 100 ms and spectrum energy about
# once per second; reading them more often only repeats the same values.
# A sensor sometimes returns a statistics block computed over a transient: RMS registers
# read 0xFFFF and the other statistics are meaningless (observed: kurtosis -31, pulse index 15).
# Such a block is discarded as a whole.
INVALID_RAW = 0xFFFF
_RMS_SUFFIXES = ("velocity_rms_", "displacement_rms_")
BLOCK_SENTINEL_REGISTERS = {
    BLOCK_STATS[0]: tuple(f.register for f in STATS_FIELDS if f.name.startswith(_RMS_SUFFIXES)),
}


def block_valid(first_register: int, registers: list[int]) -> bool:
    """False when the block carries the sensor's 'not computed' marker."""
    return all(registers[r - first_register] != INVALID_RAW
               for r in BLOCK_SENTINEL_REGISTERS.get(first_register, ()))


BLOCK_REFRESH_SEC = {BLOCK_BASIC[0]: 0.0, BLOCK_STATS[0]: 0.1, BLOCK_ENERGY[0]: 1.0}

# Focus points per manual 10.3: point1 = 1/4, point2 = 1/2, point3 = 1x (fundamental),
# point4 = 2x ... point8 = 6x.
HARMONIC_POINTS = ("0.25x", "0.5x", "1x", "2x", "3x", "4x", "5x", "6x")
HARMONIC_BANDS = ("0-0.5x", "0.5-1x", "1-2x", "2-3x", "3-4x", "4-5x")


def derived_fields(values: dict[str, float]) -> dict[str, float]:
    """Harmonic ratios used for diagnosis: 2x/1x (misalignment, looseness) and
    0.5x/1x (rub, whirl). Omitted while the 1x energy is zero."""
    derived: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        fundamental = values.get(f"energy_{axis}_point3")
        if not fundamental:
            continue
        derived[f"energy_{axis}_ratio_2x_1x"] = round(values[f"energy_{axis}_point4"] / fundamental, 4)
        derived[f"energy_{axis}_ratio_half_1x"] = round(values[f"energy_{axis}_point2"] / fundamental, 4)
    return derived


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _with_crc(body: bytes) -> bytes:
    # Modbus RTU sends the CRC low byte first; the manual names them CRCH/CRCL
    # but its examples (e.g. 50 03 00 34 00 03 49 84) are standard Modbus order.
    crc = crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def _check_address(address: int) -> None:
    if not 0 <= address <= 0xFF:
        raise ValueError(f"invalid Modbus address {address}")


def read_request(address: int, register: int, count: int) -> bytes:
    _check_address(address)
    if not 1 <= count <= MAX_READ_REGISTERS:
        raise ValueError(f"invalid register count {count}")
    return _with_crc(bytes((address, FUNC_READ, register >> 8, register & 0xFF, count >> 8, count & 0xFF)))


def write_request(address: int, register: int, value: int) -> bytes:
    _check_address(address)
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"invalid register value {value}")
    return _with_crc(bytes((address, FUNC_WRITE, register >> 8, register & 0xFF, value >> 8, value & 0xFF)))


def read_response_length(count: int) -> int:
    return 5 + 2 * count


WRITE_RESPONSE_LENGTH = 8
EXCEPTION_RESPONSE_LENGTH = 5


def _verify_crc(frame: bytes) -> None:
    if len(frame) < 4 or crc16(frame[:-2]) != frame[-2] | (frame[-1] << 8):
        raise ProtocolError("CRC mismatch")


def _check_header(frame: bytes, address: int, function: int) -> None:
    if len(frame) >= EXCEPTION_RESPONSE_LENGTH and frame[0] == address and frame[1] == function | 0x80:
        _verify_crc(frame[:EXCEPTION_RESPONSE_LENGTH])
        raise ModbusException(frame[2])
    if len(frame) < 2 or frame[0] != address or frame[1] != function:
        raise ProtocolError(f"unexpected response header {frame[:2].hex()}")


def parse_read_response(frame: bytes, address: int, count: int) -> list[int]:
    """Return raw unsigned register values."""
    _check_header(frame, address, FUNC_READ)
    if len(frame) != read_response_length(count) or frame[2] != 2 * count:
        raise ProtocolError(f"unexpected read length {len(frame)}")
    _verify_crc(frame)
    payload = frame[3:-2]
    return [(payload[i] << 8) | payload[i + 1] for i in range(0, len(payload), 2)]


def parse_write_response(frame: bytes, request: bytes) -> None:
    _check_header(frame, request[0], FUNC_WRITE)
    _verify_crc(frame)
    if frame != request:
        raise ProtocolError("write echo mismatch")


def to_signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def decode_fields(first_register: int, registers: list[int], fields: list[Field]) -> dict[str, float]:
    values: dict[str, float] = {}
    for field in fields:
        raw = registers[field.register - first_register]
        number = to_signed(raw) if field.signed else raw
        values[field.name] = round(number / field.scale, 6) if field.scale != 1 else number
    return values


# --- raw acceleration FIFO (FFT input) --------------------------------------------
# Not in manual V260508; command from WitMotion support, behaviour measured on a
# WTVB01-485 (see docs/vibration.md "Raw acceleration FIFO"):
# - a read of one register at 0x2C starts a capture; there is no immediate reply;
# - while capturing, the sensor sends progress frames `addr 03 01 NH NL CRC` by itself
#   about every 250 ms, N = 3 + 6 * samples collected;
# - after ~4.2 s it sends `addr 03 00` + 1024 x (AX AY AZ, int16 big-endian) + CRC;
# - the samples have the opposite sign to registers 0x34..0x36 on all three axes.
REG_FIFO = 0x2C
FIFO_SAMPLES = 1024
FIFO_AXES = 3
FIFO_PAYLOAD_LENGTH = FIFO_SAMPLES * FIFO_AXES * 2
FIFO_FRAME_LENGTH = 3 + FIFO_PAYLOAD_LENGTH + 2
FIFO_PROGRESS_LENGTH = 7
FIFO_PROGRESS_MARK = 0x01
FIFO_DATA_MARK = 0x00
FIFO_SIGN = -1
ACC_SCALE_G = 16 / 32768
# Measured 245.6-246.4 samples/s on one sensor (host clock, 115200 and 230400 baud).
FIFO_DEFAULT_SAMPLE_RATE = 246.0
FIFO_SAMPLE_RATE_RANGE = (100.0, 1000.0)
# Sensor-side capture time; the frame transfer time is added per baud rate.
FIFO_CAPTURE_SEC = 4.3


def fifo_request(address: int) -> bytes:
    return read_request(address, REG_FIFO, 1)


def fifo_progress_samples(count: int) -> int:
    return max(0, (count - 3) // (2 * FIFO_AXES))


def split_fifo_stream(buffer: bytes, address: int) -> tuple[list[tuple[str, Any]], bytes]:
    """Split received bytes into FIFO events; returns (events, unconsumed tail).

    Events: ("progress", samples collected) and ("data", list of (x, y, z) in g).
    Bytes before a header (line turnaround noise) and progress frames with a bad
    CRC are skipped; a data frame with a bad CRC raises ProtocolError.
    """
    events: list[tuple[str, Any]] = []
    data = bytes(buffer)
    while True:
        start = -1
        for index in range(len(data) - 2):
            if data[index] == address and data[index + 1] == FUNC_READ and data[index + 2] in (FIFO_PROGRESS_MARK, FIFO_DATA_MARK):
                start = index
                break
        if start < 0:
            # Keep a possible partial header at the end.
            return events, data[-2:] if len(data) >= 2 else data
        data = data[start:]
        if data[2] == FIFO_PROGRESS_MARK:
            if len(data) < FIFO_PROGRESS_LENGTH:
                return events, data
            frame = data[:FIFO_PROGRESS_LENGTH]
            if crc16(frame[:-2]) == frame[-2] | (frame[-1] << 8):
                events.append(("progress", fifo_progress_samples((frame[3] << 8) | frame[4])))
                data = data[FIFO_PROGRESS_LENGTH:]
            else:
                data = data[1:]
            continue
        if len(data) < FIFO_FRAME_LENGTH:
            return events, data
        frame = data[:FIFO_FRAME_LENGTH]
        _verify_crc(frame)
        events.append(("data", decode_fifo_payload(frame[3:-2])))
        data = data[FIFO_FRAME_LENGTH:]


def decode_fifo_payload(payload: bytes) -> list[tuple[float, float, float]]:
    """Samples in g with the sign of registers 0x34..0x36."""
    if len(payload) % (2 * FIFO_AXES):
        raise ProtocolError(f"FIFO payload length {len(payload)} is not a multiple of 6")
    scale = FIFO_SIGN * ACC_SCALE_G
    values = [to_signed((payload[i] << 8) | payload[i + 1]) * scale for i in range(0, len(payload), 2)]
    return [tuple(values[i:i + FIFO_AXES]) for i in range(0, len(values), FIFO_AXES)]  # type: ignore[misc]


def estimate_sample_rate(progress: list[tuple[float, int]]) -> float | None:
    """Least-squares samples/s from (host time, samples collected) progress points.

    Returns None with fewer than three usable points or an implausible result.
    """
    points = [(t, n) for t, n in progress if n > 0]
    if len(points) < 3:
        return None
    mean_t = sum(t for t, _ in points) / len(points)
    mean_n = sum(n for _, n in points) / len(points)
    denominator = sum((t - mean_t) ** 2 for t, _ in points)
    if denominator <= 0:
        return None
    rate = sum((t - mean_t) * (n - mean_n) for t, n in points) / denominator
    low, high = FIFO_SAMPLE_RATE_RANGE
    return rate if low <= rate <= high else None


def unlock_request(address: int) -> bytes:
    return write_request(address, REG_UNLOCK, UNLOCK_KEY)


def save_request(address: int) -> bytes:
    return write_request(address, REG_SAVE, SAVE_VALUE)


def baud_code(baud: int) -> int:
    try:
        return BAUD_CODES[int(baud)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"unsupported baud rate {baud}") from None


def valid_address(address: int) -> bool:
    return isinstance(address, int) and not isinstance(address, bool) and MIN_ADDRESS <= address <= MAX_ADDRESS
