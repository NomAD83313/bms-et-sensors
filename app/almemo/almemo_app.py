import os
import time
import threading
import re
import glob
from pathlib import Path
from typing import Any
from collections import deque
from datetime import datetime, timezone

from flask import Flask, jsonify, request
import serial  # type: ignore
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore

app = Flask(__name__)

ALMEMO_PORT_ENV = os.getenv("ALMEMO_PORT", "").strip()
ALMEMO_BAUD = 9600
ALMEMO_TIMEOUT_MS = int(os.getenv("ALMEMO_TIMEOUT_MS", "1500"))
ALMEMO_EOL = os.getenv("ALMEMO_EOL", "\\r\\n")
ALMEMO_PROBE_TIMEOUT_MS = int(os.getenv("ALMEMO_PROBE_TIMEOUT_MS", "2000"))
ALMEMO_HEALTH_PROBE_TIMEOUT_MS = int(os.getenv("ALMEMO_HEALTH_PROBE_TIMEOUT_MS", "1000"))
ALMEMO_DRAIN_MAX_SEC = float(os.getenv("ALMEMO_DRAIN_MAX_SEC", "1.2"))
ALMEMO_DRAIN_QUIET_SEC = float(os.getenv("ALMEMO_DRAIN_QUIET_SEC", "0.18"))
ALMEMO_LIVE_STALL_SEC = float(os.getenv("ALMEMO_LIVE_STALL_SEC", "12.0"))
ALMEMO_FAST_LIVE_STALL_SEC = float(os.getenv("ALMEMO_FAST_LIVE_STALL_SEC", "4.0"))
ALMEMO_MISSING_CACHE_SEC = float(os.getenv("ALMEMO_MISSING_CACHE_SEC", "2.0"))
ALMEMO_EXCLUDE_PORTS = os.getenv("ALMEMO_EXCLUDE_PORTS", "/dev/ttyMICROEPS*,/dev/ttyOPTRIS*,/dev/ttyPYRO*")
ALMEMO_EXCLUDE_USB_IDS = os.getenv("ALMEMO_EXCLUDE_USB_IDS", "10c4:834b")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086").strip()
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "").strip()
INFLUX_ORG = os.getenv("INFLUX_ORG", "").strip()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "").strip()
ALMEMO_MEASUREMENT = "almemo_live"
ALMEMO_DEVICE_NAME = os.getenv("ALMEMO_DEVICE_NAME", "almemo_logger").strip()
THREAD_JOIN_TIMEOUT = 4.0

# Global state for detected protocol version
ALMEMO_PROTOCOL_VERSION = "V6"  # Default to V6, updated during probe
VERSION_LOCK = threading.Lock()


def _get_protocol_version() -> str:
    with VERSION_LOCK:
        return ALMEMO_PROTOCOL_VERSION

# Cache for health status to avoid hammering the device
HEALTH_CACHE_LOCK = threading.Lock()
_LAST_HEALTH_DATA: dict[str, Any] = {}
_LAST_HEALTH_AT = 0.0
HEALTH_CACHE_TTL = 10.0

SERIAL_LOCK = threading.Lock()
SESSION_SWITCH_LOCK = threading.Lock()
HEALTH_PROBE_LOCK = threading.Lock()  # only one health probe at a time
_PSER: serial.Serial | None = None
_PSER_PORT: str = ""
LIVE_LOCK = threading.Lock()
LIVE_THREAD: threading.Thread | None = None
LIVE_STOP = threading.Event()
LIVE_LINES: deque[str] = deque(maxlen=400)
LIVE_CYCLE = "000001"
LIVE_LAST_DATA_AT = 0.0
FAST_LIVE_LOCK = threading.Lock()
FAST_LIVE_THREAD: threading.Thread | None = None
FAST_LIVE_STOP = threading.Event()
FAST_LIVE_LINES: deque[str] = deque(maxlen=800)
FAST_LIVE_RATE = "10"
FAST_LIVE_LAST_DATA_AT = 0.0
LAST_DEVICE_OK_AT = 0.0
LAST_DEVICE_MISSING_AT = 0.0
LAST_DEVICE_MISSING_REASON = ""
RECORD_START_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:\.\d{2})?\b")
CHANNEL_VALUE_RE = re.compile(
    r"(\d{2}):\s*([<>]?[+-]?\d+(?:\.\d*)?)\s+(\S+)(?:\s+(.*?))?(?=(?:\s+\d{2}:)|$)"
)
# V7: channel;value;unit (e.g. "0.0;23.5;C" or with time "12:34:00;0.0;23.5;C")
CHANNEL_VALUE_V7_RE = re.compile(
    r"(?:^|;)(\d+\.\d+);([<>]?[+-]?\d+(?:\.\d*)?);?([^;\s]+)?(?=(?:;|$))"
)
DEVICE_VERSION_RE = re.compile(r"^[A-Z]?\d{4}-[A-Z0-9]+(?:\s+\S.*)?$")


def _live_is_running() -> bool:
    return LIVE_THREAD is not None and LIVE_THREAD.is_alive()


def _join_thread(thread: threading.Thread | None, timeout_sec: float = THREAD_JOIN_TIMEOUT) -> None:
    if thread is None:
        return
    deadline = time.monotonic() + max(0.2, timeout_sec)
    while thread.is_alive() and time.monotonic() < deadline:
        thread.join(timeout=0.2)


def _stop_stream(thread: threading.Thread | None, stop_event: threading.Event) -> None:
    stop_event.set()
    _join_thread(thread)


def _mark_device_ok() -> None:
    global LAST_DEVICE_OK_AT, LAST_DEVICE_MISSING_AT, LAST_DEVICE_MISSING_REASON
    LAST_DEVICE_OK_AT = time.monotonic()
    LAST_DEVICE_MISSING_AT = 0.0
    LAST_DEVICE_MISSING_REASON = ""


