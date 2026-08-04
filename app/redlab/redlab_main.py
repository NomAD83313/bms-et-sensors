import os
import time
import sys
import json
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template
from uldaq import (get_daq_device_inventory, DaqDevice, InterfaceType,  # type: ignore
                   TcType)
from uldaq.daq_device_config import DevVersionType  # type: ignore
from influxdb_client import InfluxDBClient  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS # type: ignore

try:
    from redlab_acquisition import (
        BOOTSTRAP_BATCHES,
        BOOTSTRAP_WARMUP_SECONDS,
        CHANNEL_COUNT,
        build_points,
        channel_log_data,
        configure_tc_channels,
        filter_bootstrap_batches,
        load_active_channels,
        new_filter_state,
        read_batch,
    )
    from redlab_inventory import (
        canonical_redlab_device_id,
        descriptor_has_valid_unique_id,
        is_valid_redlab_unique_id,
        normalize_redlab_inventory,
    )
except ImportError:
    from app.redlab.redlab_acquisition import (
        BOOTSTRAP_BATCHES,
        BOOTSTRAP_WARMUP_SECONDS,
        CHANNEL_COUNT,
        build_points,
        channel_log_data,
        configure_tc_channels,
        filter_bootstrap_batches,
        load_active_channels,
        new_filter_state,
        read_batch,
    )
    from app.redlab.redlab_inventory import (
        canonical_redlab_device_id,
        descriptor_has_valid_unique_id,
        is_valid_redlab_unique_id,
        normalize_redlab_inventory,
    )

URL = "http://influxdb:8086"
TOKEN = os.getenv("INFLUX_TOKEN")
ORG = os.getenv("INFLUX_ORG")
BUCKET = os.getenv("INFLUX_BUCKET")
HEALTH_PORT = 8090
REDLAB_ACTIVE_UNIQUE_ID = os.getenv("REDLAB_ACTIVE_UNIQUE_ID", "").strip()
LOOP_SLEEP_SECONDS = float(os.getenv("REDLAB_LOOP_SLEEP", "0.5"))
READ_MODE = os.getenv("REDLAB_READ_MODE", "list").strip().lower()
REDLAB_DISCOVERY_INTERVAL_SECONDS = float(os.getenv("REDLAB_DISCOVERY_INTERVAL", "5.0"))
REDLAB_REENUMERATION_RECOVERY = os.getenv("REDLAB_REENUMERATION_RECOVERY", "1").strip().lower() not in {"0", "false", "no", "off"}
REDLAB_REENUMERATION_CONFIRMATIONS = max(1, int(os.getenv("REDLAB_REENUMERATION_CONFIRMATIONS", "2")))
REDLAB_REENUMERATION_DEBOUNCE_SECONDS = max(0.0, float(os.getenv("REDLAB_REENUMERATION_DEBOUNCE_SECONDS", "5.0")))
REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS = max(0.0, float(os.getenv("REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS", "60.0")))
REDLAB_WRITE_QUEUE_MAX_BATCHES = max(1, int(os.getenv("REDLAB_WRITE_QUEUE_MAX_BATCHES", "600")))
REDLAB_WRITE_RETRY_SECONDS = max(0.1, float(os.getenv("REDLAB_WRITE_RETRY_SECONDS", "1.0")))
_REDLAB_REENUMERATION_LAST_RESTART_ENV = "_REDLAB_REENUMERATION_LAST_RESTART_TS"
REDLAB_CHANNEL_STATE_PATH = Path("/runtime/redlab_channels.json")
REDLAB_CHANNEL_KEYS = [f"ch{i}" for i in range(CHANNEL_COUNT)]

app = Flask(__name__)

HEALTH_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
HEALTH_SERVER_LOCK = threading.Lock()
HEALTH_SERVER = None
HEALTH_SERVER_THREAD: threading.Thread | None = None
READ_SETTINGS = {
    "read_mode": READ_MODE if READ_MODE in {"single", "list"} else "list",
    "loop_sleep_seconds": LOOP_SLEEP_SECONDS,
}
HEALTH_STATE = {
    "status": "starting",
    "connected": False,
    "influx_ok": False,
    "last_ok_ts": None,
    "last_channels": [],
    "message": "initializing",
    "device_name": None,
    "device_unique_id": None,
    "device_fw_main": None,
    "read_mode": READ_SETTINGS["read_mode"],
    "loop_sleep_seconds": READ_SETTINGS["loop_sleep_seconds"],
    "loop_hz": None,
    "cal_date": None,
    "resolution": None,
    "has_pacer": None,
}


