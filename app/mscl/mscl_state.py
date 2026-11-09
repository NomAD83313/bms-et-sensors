import fcntl
import glob
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from mscl_constants import mscl
from mscl_port_filter import filter_excluded_usb_ports

CONNECT_LOCK = threading.Lock()
INTERPROCESS_LOCK_PATH = os.getenv("MSCL_LOCK_FILE", "/tmp/mscl/base.lock")


class SharedOpLock:
    """Thread + process lock to serialize BaseStation access across containers."""

    def __init__(self, lock_path):
        self._lock_path = lock_path
        self._thread_lock = threading.RLock()
        self._tls = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._tls, "depth", 0)
        if depth == 0:
            lock_dir = os.path.dirname(self._lock_path)
            if lock_dir:
                os.makedirs(lock_dir, exist_ok=True)
            fh = open(self._lock_path, "a+", encoding="utf-8")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            self._tls.fh = fh
        self._tls.depth = depth + 1
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        depth = getattr(self._tls, "depth", 1) - 1
        self._tls.depth = depth
        if depth == 0:
            fh = getattr(self._tls, "fh", None)
            if fh is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
                self._tls.fh = None
        self._thread_lock.release()


OP_LOCK = SharedOpLock(INTERPROCESS_LOCK_PATH)
METRICS_LOCK = threading.Lock()

# Global state
BASE_STATION = None
BAUDRATE = 3000000
EXCLUDE_USB_IDS = os.getenv("MSCL_EXCLUDE_USB_IDS", "10c4:834b")
LOG_BUFFER = []
LOG_MAX = 200
LAST_BASE_STATUS = {"connected": False, "port": "N/A", "message": "Not connected", "ts": None}
LAST_PING_OK_TS = 0
PING_TTL_SEC = 10
CONNECT_BACKOFF_SEC = 1.5
CURRENT_PORT = None
LAST_CONNECT_ATTEMPT_TS = 0
CONNECT_MIN_INTERVAL_SEC = 2.0
NODE_READ_CACHE: dict[int, dict[str, Any]] = {}
BASE_BEACON_STATE = None
NODE_FRESH_COMM_SEC = 45
NODE_ACTIVE_STATE_FRESH_SEC = 8
SAMPLE_STOP_TOKENS: dict[int, int] = {}
SAMPLE_RUNS: dict[int, dict[str, Any]] = {}
IDLE_IN_PROGRESS: set[int] = set()
STREAM_PAUSE_UNTIL = 0.0
METRICS = {
    "base_reconnect_attempts": 0,
    "base_reconnect_successes": 0,
    "stream_reader_errors": 0,
    "stream_writer_errors": 0,
    "stream_packets_read": 0,
    "stream_points_written": 0,
    "stream_write_calls": 0,
    "stream_queue_depth": 0,
    "stream_queue_hwm": 0,
    "stream_queue_dropped_packets": 0,
    "eeprom_retries_read": 0,
    "eeprom_retries_write": 0,
}


def log(msg):
    print(msg, flush=True)
    LOG_BUFFER.append(f"{time.strftime('%H:%M:%S')} {msg}")
    if len(LOG_BUFFER) > LOG_MAX:
        del LOG_BUFFER[0 : len(LOG_BUFFER) - LOG_MAX]


def metric_inc(name, amount=1):
    with METRICS_LOCK:
        METRICS[name] = int(METRICS.get(name, 0)) + int(amount)


def metric_set(name, value):
    with METRICS_LOCK:
        METRICS[name] = value


def metric_max(name, value):
    with METRICS_LOCK:
        current = int(METRICS.get(name, 0))
        if int(value) > current:
            METRICS[name] = int(value)


def metric_snapshot():
    with METRICS_LOCK:
        return dict(METRICS)


def mark_base_disconnected(reset_port=False):
    global BASE_STATION, BASE_BEACON_STATE, LAST_PING_OK_TS, CURRENT_PORT
    with CONNECT_LOCK:
        BASE_STATION = None
        BASE_BEACON_STATE = None
        LAST_PING_OK_TS = 0
        if reset_port:
            CURRENT_PORT = None


def _get_temp_sensor_options(node):
    errs = []
    for getter in (
        lambda: node.getTempSensorOptions(ch1_mask()),
        lambda: node.getTempSensorOptions(),
    ):
        try:
            return getter(), None
        except Exception as exc:
            errs.append(str(exc))
    return None, " | ".join(errs) if errs else "getTempSensorOptions failed"


def _set_temp_sensor_options(cfg, opts):
    errs = []
    for setter in (
        lambda: cfg.tempSensorOptions(ch1_mask(), opts),
        lambda: cfg.tempSensorOptions(opts),
    ):
        try:
            setter()
            return True, None
        except Exception as exc:
            errs.append(str(exc))
    return False, " | ".join(errs) if errs else "tempSensorOptions set failed"