def _mark_device_missing(reason: str) -> None:
    global LAST_DEVICE_MISSING_AT, LAST_DEVICE_MISSING_REASON
    LAST_DEVICE_MISSING_AT = time.monotonic()
    LAST_DEVICE_MISSING_REASON = reason.strip()


def _device_recently_ok(max_age_sec: float) -> bool:
    if LAST_DEVICE_OK_AT <= 0.0:
        return False
    return (time.monotonic() - LAST_DEVICE_OK_AT) <= max_age_sec


def _recent_missing_reason(max_age_sec: float) -> str | None:
    if LAST_DEVICE_MISSING_AT <= 0.0:
        return None
    if LAST_DEVICE_OK_AT > LAST_DEVICE_MISSING_AT:
        return None
    if (time.monotonic() - LAST_DEVICE_MISSING_AT) > max_age_sec:
        return None
    return LAST_DEVICE_MISSING_REASON or "ALMEMO 2490-2 not responding"


def _mark_live_data(kind: str) -> None:
    global LIVE_LAST_DATA_AT, FAST_LIVE_LAST_DATA_AT
    now = time.monotonic()
    if kind == "live":
        LIVE_LAST_DATA_AT = now
    else:
        FAST_LIVE_LAST_DATA_AT = now
    _mark_device_ok()


def _live_data_fresh() -> bool:
    return LIVE_LAST_DATA_AT > 0.0 and (time.monotonic() - LIVE_LAST_DATA_AT) <= max(1.0, ALMEMO_LIVE_STALL_SEC)


def _fast_live_data_fresh() -> bool:
    return FAST_LIVE_LAST_DATA_AT > 0.0 and (time.monotonic() - FAST_LIVE_LAST_DATA_AT) <= max(
        1.0, ALMEMO_FAST_LIVE_STALL_SEC
    )


def _live_push(line: str) -> None:
    if not line:
        return
    if not line.startswith("ERROR"):
        _mark_live_data("live")
    with LIVE_LOCK:
        LIVE_LINES.append(line)


def _fast_live_is_running() -> bool:
    return FAST_LIVE_THREAD is not None and FAST_LIVE_THREAD.is_alive()


def _log_serial_event(message: str) -> None:
    print(f">>> {message}", flush=True)


def _normalize_line_text(line: str) -> str:
    return line.replace("\r", "").replace("\n", "").replace("\x03", "").strip()


def _fast_live_push(line: str) -> None:
    if not line:
        return
    if not line.startswith("ERROR"):
        _mark_live_data("fast")
    with FAST_LIVE_LOCK:
        FAST_LIVE_LINES.append(line)


def _is_fast_live_data_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if text in {"G00", "f5", "ERROR"}:
        return False
    if text.startswith("\x03"):
        text = text.lstrip("\x03").strip()
    if text.startswith("k") and len(text) <= 3:
        return False

    version = _get_protocol_version()

    if version == "V7":
        # V7 fast live lines look like "0.0;23.5;C"
        return ";" in text and text[0:1].isdigit()
    else:
        # V6 fast live lines look like "01: 23.5 C"
        if text.startswith(("10:", "01:", "00:", "02:", "03:", "04:", "05:", "06:", "07:", "08:", "09:")):
            return True
        return bool(text and text[0:2].isdigit() and ":" in text)


def _is_print_live_data_line(line: str) -> bool:
    text = _normalize_line_text(line).lstrip("\x03").strip()
    if not text:
        return False
    if text == "ERROR":
        return False
    if text in {"C11", "G00", "S2"}:
        return False
    if text.startswith(("f5", "Z")):
        return False
    if text.startswith(("DATE:", "TIME:")):
        return True
    if _record_start(text):
        return True
    if _get_protocol_version() == "V7" and ";" in text:
        return True
    return CHANNEL_VALUE_RE.search(text) is not None


def _clean_fast_live_line(line: str) -> str:
    return _normalize_line_text(line)


def _influx_enabled() -> bool:
    return bool(INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET)


def _record_start(line: str) -> bool:
    return bool(RECORD_START_RE.match(line.strip()))


def _record_timestamp() -> datetime:
    return datetime.now(timezone.utc)


def _parse_record_points(record: str, mode: str) -> list[Point]:
    text = " ".join(record.strip().split())
    if not text:
        return []
    ts = _record_timestamp()
    payload = RECORD_START_RE.sub("", text, count=1).strip().lstrip("|").strip()
    payload = payload.replace("|", " ")
    points: list[Point] = []

    version = _get_protocol_version()
    regex = CHANNEL_VALUE_V7_RE if version == "V7" else CHANNEL_VALUE_RE

    for match in regex.finditer(payload):
        channel = match.group(1)
        try:
            value = float(match.group(2).lstrip("<>"))
        except ValueError:
            continue
        unit = (match.group(3) or "").strip()
        # V6 has sensor name as 4th group, V7 might not in this simple regex
        sensor = ""
        if version == "V6" and len(match.groups()) >= 4:
            sensor = (match.group(4) or "").strip()

        point = (
            Point(ALMEMO_MEASUREMENT)
            .tag("source", "almemo")
            .tag("device", ALMEMO_DEVICE_NAME)
            .tag("mode", mode)
            .tag("channel", channel)
            .field("value", value)
            .time(ts, WritePrecision.MS)
        )
        if unit:
            point.tag("unit", unit)
        if sensor:
            point.tag("sensor", sensor)
        points.append(point)
    return points