RUNTIME_LOCK = threading.Lock()
RUNTIMES: dict[str, "RedLabRuntime"] = {}


def set_health(**kwargs):
    with HEALTH_LOCK:
        HEALTH_STATE.update(kwargs)


def get_health():
    with HEALTH_LOCK:
        return dict(HEALTH_STATE)


def _runtime_snapshot() -> dict[str, dict]:
    with RUNTIME_LOCK:
        return {device_id: runtime.health_snapshot() for device_id, runtime in RUNTIMES.items()}


def _running_runtime_ids() -> set[str]:
    snapshot = _runtime_snapshot()
    return {
        device_id
        for device_id, state in snapshot.items()
        if state.get("status") in {"starting", "running", "degraded"}
    }


def _runtime_for_device(device_id: str):
    with RUNTIME_LOCK:
        return RUNTIMES.get(str(device_id or "").strip())


def _active_device_id() -> str | None:
    configured = REDLAB_ACTIVE_UNIQUE_ID.strip()
    if configured:
        return configured if configured.startswith("redlab_") else canonical_redlab_device_id(configured)
    running = sorted(_running_runtime_ids())
    if running:
        return running[0]
    unique_id = str(get_health().get("device_unique_id") or "").strip()
    return canonical_redlab_device_id(unique_id) if unique_id else None


def _device_is_active(device_id: str) -> bool:
    runtime = _runtime_for_device(device_id)
    if runtime is None:
        return False
    return runtime.health_snapshot().get("status") in {"starting", "running", "degraded"}


def _device_inventory() -> list[dict]:
    active_unique_id = str(get_health().get("device_unique_id") or "")
    descriptors = _valid_inventory_descriptors()
    return normalize_redlab_inventory(
        descriptors,
        active_unique_id=active_unique_id,
        active_device_ids=_running_runtime_ids(),
    )


def _valid_inventory_descriptors() -> list:
    return [
        descriptor for descriptor in get_daq_device_inventory(InterfaceType.USB)
        if descriptor_has_valid_unique_id(descriptor)
    ]


def _descriptor_device_ids(descriptors: list) -> set[str]:
    return {
        canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
        for descriptor in descriptors
    }


def _fresh_inventory_device_ids() -> set[str]:
    code = (
        "import json;"
        "from uldaq import get_daq_device_inventory, InterfaceType;"
        "print(json.dumps([getattr(d, 'unique_id', '') for d in get_daq_device_inventory(InterfaceType.USB)]))"
    )
    try:
        raw = subprocess.check_output([sys.executable, "-c", code], text=True, timeout=5)
        unique_ids = json.loads(raw)
    except Exception as exc:
        print(f"!!! RedLab fresh inventory check failed: {exc}")
        return set()
    return {
        canonical_redlab_device_id(unique_id)
        for unique_id in unique_ids
        if is_valid_redlab_unique_id(unique_id)
    }


def _last_reenumeration_restart_ts() -> float:
    try:
        return float(os.getenv(_REDLAB_REENUMERATION_LAST_RESTART_ENV, "0"))
    except ValueError:
        return 0.0


def _restart_cooldown_remaining() -> float:
    if REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS <= 0:
        return 0.0
    return max(0.0, REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS - (time.time() - _last_reenumeration_restart_ts()))


def _restart_process_for_usb_reenumeration(missing_device_ids: set[str]) -> None:
    if not missing_device_ids:
        return
    os.environ[_REDLAB_REENUMERATION_LAST_RESTART_ENV] = str(time.time())
    print(
        "!!! RedLab registry detected USB re-enumeration stale state; "
        f"fresh inventory sees {sorted(missing_device_ids)} only from a new process. Restarting redlab-collector process."
    )
    sys.stdout.flush()
    sys.stderr.flush()
    stop_health_server()
    time.sleep(0.2)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _device_not_configurable(device_id: str):
    return jsonify(
        ok=False,
        configurable=False,
        device_id=device_id,
        error="device is detected but acquisition runtime is not attached yet",
    ), 409


def _active_runtime():
    active_id = _active_device_id()
    if active_id:
        runtime = _runtime_for_device(active_id)
        if runtime is not None:
            return runtime
    with RUNTIME_LOCK:
        for runtime in RUNTIMES.values():
            if runtime.health_snapshot().get("status") in {"starting", "running", "degraded"}:
                return runtime
    return None


def _is_redlab_selection_key(key: str) -> bool:
    if key in REDLAB_CHANNEL_KEYS:
        return True
    if "|" not in key:
        return False
    device, channel = key.split("|", 1)
    return bool(device.strip()) and channel in REDLAB_CHANNEL_KEYS