def _filter_default_modes(opts):
    vals = {int(x.get("value")) for x in opts if x.get("value") is not None}
    if 4 in vals:
        opts = [x for x in opts if int(x.get("value")) != 4]
    return opts


def _feature_supported(features, method_name):
    try:
        fn = getattr(features, method_name, None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    return False


def find_port():
    def _normalize_port(path):
        if not path:
            return None
        if not os.path.exists(path):
            return None
        # MSCL may fail on /dev/serial/by-id symlinks (it can open relative link target).
        # Resolve to absolute tty device path for reliable open().
        try:
            return os.path.realpath(path)
        except Exception:
            return path

    # 1) Explicit port from environment has highest priority.
    configured = os.getenv("MSCL_PORT", "").strip()
    normalized = _normalize_port(configured)
    if normalized:
        return normalized

    # 2) Then prefer stable by-id symlink for WSDA base station.
    wsda_by_id = sorted(glob.glob("/dev/serial/by-id/*WSDA-Base-200*"))
    if wsda_by_id:
        normalized = _normalize_port(wsda_by_id[0])
        if normalized:
            return normalized

    # 3) Fallback: USB serial first, ACM second.
    usb_ports = filter_excluded_usb_ports(sorted(glob.glob("/dev/ttyUSB*")), EXCLUDE_USB_IDS)
    if usb_ports:
        return usb_ports[0]
    acm_ports = sorted(glob.glob("/dev/ttyACM*"))
    return acm_ports[0] if acm_ports else None


def internal_connect(force_ping=False):
    global BASE_STATION, LAST_PING_OK_TS, CURRENT_PORT, LAST_CONNECT_ATTEMPT_TS, BASE_BEACON_STATE
    now = time.time()
    with CONNECT_LOCK:
        if BASE_STATION and not force_ping:
            if (now - LAST_PING_OK_TS) <= PING_TTL_SEC:
                return True, "Connected"
            try:
                if BASE_STATION.ping():
                    LAST_PING_OK_TS = now
                    LAST_BASE_STATUS.update({"connected": True, "message": "Connected", "ts": time.strftime("%H:%M:%S")})
                    return True, "Connected"
            except Exception as exc:
                LAST_BASE_STATUS.update(
                    {
                        "connected": False,
                        "port": CURRENT_PORT or "N/A",
                        "message": f"Runtime ping failed: {exc}",
                        "ts": time.strftime("%H:%M:%S"),
                    }
                )
                BASE_STATION = None
        if BASE_STATION and force_ping:
            try:
                if BASE_STATION.ping():
                    LAST_PING_OK_TS = now
                    LAST_BASE_STATUS.update({"connected": True, "message": "Connected", "ts": time.strftime("%H:%M:%S")})
                    return True, "Connected"
            except Exception as exc:
                LAST_BASE_STATUS.update(
                    {
                        "connected": False,
                        "port": CURRENT_PORT or "N/A",
                        "message": f"Forced ping failed: {exc}",
                        "ts": time.strftime("%H:%M:%S"),
                    }
                )
                BASE_STATION = None
        if not force_ping and (now - LAST_CONNECT_ATTEMPT_TS) < CONNECT_MIN_INTERVAL_SEC:
            return False, "Connect throttled"
        LAST_CONNECT_ATTEMPT_TS = now
        if CURRENT_PORT and not os.path.exists(CURRENT_PORT):
            CURRENT_PORT = None
        port = CURRENT_PORT or find_port()
        if not port:
            LAST_BASE_STATUS.update(
                {"connected": False, "port": "N/A", "message": "No Port", "ts": time.strftime("%H:%M:%S")}
            )
            return False, "No Port"
        try:
            log(f"[mscl-web] Connecting BaseStation on {port}...")
            conn = mscl.Connection.Serial(port, BAUDRATE)
            station = mscl.BaseStation(conn)
            station.readWriteRetries(10)
            if station.ping():
                BASE_STATION = station
                BASE_STATION.enableBeacon()
                BASE_BEACON_STATE = True
                LAST_PING_OK_TS = now
                CURRENT_PORT = port
                LAST_BASE_STATUS.update(
                    {"connected": True, "port": port, "message": "OK", "ts": time.strftime("%H:%M:%S")}
                )
                log(f"[mscl-web] BaseStation OK port={port}")
                return True, port
            LAST_BASE_STATUS.update(
                {"connected": False, "port": port, "message": "Ping failed", "ts": time.strftime("%H:%M:%S")}
            )
            log("[mscl-web] BaseStation ping failed")
            time.sleep(CONNECT_BACKOFF_SEC)
            return False, "Ping failed"
        except Exception as exc:
            if "Invalid Com Port" in str(exc):
                CURRENT_PORT = None
            LAST_BASE_STATUS.update(
                {"connected": False, "port": port, "message": str(exc), "ts": time.strftime("%H:%M:%S")}
            )
            log(f"[mscl-web] BaseStation connect error: {exc}")
            time.sleep(CONNECT_BACKOFF_SEC)
            return False, str(exc)


def ensure_beacon_on():
    global BASE_BEACON_STATE
    if BASE_STATION is None:
        return
    if BASE_BEACON_STATE is True:
        return
    try:
        BASE_STATION.enableBeacon()
        BASE_BEACON_STATE = True
        log("[mscl-web] [BEACON] auto-enabled for node communication")
    except Exception as exc:
        log(f"[mscl-web] [BEACON] auto-enable failed: {exc}")


def ch1_mask():
    mask = mscl.ChannelMask()
    mask.enable(1)
    return mask


def ch2_mask():
    mask = mscl.ChannelMask()
    mask.enable(2)
    return mask


def set_idle_with_retry(node, node_id, stage_tag, attempts=2, delay_sec=0.8, required=False):
    last_err = None
    for i in range(1, attempts + 1):
        try:
            node.setToIdle()
            log(f"[mscl-web] [PREP-IDLE] {stage_tag} success node_id={node_id} attempt {i}/{attempts}")
            return True
        except Exception as exc:
            last_err = str(exc)
            log(
                f"[mscl-web] [PREP-IDLE] {stage_tag} fail node_id={node_id} attempt {i}/{attempts}: {exc}"
            )
            if i < attempts:
                time.sleep(delay_sec)
    if required:
        raise RuntimeError(f"Set to Idle failed at {stage_tag}: {last_err}")
    return False


def _node_state_info(node):
    state_map = {
        0: "Idle",
        1: "Sampling",
        2: "Sampling",
        255: "Unknown",
    }
    try:
        raw_state = node.lastDeviceState()
        try:
            state_num = int(raw_state)
            state_text = state_map.get(state_num, f"State {state_num}")
        except Exception:
            state_num = None
            state_text = str(raw_state)
        try:
            raw_last_comm = str(node.lastCommunicationTime())
            ts_base = raw_last_comm.split(".", maxsplit=1)[0]
            dt = datetime.strptime(ts_base, "%Y-%m-%d %H:%M:%S")
            ts_local = dt.timestamp()
            ts_utc = dt.replace(tzinfo=timezone.utc).timestamp()
            now = time.time()
            age_sec = min(abs(now - ts_local), abs(now - ts_utc))
            if age_sec > NODE_FRESH_COMM_SEC:
                return None, f"Offline (stale {int(age_sec)}s)", f"stale_last_comm={int(age_sec)}s"
            if state_num in (1, 2) and age_sec > NODE_ACTIVE_STATE_FRESH_SEC:
                return None, "Unknown", f"stale_active_state={int(age_sec)}s"
        except Exception:
            pass
        return state_num, state_text, None
    except Exception as exc:
        return None, None, str(exc)


def close_base_station():
    global BASE_STATION, BASE_BEACON_STATE
    if BASE_STATION is None:
        return
    try:
        BASE_STATION.disableBeacon()
    except Exception:
        pass
    try:
        BASE_STATION.disconnect()
        BASE_STATION.release()
    except Exception:
        pass
    BASE_STATION = None
    BASE_BEACON_STATE = None


__all__ = [
    "CONNECT_LOCK",
    "INTERPROCESS_LOCK_PATH",
    "SharedOpLock",
    "OP_LOCK",
    "METRICS_LOCK",
    "BASE_STATION",
    "BAUDRATE",
    "EXCLUDE_USB_IDS",
    "LOG_BUFFER",
    "LOG_MAX",
    "LAST_BASE_STATUS",
    "LAST_PING_OK_TS",
    "PING_TTL_SEC",
    "CONNECT_BACKOFF_SEC",
    "CURRENT_PORT",
    "LAST_CONNECT_ATTEMPT_TS",
    "CONNECT_MIN_INTERVAL_SEC",
    "NODE_READ_CACHE",
    "BASE_BEACON_STATE",
    "NODE_FRESH_COMM_SEC",
    "NODE_ACTIVE_STATE_FRESH_SEC",
    "SAMPLE_STOP_TOKENS",
    "SAMPLE_RUNS",
    "IDLE_IN_PROGRESS",
    "METRICS",
    "log",
    "metric_inc",
    "metric_set",
    "metric_max",
    "metric_snapshot",
    "mark_base_disconnected",
    "_get_temp_sensor_options",
    "_set_temp_sensor_options",
    "_filter_default_modes",
    "_feature_supported",
    "find_port",
    "internal_connect",
    "ensure_beacon_on",
    "ch1_mask",
    "ch2_mask",
    "set_idle_with_retry",
    "_node_state_info",
    "close_base_station",
]