def _write_record_to_influx(write_api, record: str, mode: str) -> None:
    if write_api is None:
        return
    points = _parse_record_points(record, mode)
    if not points:
        return
    write_api.write(
        bucket=INFLUX_BUCKET,
        org=INFLUX_ORG,
        record=points,
        write_precision=WritePrecision.MS,
    )


def _normalize_live_cycle(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 6:
        return "000001"
    return digits


def _reader_loop(
    stop_event: threading.Event,
    push_fn,
    setup_cmds: tuple,
    setup_sleep: float,
    cleanup_cmds: tuple,
    cleanup_sleep: float,
    mode: str,
    *,
    filter_fn=None,
    push_clean: bool = False,
    channel_only_write: bool = False,
    stall_timeout_sec: float = 0.0,
    stall_fallback_cmds: tuple = (),
) -> None:
    influx_client = None
    write_api = None
    current_record = ""
    try:
        if _influx_enabled():
            influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            write_api = influx_client.write_api(write_options=SYNCHRONOUS)
        while not stop_event.is_set():
            try:
                ser, active_port = _open_verified_serial(ALMEMO_TIMEOUT_MS, xonxoff=True)
                with ser:
                    _log_serial_event(f"{mode}: reader attached to {active_port}")
                    for cmd in setup_cmds:
                        _write_line(ser, cmd, sleep_sec=0.03)
                        time.sleep(setup_sleep)
                    current_record = ""
                    last_data_at = time.monotonic()
                    fallback_sent = False
                    while not stop_event.is_set():
                        try:
                            raw = ser.readline()
                        except serial.SerialException as exc:
                            push_fn(f"ERROR: {exc}")
                            break
                        if not raw:
                            if stall_timeout_sec > 0 and (time.monotonic() - last_data_at) >= stall_timeout_sec:
                                if stall_fallback_cmds and not fallback_sent:
                                    for cmd in stall_fallback_cmds:
                                        _write_line(ser, cmd, sleep_sec=0.03)
                                        time.sleep(setup_sleep)
                                    fallback_sent = True
                                    last_data_at = time.monotonic()
                                    _log_serial_event(f"{mode}: fallback command(s) sent after data stall")
                                    continue
                                message = (
                                    f"ERROR: no ALMEMO data for {stall_timeout_sec:.1f}s on {active_port}; "
                                    "reconnecting"
                                )
                                push_fn(message)
                                _log_serial_event(message)
                                break
                            continue
                        last_data_at = time.monotonic()
                        line = raw.decode("latin-1", errors="replace").strip()
                        if not line:
                            continue
                        if filter_fn is not None and not filter_fn(line):
                            continue
                        fallback_sent = False
                        clean_line = line.replace("\r", "").lstrip("\x03").strip()
                        push_fn(clean_line if push_clean else line)
                        if _record_start(clean_line):
                            if current_record:
                                _write_record_to_influx(write_api, current_record, mode)
                            current_record = clean_line
                        elif current_record:
                            current_record = f"{current_record} {clean_line}".strip()
                        elif channel_only_write:
                            _write_record_to_influx(write_api, clean_line, mode)
                    try:
                        for cmd in cleanup_cmds:
                            _write_line(ser, cmd, sleep_sec=0.03)
                            if cleanup_sleep:
                                time.sleep(cleanup_sleep)
                        # drain until device goes quiet (ensures S2 stopped before port closes)
                        _discard_input_until_quiet(ser, max_sec=1.5, quiet_sec=0.15)
                    except Exception:
                        pass
            except serial.SerialException as exc:
                push_fn(f"ERROR: {exc}")
                _log_serial_event(f"{mode}: reconnect failed: {exc}")
                time.sleep(0.5)
    finally:
        if current_record:
            try:
                _write_record_to_influx(write_api, current_record, mode)
            except Exception:
                pass
        if influx_client is not None:
            influx_client.close()
        stop_event.clear()


def _live_setup_commands(version: str, cycle: str) -> tuple[str, ...]:
    if version == "V7":
        return ("C11", "f5 k5", "G00", f"Z{cycle}", "S2")
    # Some V6 ALMEMO 2490 units acknowledge S2 but do not emit printable
    # records until the first measurement channel is selected again.
    return ("C11", "f5 k-5", "f5 k-4", "f5 k-2", "G00", f"Z{cycle}", "S2", "M00")


def _live_reader(cycle: str) -> None:
    version = _get_protocol_version()
    setup = _live_setup_commands(version, cycle)
    # V6 ALMEMO 2490: M00 alone is not enough to recover a frozen print cycle.
    # Full stop (C11) + channel select (G00) + restart (S2) + measurement
    # select (M00) mirrors the original setup and reliably restarts the cycle
    # without needing a full serial reconnect.
    fallback = ("C11", "G00", f"Z{cycle}", "S2", "M00") if version == "V6" else ()

    with SERIAL_LOCK:
        _close_pser()
        _reader_loop(
            LIVE_STOP,
            _live_push,
            setup_cmds=setup,
            setup_sleep=0.15,
            cleanup_cmds=("C11",),
            cleanup_sleep=0.0,
            mode="print_cycle",
            filter_fn=_is_print_live_data_line,
            push_clean=True,
            stall_timeout_sec=max(1.0, ALMEMO_LIVE_STALL_SEC),
            stall_fallback_cmds=fallback,
        )


def _fast_live_rate_command(rate: str) -> str:
    return "f5 k0" if rate == "2.5" else "f5 k1"


def _fast_live_reader(rate: str) -> None:
    with SERIAL_LOCK:
        _close_pser()
        _reader_loop(
            FAST_LIVE_STOP,
            _fast_live_push,
            setup_cmds=("G00", _fast_live_rate_command(rate), "f5 k2", "f5 k5"),
            setup_sleep=0.12,
            cleanup_cmds=("f5 k-5", "f5 k-2"),
            cleanup_sleep=0.05,
            mode="continuous_query",
            filter_fn=_is_fast_live_data_line,
            push_clean=True,
            channel_only_write=True,
            stall_timeout_sec=max(1.0, ALMEMO_FAST_LIVE_STALL_SEC),
        )


def _serial_candidates() -> list[str]:
    candidates: list[str] = []
    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        for name in sorted(os.listdir(by_id)):
            candidates.append(os.path.join(by_id, name))
    for pattern in ("/dev/ttyUSB", "/dev/ttyACM"):
        for idx in range(0, 8):
            path = f"{pattern}{idx}"
            if os.path.exists(path):
                candidates.append(path)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _excluded_serial_realpaths() -> set[str]:
    excluded: set[str] = set()
    for raw_pattern in ALMEMO_EXCLUDE_PORTS.split(","):
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        matches = glob.glob(pattern) if any(ch in pattern for ch in "*?[]") else [pattern]
        for path in matches:
            if os.path.exists(path):
                excluded.add(os.path.realpath(path))
            else:
                excluded.add(path)
    return excluded


def _excluded_usb_ids() -> set[tuple[str, str]]:
    excluded: set[tuple[str, str]] = set()
    for raw_item in ALMEMO_EXCLUDE_USB_IDS.split(","):
        item = raw_item.strip().lower()
        if not item or ":" not in item:
            continue
        vendor, product = (part.strip() for part in item.split(":", 1))
        if vendor and product:
            excluded.add((vendor, product))
    return excluded


def _serial_usb_ids(path: str) -> tuple[str, str] | None:
    real_path = os.path.realpath(path) if os.path.exists(path) else path
    tty_name = os.path.basename(real_path)
    sys_path = Path("/sys/class/tty") / tty_name / "device"
    if not sys_path.exists():
        return None
    try:
        current = sys_path.resolve()
    except Exception:
        current = sys_path
    for current in (current, *current.parents):
        vendor_path = current / "idVendor"
        product_path = current / "idProduct"
        if vendor_path.exists() and product_path.exists():
            try:
                return (
                    vendor_path.read_text(encoding="ascii").strip().lower(),
                    product_path.read_text(encoding="ascii").strip().lower(),
                )
            except Exception:
                return None
    return None


def _is_excluded_serial_port(path: str, excluded_realpaths: set[str]) -> bool:
    if not path:
        return True
    basename = os.path.basename(path).lower()
    if basename.startswith(("ttymicroeps", "ttyoptris", "ttypyro")):
        return True
    key = os.path.realpath(path) if os.path.exists(path) else path
    if key in excluded_realpaths:
        return True
    usb_ids = _serial_usb_ids(path)
    return usb_ids is not None and usb_ids in _excluded_usb_ids()


def _candidate_ports() -> list[str]:
    ordered: list[str] = []
    if ALMEMO_PORT_ENV and os.path.exists(ALMEMO_PORT_ENV):
        ordered.append(ALMEMO_PORT_ENV)
    candidates = _serial_candidates()
    excluded_realpaths = _excluded_serial_realpaths()
    for path in candidates:
        name = os.path.basename(path).lower()
        if _is_excluded_serial_port(path, excluded_realpaths):
            continue
        if "almemo" in name:
            ordered.append(path)
    if os.path.exists("/dev/ttyUSB0"):
        ordered.append("/dev/ttyUSB0")
    ordered.extend(path for path in candidates if not _is_excluded_serial_port(path, excluded_realpaths))

    deduped: list[str] = []
    seen: set[str] = set()
    for path in ordered:
        if _is_excluded_serial_port(path, excluded_realpaths):
            continue
        key = os.path.realpath(path) if os.path.exists(path) else path
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _resolve_port() -> str:
    ports = _candidate_ports()
    if ports:
        return ports[0]
    if ALMEMO_PORT_ENV:
        return ALMEMO_PORT_ENV
    return "/dev/ttyUSB0"


def _decode_eol(raw: str) -> str:
    return (
        raw.replace("\\r", "\r")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def _write_line(ser: serial.Serial, command: str, *, sleep_sec: float = 0.02) -> None:
    payload = (command + _decode_eol(ALMEMO_EOL)).encode("utf-8")
    ser.reset_output_buffer()
    time.sleep(sleep_sec)
    ser.write(payload)
    time.sleep(sleep_sec)


def _discard_input_until_quiet(
    ser: serial.Serial,
    *,
    max_sec: float | None = None,
    quiet_sec: float | None = None,
) -> int:
    max_wait = max(0.2, max_sec if max_sec is not None else ALMEMO_DRAIN_MAX_SEC)
    quiet_wait = max(0.05, quiet_sec if quiet_sec is not None else ALMEMO_DRAIN_QUIET_SEC)
    previous_timeout = ser.timeout
    discarded = 0
    try:
        ser.timeout = min(0.05, quiet_wait / 2.0)
        deadline = time.monotonic() + max_wait
        last_rx = time.monotonic()
        while time.monotonic() < deadline:
            chunk = ser.read(256)
            if chunk:
                discarded += len(chunk)
                last_rx = time.monotonic()
                continue
            if time.monotonic() - last_rx >= quiet_wait:
                break
    finally:
        ser.timeout = previous_timeout
    return discarded


def _prepare_serial_link(ser: serial.Serial, full_reset: bool = False) -> None:
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    # Explicit XON: if device was paused by XOFF (OS buffer full), reset_input_buffer()
    # empties the buffer but does NOT send XON — device stays frozen until power cycle.
    ser.write(b"\x11")
    time.sleep(0.03)
    if full_reset:
        # Only do aggressive stop if we are about to start a real session
        for _ in range(2):
            _write_line(ser, "C11", sleep_sec=0.1)
            _discard_input_until_quiet(ser, max_sec=0.6, quiet_sec=0.15)

    # Select address 00 (broadcast/default)
    _write_line(ser, "G00", sleep_sec=0.05)
    _discard_input_until_quiet(ser, max_sec=0.3, quiet_sec=0.1)


def _extract_device_version(lines: list[str]) -> str | None:
    for raw_line in lines:
        line = _normalize_line_text(raw_line)
        if not line or line in {"t0", "G00", "C11"}:
            continue
        upper_line = line.upper()
        if "ALMEMO" in upper_line:
            return line
        if DEVICE_VERSION_RE.match(line):
            return line
    return None


def _update_protocol_version(version_str: str | None) -> None:
    global ALMEMO_PROTOCOL_VERSION
    if not version_str:
        return
    # ALMEMO V6 versions are reported with a 6.xx software version.
    # Example version: "8590-9KL 6.52" or "2490-1 V7 7.20"
    match = re.search(r"(\d)\.\d{2}", version_str)
    with VERSION_LOCK:
        if match and match.group(1) == "6":
            ALMEMO_PROTOCOL_VERSION = "V6"
        else:
            ALMEMO_PROTOCOL_VERSION = "V7"
    _log_serial_event(f"Protocol detected: {ALMEMO_PROTOCOL_VERSION} based on version '{version_str}'")


def _probe_device(ser: serial.Serial, timeout_ms: int, full_reset: bool = False) -> tuple[str | None, list[str]]:
    _prepare_serial_link(ser, full_reset=full_reset)
    _write_line(ser, "t0", sleep_sec=0.05)
    lines = _read_lines(ser, 3, timeout_ms)
    version = _extract_device_version(lines)
    if version:
        _update_protocol_version(version)
    return version, lines


def _open_serial_on_port(
    port: str,
    timeout_ms: int,
    xonxoff: bool = True,
) -> serial.Serial:
    timeout_s = max(0.05, float(timeout_ms) / 1000.0)

    return serial.Serial(
        port,
        baudrate=ALMEMO_BAUD,
        timeout=timeout_s,
        write_timeout=timeout_s,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=xonxoff,
        exclusive=True,
    )


def _open_verified_serial(
    timeout_ms: int,
    xonxoff: bool = True,
) -> tuple[serial.Serial, str]:
    errors: list[str] = []
    probe_timeout_ms = max(timeout_ms, ALMEMO_PROBE_TIMEOUT_MS)
    for port in _candidate_ports():
        if not os.path.exists(port):
            continue
        ser: serial.Serial | None = None
        version: str | None = None
        try:
            ser = _open_serial_on_port(port, timeout_ms, xonxoff=xonxoff)
            version, lines = _probe_device(ser, probe_timeout_ms, full_reset=True)
            if version:
                _mark_device_ok()
                _log_serial_event(f"ALMEMO probe ok on {port}: {version}")
                return ser, port
            details = ", ".join(_normalize_line_text(line) for line in lines if _normalize_line_text(line))
            errors.append(f"{port}: no ALMEMO version response ({details or 'empty'})")
        except Exception as exc:
            errors.append(f"{port}: {exc}")
        finally:
            if ser is not None and not version:
                try:
                    ser.close()
                except Exception:
                    pass
    if not errors:
        message = "No serial candidates available for ALMEMO"
        _mark_device_missing(message)
        raise serial.SerialException(message)
    message = "ALMEMO 2490-2 not responding on serial candidates: " + "; ".join(errors)
    _mark_device_missing(message)
    raise serial.SerialException(message)


def _get_pser() -> serial.Serial:
    """Return persistent serial connection; open and drain if needed. Caller must hold SERIAL_LOCK."""
    global _PSER, _PSER_PORT
    recent_missing = _recent_missing_reason(ALMEMO_MISSING_CACHE_SEC)
    if recent_missing:
        raise serial.SerialException(recent_missing)
    port = _resolve_port()
    if _PSER is not None and _PSER.is_open and _PSER_PORT == port:
        return _PSER
    if _PSER is not None:
        try:
            _PSER.close()
        except Exception:
            pass
        _PSER = None
    ser, verified_port = _open_verified_serial(ALMEMO_TIMEOUT_MS, xonxoff=True)
    _PSER = ser
    _PSER_PORT = verified_port
    return _PSER


def _close_pser() -> None:
    """Close persistent serial connection. Caller must hold SERIAL_LOCK."""
    global _PSER, _PSER_PORT
    if _PSER is not None:
        try:
            _PSER.close()
        except Exception:
            pass
        _PSER = None
        _PSER_PORT = ""


def _read_lines(ser: serial.Serial, read_lines: int, timeout_ms: int) -> list[str]:
    lines: list[str] = []
    deadline = time.monotonic() + max(0.05, float(timeout_ms) / 1000.0)

    while True:
        if read_lines > 0 and len(lines) >= read_lines:
            break
        if time.monotonic() >= deadline:
            break
        raw = ser.readline()
        if not raw:
            continue
        line = _normalize_line_text(raw.decode("utf-8", errors="replace"))
        if line:
            lines.append(line)
    return lines


def _send_command(
    command: str,
    *,
    timeout_ms: int,
    read_lines: int,
    raw: bool,
    eol: str | None = None,
) -> dict[str, Any]:
    result = _send_command_sequence(
        [
            {
                "command": command,
                "timeout_ms": timeout_ms,
                "read_lines": read_lines,
                "raw": raw,
                "eol": eol,
            }
        ]
    )
    sequence = result.get("sequence")
    if isinstance(sequence, list) and len(sequence) == 1:
        return sequence[0]
    return result


def _send_command_sequence(steps: list[dict[str, Any]]) -> dict[str, Any]:
    if not steps:
        return {"ok": False, "error": "missing steps", "sequence": []}

    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        command = str(step.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "missing command", "sequence": []}
        normalized_steps.append(
            {
                "command": command,
                "timeout_ms": int(step.get("timeout_ms", ALMEMO_TIMEOUT_MS)),
                "read_lines": int(step.get("read_lines", 0)),
                "raw": bool(step.get("raw", False)),
                "eol": step.get("eol"),
            }
        )

    sequence: list[dict[str, Any]] = []

    with SESSION_SWITCH_LOCK:
        paused_streams = _pause_streaming_sessions()
        try:
            with SERIAL_LOCK:
                if paused_streams:
                    try:
                        _rearm_command_session_after_stream_pause(paused_streams)
                    except Exception as exc:
                        _close_pser()
                        return {"ok": False, "error": f"ALMEMO session switch failed: {exc}", "sequence": sequence}
                for step in normalized_steps:
                    result = _send_command_on_active_serial(
                        step["command"],
                        timeout_ms=step["timeout_ms"],
                        read_lines=step["read_lines"],
                        raw=step["raw"],
                        eol=step["eol"],
                    )
                    sequence.append(result)
                    if not result.get("ok"):
                        return {
                            "ok": False,
                            "error": result.get("error", "command failed"),
                            "failed_command": step["command"],
                            "sequence": sequence,
                        }
                return {"ok": True, "sequence": sequence}
        finally:
            _resume_streaming_sessions(paused_streams)


def _rearm_command_session_after_stream_pause(state: dict[str, str]) -> None:
    """Re-open a clean command session after stopping streaming readers. Caller must hold SERIAL_LOCK."""
    if not state:
        return
    active_modes = ",".join(sorted(state.keys()))
    _log_serial_event(f"session switch: stopping {active_modes}, re-arming command channel")
    _close_pser()
    ser = _get_pser()
    _discard_input_until_quiet(ser, max_sec=0.35, quiet_sec=0.10)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(b"\x11")
    time.sleep(0.03)


def _send_command_on_active_serial(
    command: str,
    *,
    timeout_ms: int,
    read_lines: int,
    raw: bool,
    eol: str | None = None,
) -> dict[str, Any]:
    payload = command
    if not raw:
        payload += _decode_eol(eol if eol is not None else ALMEMO_EOL)

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            ser = _get_pser()
            _discard_input_until_quiet(ser, max_sec=0.35, quiet_sec=0.10)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            # reset_input_buffer() clears the OS-side queue but does not
            # release a device paused by XOFF, so send XON before the next command.
            ser.write(b"\x11")
            time.sleep(0.03)
            time.sleep(0.02)
            ser.write(payload.encode("utf-8"))
            time.sleep(0.02)
            lines = _read_lines(ser, read_lines, timeout_ms)
            if read_lines > 0 and not lines:
                last_exc = serial.SerialException("No response from ALMEMO device")
                _mark_device_missing(str(last_exc))
                _log_serial_event("No response to command, forcing ALMEMO reconnect")
                _close_pser()
                break
            if command == "t0" or lines:
                _mark_device_ok()
            return {
                "ok": True,
                "command": command,
                "lines": lines,
                "raw": "\n".join(lines),
            }
        except Exception as exc:
            last_exc = exc
            _close_pser()
    return {"ok": False, "command": command, "error": str(last_exc), "lines": [], "raw": ""}


@app.get("/")
def index():
    path = Path(__file__).with_name("ui.html")
    return path.read_text(encoding="utf-8")


@app.get("/ui.js")
def ui_js():
    path = Path(__file__).with_name("ui.js")
    return path.read_text(encoding="utf-8"), 200, {"Content-Type": "application/javascript"}


@app.get("/device-common.css")
def device_common_css():
    path = Path(__file__).with_name("device-common.css")
    return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/css"}


def _cache_health_result(res: dict[str, Any]):
    global _LAST_HEALTH_DATA, _LAST_HEALTH_AT
    with HEALTH_CACHE_LOCK:
        _LAST_HEALTH_DATA = res
        _LAST_HEALTH_AT = time.monotonic()
    return jsonify(res)


@app.get("/health")
@app.get("/healthz")
@app.get("/api/health")
def health():
    force_refresh = request.args.get("refresh") in ("1", "true")

    # Fast path: serve from cache if fresh enough
    with HEALTH_CACHE_LOCK:
        now = time.monotonic()
        if not force_refresh and _LAST_HEALTH_DATA and (now - _LAST_HEALTH_AT) < HEALTH_CACHE_TTL:
            return jsonify(_LAST_HEALTH_DATA)

    port = _resolve_port()
    port_present = os.path.exists(port)
    if not port_present:
        return _cache_health_result({"status": "missing", "port": port, "port_present": False})

    recent_missing = _recent_missing_reason(ALMEMO_MISSING_CACHE_SEC)
    if recent_missing:
        return _cache_health_result({"status": "cable_only", "port": port, "port_present": True, "reason": recent_missing})

    if _live_is_running() and _live_data_fresh():
        return _cache_health_result({"status": "ok", "port": port, "port_present": True, "source": "live", "version": "V6/V7 (active)"})

    if _fast_live_is_running() and _fast_live_data_fresh():
        return _cache_health_result({"status": "ok", "port": port, "port_present": True, "source": "fast_live", "version": "V6/V7 (active)"})

    # Only one probe at a time — concurrent callers skip the probe and reuse cache or recent state.
    # This prevents multiple back-to-back G00+t0 bursts that corrupt XON/XOFF flow control.
    probe_acquired = HEALTH_PROBE_LOCK.acquire(blocking=False)
    if not probe_acquired:
        with HEALTH_CACHE_LOCK:
            if _LAST_HEALTH_DATA:
                return jsonify(_LAST_HEALTH_DATA)
        status = "ok" if _device_recently_ok(max_age_sec=max(5.0, float(ALMEMO_TIMEOUT_MS) / 300.0)) else "cable_only"
        return jsonify({"status": status, "port": port, "port_present": True, "reason": "probe_busy"})

    try:
        # Re-check cache under probe lock: another thread may have just updated it
        with HEALTH_CACHE_LOCK:
            now = time.monotonic()
            if not force_refresh and _LAST_HEALTH_DATA and (now - _LAST_HEALTH_AT) < HEALTH_CACHE_TTL:
                return jsonify(_LAST_HEALTH_DATA)

        acquired = SERIAL_LOCK.acquire(timeout=3.0)
        if not acquired:
            status = "ok" if _device_recently_ok(max_age_sec=max(5.0, float(ALMEMO_TIMEOUT_MS) / 300.0)) else "cable_only"
            return jsonify({"status": status, "port": port, "port_present": True, "reason": "serial_busy"})

        device_ok = False
        version = ""
        try:
            try:
                ser = _get_pser()
                version, lines = _probe_device(ser, max(600, ALMEMO_HEALTH_PROBE_TIMEOUT_MS), full_reset=False)
                device_ok = bool(version)
                if device_ok:
                    _mark_device_ok()
                else:
                    _mark_device_missing("ALMEMO probe returned no device version")
            except Exception as exc:
                _mark_device_missing(str(exc))
                _close_pser()
        finally:
            SERIAL_LOCK.release()

        status = "ok" if device_ok else "cable_only"
        return _cache_health_result({"status": status, "port": port, "port_present": True, "version": version})
    finally:
        HEALTH_PROBE_LOCK.release()


@app.get("/api/version")
def api_version():
    response = _send_command(
        "t0",
        timeout_ms=ALMEMO_TIMEOUT_MS,
        read_lines=3,
        raw=False,
        eol=ALMEMO_EOL,
    )
    version = _extract_device_version(response.get("lines") or [])
    if version:
        response["version"] = version
    return jsonify(response)


@app.post("/api/command")
def api_command():
    payload = request.get_json(silent=True) or {}
    command = str(payload.get("command", "")).strip()
    timeout_ms = int(payload.get("timeout_ms", ALMEMO_TIMEOUT_MS))
    read_lines = int(payload.get("read_lines", 0))
    raw = bool(payload.get("raw", False))
    return jsonify(
        _send_command(
            command,
            timeout_ms=timeout_ms,
            read_lines=read_lines,
            raw=raw,
            eol=ALMEMO_EOL,
        )
    )


@app.post("/api/command-sequence")
def api_command_sequence():
    payload = request.get_json(silent=True) or {}
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return jsonify({"ok": False, "error": "missing steps", "sequence": []})
    return jsonify(_send_command_sequence(steps))


@app.get("/api/scan")
def api_scan():
    results: list[dict[str, Any]] = []
    version = _get_protocol_version()
    # Scan channels 0-15 (basic range)
    for ch in range(0, 16):
        if version == "V7":
            channel = f"{ch}.0"
        else:
            channel = f"{ch:02d}"

        select_cmd = f"M{channel}"
        select_res = _send_command(select_cmd, timeout_ms=ALMEMO_TIMEOUT_MS, read_lines=0, raw=False)
        if not select_res.get("ok"):
            results.append({"channel": channel, "ok": False, "error": "select failed", "detail": select_res})
            continue
        read_res = _send_command("p", timeout_ms=ALMEMO_TIMEOUT_MS, read_lines=3, raw=False)
        lines = read_res.get("lines") or []
        data_lines = [line for line in lines if line.strip() and line.strip() != "p"]
        results.append(
            {
                "channel": channel,
                "ok": read_res.get("ok", False),
                "lines": lines,
                "data": data_lines,
                "raw": read_res.get("raw", ""),
            }
        )
    return jsonify({"ok": True, "results": results})


@app.get("/api/p15")
def api_p15():
    res = _send_command("P15", timeout_ms=ALMEMO_TIMEOUT_MS * 3, read_lines=60, raw=False)
    lines = res.get("lines") or []
    active: list[dict[str, str]] = []
    for line in lines:
        text = line.strip()
        if len(text) < 3:
            continue
        # V6: "01: ...", V7: "001.0: ..." or "0.0: ..."
        if ":" not in text:
            continue
        parts = text.split(":", 1)
        chan_part = parts[0].strip()
        if chan_part.replace(".", "").isdigit():
            active.append({"channel": chan_part, "line": text})
    return jsonify({"ok": res.get("ok", False), "response": res, "active": active})


@app.get("/api/read")
def api_read():
    channel = str(request.args.get("channel", "")).strip()
    with_time = request.args.get("with_time", "0") in ("1", "true", "yes")
    read_lines = int(request.args.get("read_lines", "0"))
    if not channel:
        return jsonify({"ok": False, "error": "missing channel"})

    version = _get_protocol_version()
    if "." in channel:
        select_cmd = f"M{channel}"
    else:
        if version == "V7":
            # V7 uses xxx.x format
            select_cmd = f"M{channel}.0"
        else:
            select_cmd = f"M{channel.zfill(2)}"

    select_res = _send_command(select_cmd, timeout_ms=ALMEMO_TIMEOUT_MS, read_lines=0, raw=False)
    if not select_res.get("ok"):
        return jsonify({"ok": False, "error": "select failed", "detail": select_res})

    read_cmd = "P01" if with_time else "p"
    read_res = _send_command(read_cmd, timeout_ms=ALMEMO_TIMEOUT_MS, read_lines=read_lines, raw=False)
    lines = read_res.get("lines") or []
    data_lines = [line for line in lines if line.strip() and line.strip() != read_cmd]
    fallback = None
    if not data_lines and not with_time:
        fallback = _send_command("P35", timeout_ms=ALMEMO_TIMEOUT_MS, read_lines=read_lines, raw=False)
    return jsonify(
        {
            "ok": read_res.get("ok", False),
            "select": select_cmd,
            "read": read_cmd,
            "response": read_res,
            "fallback": fallback,
        }
    )


@app.post("/api/live/start")
def api_live_start():
    payload = request.get_json(silent=True) or {}
    cycle = _normalize_live_cycle(str(payload.get("cycle", LIVE_CYCLE)))
    with SESSION_SWITCH_LOCK:
        if _fast_live_is_running():
            _stop_stream(FAST_LIVE_THREAD, FAST_LIVE_STOP)
        if _live_is_running():
            if LIVE_CYCLE == cycle and _live_data_fresh():
                return jsonify({"ok": True, "running": True, "cycle": LIVE_CYCLE})
            _stop_stream(LIVE_THREAD, LIVE_STOP)
        _start_live_stream(cycle)
        return jsonify({"ok": True, "running": True, "cycle": LIVE_CYCLE})


@app.post("/api/live/stop")
def api_live_stop():
    with SESSION_SWITCH_LOCK:
        if _live_is_running():
            _stop_stream(LIVE_THREAD, LIVE_STOP)
        stopping = _live_is_running()
        return jsonify({"ok": True, "running": False, "stopping": stopping, "cycle": LIVE_CYCLE})


@app.get("/api/live/poll")
def api_live_poll():
    with LIVE_LOCK:
        lines = list(LIVE_LINES)
        LIVE_LINES.clear()
    return jsonify({"ok": True, "lines": lines, "running": _live_is_running(), "cycle": LIVE_CYCLE})


@app.post("/api/fast-live/start")
def api_fast_live_start():
    payload = request.get_json(silent=True) or {}
    rate = str(payload.get("rate", "10")).strip()
    if rate not in {"2.5", "10"}:
        return jsonify({"ok": False, "error": "unsupported rate"})
    with SESSION_SWITCH_LOCK:
        if _live_is_running():
            _stop_stream(LIVE_THREAD, LIVE_STOP)
        if _fast_live_is_running():
            if FAST_LIVE_RATE == rate and _fast_live_data_fresh():
                return jsonify({"ok": True, "running": True, "rate": FAST_LIVE_RATE})
            _stop_stream(FAST_LIVE_THREAD, FAST_LIVE_STOP)
        _start_fast_live_stream(rate)
        return jsonify({"ok": True, "running": True, "rate": FAST_LIVE_RATE})


@app.post("/api/fast-live/stop")
def api_fast_live_stop():
    with SESSION_SWITCH_LOCK:
        if _fast_live_is_running():
            _stop_stream(FAST_LIVE_THREAD, FAST_LIVE_STOP)
        stopping = _fast_live_is_running()
        return jsonify({"ok": True, "running": False, "stopping": stopping, "rate": FAST_LIVE_RATE})


@app.get("/api/fast-live/poll")
def api_fast_live_poll():
    with FAST_LIVE_LOCK:
        lines = list(FAST_LIVE_LINES)
        FAST_LIVE_LINES.clear()
    return jsonify({"ok": True, "lines": lines, "running": _fast_live_is_running(), "rate": FAST_LIVE_RATE})


def _startup_connect() -> None:
    time.sleep(2.0)
    with SESSION_SWITCH_LOCK:
        if _live_is_running() or _fast_live_is_running():
            return
        if _PSER is not None and _PSER.is_open:
            return
        with SERIAL_LOCK:
            try:
                _get_pser()
            except Exception:
                pass


def _start_live_stream(cycle: str) -> None:
    global LIVE_THREAD, LIVE_CYCLE, LIVE_LAST_DATA_AT
    with LIVE_LOCK:
        LIVE_LINES.clear()
    LIVE_CYCLE = cycle
    LIVE_LAST_DATA_AT = 0.0
    LIVE_STOP.clear()
    LIVE_THREAD = threading.Thread(target=_live_reader, args=(LIVE_CYCLE,), daemon=True)
    LIVE_THREAD.start()


def _start_fast_live_stream(rate: str) -> None:
    global FAST_LIVE_THREAD, FAST_LIVE_RATE, FAST_LIVE_LAST_DATA_AT
    with FAST_LIVE_LOCK:
        FAST_LIVE_LINES.clear()
    FAST_LIVE_RATE = rate
    FAST_LIVE_LAST_DATA_AT = 0.0
    FAST_LIVE_STOP.clear()
    FAST_LIVE_THREAD = threading.Thread(target=_fast_live_reader, args=(rate,), daemon=True)
    FAST_LIVE_THREAD.start()


def _pause_streaming_sessions() -> dict[str, str]:
    state: dict[str, str] = {}
    if _live_is_running():
        state["live"] = LIVE_CYCLE
        _stop_stream(LIVE_THREAD, LIVE_STOP)
    if _fast_live_is_running():
        state["fast_live"] = FAST_LIVE_RATE
        _stop_stream(FAST_LIVE_THREAD, FAST_LIVE_STOP)
    return state


def _resume_streaming_sessions(state: dict[str, str]) -> None:
    if "live" in state and not _live_is_running():
        _start_live_stream(state["live"])
    if "fast_live" in state and not _fast_live_is_running():
        _start_fast_live_stream(state["fast_live"])


if os.getenv("ALMEMO_STARTUP_CONNECT", "1") == "1":
    threading.Thread(target=_startup_connect, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3040)