def _default_channel_state() -> dict[str, object]:
    return {key: True for key in REDLAB_CHANNEL_KEYS}


def _load_channel_state() -> dict[str, object]:
    try:
        raw = json.loads(REDLAB_CHANNEL_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_channel_state()
    except Exception:
        return _default_channel_state()

    out: dict[str, object] = _default_channel_state()
    for key, value in raw.items():
        if not _is_redlab_selection_key(str(key)):
            continue
        if isinstance(value, dict):
            out[str(key)] = {
                "enabled": bool(value.get("enabled", True)),
                "name": str(value.get("name", "")),
            }
        else:
            out[str(key)] = bool(value)
    return out


def _save_channel_state(payload: dict) -> dict[str, object]:
    out: dict[str, object] = _default_channel_state()
    for raw_key, value in payload.items():
        key = str(raw_key)
        if not _is_redlab_selection_key(key):
            continue
        if isinstance(value, dict):
            enabled = bool(value.get("enabled", True))
            name = str(value.get("name", "")).strip()
            out[key] = {"enabled": enabled, "name": name} if name else enabled
        else:
            out[key] = bool(value)

    REDLAB_CHANNEL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REDLAB_CHANNEL_STATE_PATH.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    return out


@app.get("/")
def index():
    return render_template("index.html", tc_types=_tc_type_labels())


@app.get("/health")
@app.get("/healthz")
@app.get("/api/health")
def health():
    return jsonify(get_health())


@app.get("/api/devices")
def api_devices():
    try:
        devices = _device_inventory()
        active_device = next((device for device in devices if device.get("active")), None)
        return jsonify(
            ok=True,
            devices=devices,
            count=len(devices),
            active_device_id=_active_device_id() or (active_device.get("device_id") if active_device else None),
            active_device_ids=sorted(_running_runtime_ids()),
        )
    except Exception as exc:
        return jsonify(ok=False, devices=[], count=0, error=str(exc)), 500


@app.get("/api/redlab/channels")
def get_redlab_channels_state():
    return jsonify(ok=True, channels=_load_channel_state())


@app.post("/api/redlab/channels")
def set_redlab_channels_state():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="Expected JSON object"), 400
    return jsonify(ok=True, channels=_save_channel_state(payload))


@app.get("/api/device/<device_id>/health")
def device_health(device_id: str):
    runtime = _runtime_for_device(device_id)
    if runtime is not None:
        payload = runtime.health_snapshot()
        payload.update(ok=True, active=_device_is_active(device_id), configurable=True)
        return jsonify(payload)
    try:
        device = next((item for item in _device_inventory() if item.get("device_id") == device_id), None)
    except Exception as exc:
        return jsonify(ok=False, device_id=device_id, error=str(exc)), 500
    if device is None:
        return jsonify(ok=False, device_id=device_id, error="device not found"), 404
    return jsonify(
        ok=True,
        device_id=device_id,
        active=False,
        configurable=False,
        connected=True,
        status="standby",
        message="detected; acquisition runtime is not attached yet",
        device_name=device.get("product_name"),
        device_unique_id=device.get("unique_id"),
        device_fw_main=None,
    )


@app.get("/api/config/tc")
def get_tc_config():
    runtime = _active_runtime()
    if runtime is None:
        return jsonify(ok=False, error="device not connected")
    return jsonify(runtime.tc_config_payload())


@app.post("/api/config/tc")
def set_tc_config():
    payload = request.get_json(silent=True) or {}
    updates = payload.get("updates") or []
    runtime = _active_runtime()
    if runtime is None:
        return jsonify(ok=False, error="device not connected")
    return jsonify(runtime.apply_tc_updates(updates))


@app.get("/api/device/<device_id>/config/tc")
def get_device_tc_config(device_id: str):
    runtime = _runtime_for_device(device_id)
    if runtime is None:
        return _device_not_configurable(device_id)
    return jsonify(runtime.tc_config_payload())


@app.post("/api/device/<device_id>/config/tc")
def set_device_tc_config(device_id: str):
    runtime = _runtime_for_device(device_id)
    if runtime is None:
        return _device_not_configurable(device_id)
    payload = request.get_json(silent=True) or {}
    return jsonify(runtime.apply_tc_updates(payload.get("updates") or []))


@app.get("/api/config/read_mode")
def get_read_mode():
    return jsonify(ok=True, read_mode=_get_read_mode())


@app.post("/api/config/read_mode")
def set_read_mode():
    payload = request.get_json(silent=True) or {}
    mode = payload.get("read_mode")
    if not mode:
        return jsonify(ok=False, error="missing read_mode")
    val = str(mode).strip().lower()
    if val not in ("single", "list"):
        return jsonify(ok=False, error="invalid read_mode")
    _set_read_mode(val)
    return jsonify(ok=True, read_mode=_get_read_mode())


@app.get("/api/device/<device_id>/config/read_mode")
def get_device_read_mode(device_id: str):
    if _runtime_for_device(device_id) is None:
        return _device_not_configurable(device_id)
    return get_read_mode()


@app.post("/api/device/<device_id>/config/read_mode")
def set_device_read_mode(device_id: str):
    if _runtime_for_device(device_id) is None:
        return _device_not_configurable(device_id)
    return set_read_mode()


@app.get("/api/config/loop_sleep")
def get_loop_sleep():
    return jsonify(ok=True, loop_sleep_seconds=_get_loop_sleep())


@app.post("/api/config/loop_sleep")
def set_loop_sleep():
    payload = request.get_json(silent=True) or {}
    value = payload.get("loop_sleep_seconds")
    try:
        seconds = float(str(value).strip().replace(",", "."))
    except Exception:
        return jsonify(ok=False, error="invalid loop_sleep_seconds")
    if seconds < 0.1 or seconds > 60:
        return jsonify(ok=False, error="loop_sleep_seconds out of range (0.1-60)")
    _set_loop_sleep(seconds)
    return jsonify(ok=True, loop_sleep_seconds=_get_loop_sleep())


@app.get("/api/device/<device_id>/config/loop_sleep")
def get_device_loop_sleep(device_id: str):
    if _runtime_for_device(device_id) is None:
        return _device_not_configurable(device_id)
    return get_loop_sleep()


@app.post("/api/device/<device_id>/config/loop_sleep")
def set_device_loop_sleep(device_id: str):
    if _runtime_for_device(device_id) is None:
        return _device_not_configurable(device_id)
    return set_loop_sleep()

def _tc_type_labels() -> list[str]:
    return [t.name for t in TcType]


def _resolve_tc_type(raw: str) -> TcType | None:
    key = str(raw or "").strip().upper()
    if not key:
        return None
    try:
        return TcType[key]
    except Exception:
        return None


def _get_read_mode() -> str:
    with CONFIG_LOCK:
        return str(READ_SETTINGS.get("read_mode") or "list")


def _set_read_mode(mode: str) -> None:
    val = (mode or "").strip().lower()
    if val not in ("single", "list"):
        return
    with CONFIG_LOCK:
        READ_SETTINGS["read_mode"] = val
    set_health(read_mode=val)


def _get_loop_sleep() -> float:
    with CONFIG_LOCK:
        return float(READ_SETTINGS.get("loop_sleep_seconds") or LOOP_SLEEP_SECONDS)


def _set_loop_sleep(seconds: float) -> None:
    with CONFIG_LOCK:
        READ_SETTINGS["loop_sleep_seconds"] = seconds
    set_health(loop_sleep_seconds=seconds)


def start_health_server():
    global HEALTH_SERVER, HEALTH_SERVER_THREAD
    with HEALTH_SERVER_LOCK:
        if HEALTH_SERVER is not None:
            return
        from werkzeug.serving import make_server

        HEALTH_SERVER = make_server("0.0.0.0", HEALTH_PORT, app, threaded=True)
        HEALTH_SERVER_THREAD = threading.Thread(target=HEALTH_SERVER.serve_forever, daemon=True)
        HEALTH_SERVER_THREAD.start()
    print(f">>> RedLab server listening on :{HEALTH_PORT}/health")


def stop_health_server():
    global HEALTH_SERVER, HEALTH_SERVER_THREAD
    with HEALTH_SERVER_LOCK:
        server = HEALTH_SERVER
        thread = HEALTH_SERVER_THREAD
        HEALTH_SERVER = None
        HEALTH_SERVER_THREAD = None
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:
        print(f"!!! RedLab server shutdown warning: {exc}")
    if thread and thread.is_alive():
        thread.join(timeout=1.0)


class RedLabRuntime:
    def __init__(self, descriptor, write_api):
        self.descriptor = descriptor
        self.device_id = canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
        self.unique_id = str(getattr(descriptor, "unique_id", "") or "")
        self.product_name = str(getattr(descriptor, "product_name", "") or "USB-TC")
        self.write_api = write_api
        self.lock = threading.Lock()
        self.health_lock = threading.Lock()
        self.write_queue_lock = threading.Condition()
        self.stop_event = threading.Event()
        self.writer_stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.writer_thread: threading.Thread | None = None
        self.write_queue: list[tuple[list[dict], str, int, float | None]] = []
        self.daq_device = None
        self.ai_device = None
        self.ai_config = None
        self.filter_state = new_filter_state()
        self.cadence_signature: tuple[str, float] | None = None
        self.loop_timestamps: list[float] = []
        self.health = {
            "ok": True,
            "device_id": self.device_id,
            "device_name": self.product_name,
            "device_unique_id": self.unique_id,
            "status": "starting",
            "connected": False,
            "influx_ok": False,
            "last_ok_ts": None,
            "last_channels": [],
            "message": "runtime starting",
            "device_fw_main": None,
            "read_mode": _get_read_mode(),
            "loop_sleep_seconds": _get_loop_sleep(),
            "loop_hz": None,
            "cal_date": None,
            "resolution": None,
            "has_pacer": None,
            "write_queue_depth": 0,
        }

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.writer_stop_event.clear()
        self.writer_thread = threading.Thread(target=self.write_loop, name=f"redlab-writer-{self.unique_id}", daemon=True)
        self.writer_thread.start()
        self.thread = threading.Thread(target=self.run, name=f"redlab-{self.unique_id}", daemon=True)
        self.thread.start()

    def stop(self, message: str) -> None:
        self.stop_event.set()
        self.writer_stop_event.set()
        with self.write_queue_lock:
            self.write_queue_lock.notify_all()
        self.set_health(status="offline", connected=False, message=message)

    def set_health(self, **kwargs) -> None:
        with self.health_lock:
            self.health.update(kwargs)

    def health_snapshot(self) -> dict:
        with self.health_lock:
            return dict(self.health)

    def connect(self) -> None:
        self.set_health(status="starting", connected=False, message="connecting")
        print(f"--- Connecting RedLab runtime {self.device_id} ({self.product_name})...")
        dev = DaqDevice(self.descriptor)
        dev.connect()
        ai_device = dev.get_ai_device()
        ai_info = ai_device.get_info()
        ai_config = ai_device.get_config()
        configure_tc_channels(ai_config)

        fw_main = None
        cal_date = None
        resolution = None
        has_pacer = None
        temp_unit = None
        try:
            fw_main = dev.get_config().get_version(DevVersionType.FW_MAIN)
        except Exception:
            pass
        try:
            temp_unit = ai_config.get_temp_unit().name
        except Exception:
            pass
        try:
            raw_cal_date = ai_config.get_cal_date()
            if raw_cal_date is not None:
                try:
                    cal_date = datetime.fromtimestamp(int(raw_cal_date), tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    cal_date = str(raw_cal_date)
        except Exception:
            pass
        try:
            resolution = ai_info.get_resolution()
        except Exception:
            pass
        try:
            has_pacer = bool(ai_info.has_pacer())
        except Exception:
            pass

        with self.lock:
            self.daq_device = dev
            self.ai_device = ai_device
            self.ai_config = ai_config
        self.set_health(
            status="running",
            connected=True,
            influx_ok=True,
            message="connected",
            device_fw_main=fw_main,
            temp_unit=temp_unit,
            cal_date=cal_date,
            resolution=resolution,
            has_pacer=has_pacer,
            read_mode=_get_read_mode(),
            loop_sleep_seconds=_get_loop_sleep(),
        )
        print(f">>> RedLab runtime connected: {self.device_id}")

    def tc_config_payload(self) -> dict:
        with self.lock:
            ai_config = self.ai_config
        if ai_config is None:
            return {"ok": False, "error": "device not connected", "device_id": self.device_id}
        channels = []
        with self.lock:
            for ch in range(CHANNEL_COUNT):
                try:
                    tc = ai_config.get_chan_tc_type(ch)
                    channels.append({"channel": ch, "tc_type": tc.name})
                except Exception as exc:
                    channels.append({"channel": ch, "tc_type": "UNKNOWN", "error": str(exc)})
        return {"ok": True, "device_id": self.device_id, "channels": channels, "types": _tc_type_labels()}

    def apply_tc_updates(self, updates: list) -> dict:
        with self.lock:
            ai_config = self.ai_config
            if ai_config is None:
                return {"ok": False, "error": "device not connected", "device_id": self.device_id}
            applied = []
            for item in updates:
                try:
                    ch = int(item.get("channel"))
                    tc = _resolve_tc_type(item.get("tc_type"))
                    if tc is None:
                        continue
                    ai_config.set_chan_tc_type(ch, tc)
                    applied.append({"channel": ch, "tc_type": tc.name})
                except Exception as exc:
                    applied.append({"channel": item.get("channel"), "error": str(exc)})
            self.filter_state["last_valid_by_channel"].clear()
        return {"ok": True, "device_id": self.device_id, "applied": applied}

    def _queued_batch_count(self) -> int:
        return sum(len(entry[0]) for entry in self.write_queue)

    def _enqueue_write(self, batches: list[dict], message: str, skipped: int, loop_hz: float | None) -> int:
        dropped = 0
        with self.write_queue_lock:
            self.write_queue.append((list(batches), message, skipped, loop_hz))
            while self._queued_batch_count() > REDLAB_WRITE_QUEUE_MAX_BATCHES and self.write_queue:
                dropped += len(self.write_queue.pop(0)[0])
            depth = self._queued_batch_count()
            self.write_queue_lock.notify()
        if dropped:
            print(f"!!! RedLab write queue overflow for {self.device_id}: dropped {dropped} oldest batches")
        return depth

    def _write_batches(self, batches: list[dict]) -> list[str]:
        points = []
        last_log_data = []
        for batch in batches:
            pts, log_data = build_points(batch, self.device_id)
            points.extend(pts)
            last_log_data = log_data
        if points:
            self.write_api.write(BUCKET, ORG, points)
        return last_log_data

    def write_loop(self) -> None:
        while True:
            with self.write_queue_lock:
                while not self.write_queue and not self.writer_stop_event.is_set():
                    self.write_queue_lock.wait(timeout=1.0)
                if not self.write_queue and self.writer_stop_event.is_set():
                    return
                batches, message, skipped, loop_hz = self.write_queue[0]
            try:
                last_log_data = self._write_batches(batches)
                with self.write_queue_lock:
                    if self.write_queue and self.write_queue[0][0] is batches:
                        self.write_queue.pop(0)
                    depth = self._queued_batch_count()
                if last_log_data:
                    print(f"Logged {self.device_id}: " + " | ".join(last_log_data))
                self.set_health(
                    status="running",
                    connected=True,
                    influx_ok=True,
                    last_ok_ts=int(time.time()),
                    last_channels=last_log_data,
                    message=message,
                    skipped_channels=skipped,
                    read_mode=_get_read_mode(),
                    loop_sleep_seconds=_get_loop_sleep(),
                    loop_hz=loop_hz,
                    write_queue_depth=depth,
                )
            except Exception as exc:
                with self.write_queue_lock:
                    depth = self._queued_batch_count()
                print(f"!!! InfluxDB Write Error for {self.device_id}: {exc}")
                self.set_health(
                    status="degraded",
                    connected=True,
                    influx_ok=False,
                    message=f"influx write retry pending: {exc}",
                    write_queue_depth=depth,
                )
                time.sleep(REDLAB_WRITE_RETRY_SECONDS)

    def write_runtime_points(self, batches: list[dict], message: str, *, skipped: int = 0, loop_hz: float | None = None) -> None:
        if not batches:
            return
        last_log_data = channel_log_data(batches[-1].get("values", {}))
        depth = self._enqueue_write(batches, message, skipped, loop_hz)
        self.set_health(
            status="running",
            connected=True,
            last_channels=last_log_data,
            message=message,
            skipped_channels=skipped,
            read_mode=_get_read_mode(),
            loop_sleep_seconds=_get_loop_sleep(),
            loop_hz=loop_hz,
            write_queue_depth=depth,
        )

    def observe_loop_hz(self) -> float | None:
        signature = (_get_read_mode(), _get_loop_sleep())
        if signature != self.cadence_signature:
            self.cadence_signature = signature
            self.loop_timestamps.clear()
        now = time.monotonic()
        self.loop_timestamps.append(now)
        if len(self.loop_timestamps) > 12:
            self.loop_timestamps.pop(0)
        if len(self.loop_timestamps) < 2:
            return None
        span = self.loop_timestamps[-1] - self.loop_timestamps[0]
        if span <= 0:
            return None
        return round((len(self.loop_timestamps) - 1) / span, 2)

    def run(self) -> None:
        try:
            self.connect()
            warmup_deadline = time.monotonic() + BOOTSTRAP_WARMUP_SECONDS
            bootstrap_batches: list[dict] = []
            bootstrap_done = False

            while not self.stop_event.is_set():
                warmup_remaining = warmup_deadline - time.monotonic()
                active_channels = load_active_channels()
                if not active_channels:
                    self.set_health(
                        status="running",
                        connected=True,
                        influx_ok=True,
                        last_channels=[],
                        message="no active RedLab channels selected",
                    )
                    time.sleep(_get_loop_sleep())
                    continue

                with self.lock:
                    ai_device = self.ai_device
                    ai_config = self.ai_config
                    if ai_device is None or ai_config is None:
                        raise RuntimeError("device not connected")
                    batch_values, skipped = read_batch(
                        ai_device,
                        ai_config,
                        active_channels,
                        self.filter_state,
                        _get_read_mode(),
                    )

                if not batch_values:
                    time.sleep(_get_loop_sleep())
                    continue

                batch = {"ts_ns": time.time_ns(), "values": batch_values}
                loop_hz = self.observe_loop_hz()

                if not bootstrap_done and warmup_remaining > 0:
                    self.set_health(
                        status="starting",
                        connected=True,
                        influx_ok=True,
                        last_channels=channel_log_data(batch_values),
                        message=f"warmup {warmup_remaining:.1f}s",
                        skipped_channels=skipped,
                        read_mode=_get_read_mode(),
                        loop_sleep_seconds=_get_loop_sleep(),
                        loop_hz=loop_hz,
                    )
                elif not bootstrap_done:
                    bootstrap_batches.append(batch)
                    if len(bootstrap_batches) < BOOTSTRAP_BATCHES:
                        self.set_health(
                            status="starting",
                            connected=True,
                            influx_ok=True,
                            last_channels=channel_log_data(batch_values),
                            message=f"bootstrap review {len(bootstrap_batches)}/{BOOTSTRAP_BATCHES}",
                            skipped_channels=skipped,
                            read_mode=_get_read_mode(),
                            loop_sleep_seconds=_get_loop_sleep(),
                            loop_hz=loop_hz,
                        )
                        time.sleep(_get_loop_sleep())
                        continue
                    filtered_batches, dropped = filter_bootstrap_batches(bootstrap_batches)
                    bootstrap_done = True
                    print(
                        f">>> Bootstrap review complete for {self.device_id}: "
                        f"kept {len(filtered_batches)}/{len(bootstrap_batches)} batches, "
                        f"dropped {dropped} bootstrap outlier points"
                    )
                    self.write_runtime_points(
                        filtered_batches,
                        f"bootstrap kept {len(filtered_batches)} batches",
                        skipped=skipped,
                        loop_hz=loop_hz,
                    )
                else:
                    self.write_runtime_points([batch], f"logged {len(batch_values)} points", skipped=skipped, loop_hz=loop_hz)
                time.sleep(_get_loop_sleep())
        except Exception as exc:
            print(f"!!! RedLab runtime error for {self.device_id}: {exc}")
            self.set_health(status="error", connected=False, influx_ok=False, message=f"runtime error: {exc}")
        finally:
            self.writer_stop_event.set()
            with self.write_queue_lock:
                self.write_queue_lock.notify_all()
            with self.lock:
                daq_device = self.daq_device
                self.daq_device = None
                self.ai_device = None
                self.ai_config = None
                self.filter_state["last_valid_by_channel"].clear()
            if daq_device:
                try:
                    daq_device.disconnect()
                    daq_device.release()
                except Exception:
                    pass
            if self.stop_event.is_set():
                self.set_health(status="offline", connected=False, message="runtime stopped")


def _registry_overall_health() -> dict:
    snapshot = _runtime_snapshot()
    running_states = [
        state for state in snapshot.values()
        if state.get("status") in {"starting", "running", "degraded"}
    ]
    preferred_id = _active_device_id()
    selected = snapshot.get(preferred_id) if preferred_id else None
    if selected is None and running_states:
        selected = sorted(running_states, key=lambda item: item.get("device_id") or "")[0]
    if selected is None:
        return {
            "status": "offline",
            "connected": False,
            "influx_ok": bool(TOKEN and ORG and BUCKET),
            "last_ok_ts": None,
            "last_channels": [],
            "message": "no RedLab runtimes running",
            "device_name": None,
            "device_unique_id": None,
            "device_fw_main": None,
            "read_mode": _get_read_mode(),
            "loop_sleep_seconds": _get_loop_sleep(),
            "loop_hz": None,
            "devices": snapshot,
            "device_count": len(snapshot),
            "running_device_count": 0,
        }
    payload = dict(selected)
    payload.update(
        devices=snapshot,
        device_count=len(snapshot),
        running_device_count=len(running_states),
        connected=bool(running_states),
        status="running" if any(state.get("status") == "running" for state in running_states) else payload.get("status"),
        read_mode=_get_read_mode(),
        loop_sleep_seconds=_get_loop_sleep(),
    )
    return payload


def registry_loop(write_api) -> None:
    set_health(status="starting", influx_ok=True, message="registry starting")
    reenumeration_hits: dict[str, int] = {}
    while True:
        try:
            descriptors = _valid_inventory_descriptors()
            seen_device_ids = _descriptor_device_ids(descriptors)
            fresh_device_ids: set[str] = set()
            if REDLAB_REENUMERATION_RECOVERY:
                fresh_device_ids = _fresh_inventory_device_ids()
                runtime_ids = set(_runtime_snapshot().keys())
                stale_missing = fresh_device_ids - seen_device_ids - runtime_ids
                if stale_missing:
                    for device_id in stale_missing:
                        reenumeration_hits[device_id] = reenumeration_hits.get(device_id, 0) + 1
                    for device_id in list(reenumeration_hits):
                        if device_id not in stale_missing:
                            reenumeration_hits.pop(device_id, None)
                    confirmed_missing = {
                        device_id for device_id, count in reenumeration_hits.items()
                        if count >= REDLAB_REENUMERATION_CONFIRMATIONS
                    }
                    if confirmed_missing:
                        cooldown_remaining = _restart_cooldown_remaining()
                        visible_missing = confirmed_missing - seen_device_ids
                        if not visible_missing:
                            print(
                                ">>> RedLab USB re-enumeration stale state already visible in current process; "
                                f"skipping restart: {sorted(confirmed_missing)}"
                            )
                            reenumeration_hits.clear()
                        elif cooldown_remaining > 0:
                            print(
                                "!!! RedLab registry sees stale USB re-enumeration, "
                                f"but restart cooldown has {cooldown_remaining:.1f}s remaining: "
                                f"{sorted(confirmed_missing)}"
                            )
                            set_health(
                                status="degraded",
                                connected=bool(_running_runtime_ids()),
                                message=f"USB re-enumeration restart cooldown {cooldown_remaining:.1f}s",
                            )
                        else:
                            if REDLAB_REENUMERATION_DEBOUNCE_SECONDS:
                                print(
                                    "!!! RedLab registry sees possible USB re-enumeration stale state; "
                                    f"waiting {REDLAB_REENUMERATION_DEBOUNCE_SECONDS:.1f}s before restart check: "
                                    f"{sorted(confirmed_missing)}"
                                )
                                time.sleep(REDLAB_REENUMERATION_DEBOUNCE_SECONDS)
                            descriptors = _valid_inventory_descriptors()
                            seen_device_ids = _descriptor_device_ids(descriptors)
                            fresh_device_ids = _fresh_inventory_device_ids()
                            runtime_ids = set(_runtime_snapshot().keys())
                            still_missing = fresh_device_ids - seen_device_ids - runtime_ids
                            restart_needed = confirmed_missing & still_missing
                            if restart_needed:
                                _restart_process_for_usb_reenumeration(restart_needed)
                            else:
                                print(">>> RedLab USB re-enumeration settled without process restart")
                                reenumeration_hits.clear()
                else:
                    reenumeration_hits.clear()
            with RUNTIME_LOCK:
                for device_id, runtime in list(RUNTIMES.items()):
                    if device_id not in seen_device_ids and device_id not in fresh_device_ids:
                        runtime.stop("device disconnected")
                        RUNTIMES.pop(device_id, None)
                for descriptor in descriptors:
                    device_id = canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
                    runtime = RUNTIMES.get(device_id)
                    if runtime is None or (runtime.thread and not runtime.thread.is_alive() and runtime.health_snapshot().get("status") == "error"):
                        runtime = RedLabRuntime(descriptor, write_api)
                        RUNTIMES[device_id] = runtime
                        runtime.start()
            set_health(**_registry_overall_health())
        except Exception as exc:
            print(f"!!! RedLab registry error: {exc}")
            set_health(status="degraded", connected=False, message=f"registry error: {exc}")
        time.sleep(max(1.0, REDLAB_DISCOVERY_INTERVAL_SECONDS))


def main():
    start_health_server()
    if not all([TOKEN, ORG, BUCKET]):
        print("!!! Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET")
        set_health(status="degraded", connected=False, influx_ok=False, message="missing Influx credentials")
        sys.exit(1)

    client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    set_health(status="starting", influx_ok=True, message="influx configured")
    registry_loop(write_api)

if __name__ == "__main__":
    main()
