import json
import os
import threading
import time
from copy import deepcopy
from typing import Any

import docker
from flask import Flask, Response, jsonify, request, send_from_directory
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
from websocket import WebSocketApp, create_connection  # type: ignore

try:
    from .matter_docker import container_status, set_container_running
    from .matter_nodes import fetch_matter_node_snapshot
    from .otbr_diag import otbr_diag_snapshot
    from .matter_payload import extract_event
    from .thread_diag import parse_thread_diag_lines, record_to_dict
    from .thread_diag_store import ThreadDiagStore
    from .thread_diag_transport import load_transport_lines
    from .thread_topology import build_thread_topology
    from .matter_ui import INDEX_HTML
except ImportError:
    from matter_docker import container_status, set_container_running
    from matter_nodes import fetch_matter_node_snapshot
    from otbr_diag import otbr_diag_snapshot
    from matter_payload import extract_event
    from thread_diag import parse_thread_diag_lines, record_to_dict
    from thread_diag_store import ThreadDiagStore
    from thread_diag_transport import load_transport_lines
    from thread_topology import build_thread_topology
    from matter_ui import INDEX_HTML


MATTER_APP_PORT = int(os.getenv("MATTER_APP_PORT", "3060"))
MATTER_SERVER_WS_URL = os.getenv("MATTER_SERVER_WS_URL", "ws://host.docker.internal:5580/ws").strip()
MATTER_WS_RECONNECT_SEC = float(os.getenv("MATTER_WS_RECONNECT_SEC", "3.0"))
MATTER_WS_PING_INTERVAL_SEC = int(os.getenv("MATTER_WS_PING_INTERVAL_SEC", "20"))
MATTER_WS_PING_TIMEOUT_SEC = int(os.getenv("MATTER_WS_PING_TIMEOUT_SEC", "10"))
MATTER_POLL_INTERVAL_SEC = float(os.getenv("MATTER_POLL_INTERVAL_SEC", "60"))
MATTER_POLL_NODE_ID = int(os.getenv("MATTER_POLL_NODE_ID", "1"))
MATTER_POLL_BATTERY_ENDPOINT_ID = int(os.getenv("MATTER_POLL_BATTERY_ENDPOINT_ID", "5"))
MATTER_INFLUX_MEASUREMENT = os.getenv("MATTER_INFLUX_MEASUREMENT", "matter_sensor").strip() or "matter_sensor"
MATTER_SOURCE_TAG = os.getenv("MATTER_SOURCE_TAG", "matter-server").strip() or "matter-server"
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086").strip()
INFLUX_ORG = os.getenv("INFLUX_ORG", "").strip()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "").strip()
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "").strip()
MATTER_THREAD_DIAG_STATE_FILE = os.getenv("MATTER_THREAD_DIAG_STATE_FILE", "/data/thread-diag.json").strip()
MATTER_THREAD_DIAG_SOURCE = os.getenv("MATTER_THREAD_DIAG_SOURCE", "off").strip().lower()
MATTER_THREAD_DIAG_FILE = os.getenv("MATTER_THREAD_DIAG_FILE", "/data/thread-diag-feed.txt").strip()
MATTER_THREAD_DIAG_HTTP_URL = os.getenv("MATTER_THREAD_DIAG_HTTP_URL", "").strip()
MATTER_THREAD_DIAG_POLL_SEC = float(os.getenv("MATTER_THREAD_DIAG_POLL_SEC", "5.0"))
MATTER_THREAD_DIAG_HTTP_TIMEOUT_SEC = float(os.getenv("MATTER_THREAD_DIAG_HTTP_TIMEOUT_SEC", "5.0"))
MATTER_NODE_SNAPSHOT_TTL_SEC = float(os.getenv("MATTER_NODE_SNAPSHOT_TTL_SEC", "2.0"))
MATTER_NODE_REBOOT_ENABLE_KEY_B64 = os.getenv(
    "MATTER_NODE_REBOOT_ENABLE_KEY_B64",
    "ABEiM0RVZneImaq7zN3u/w==",
).strip()
MATTER_NODE_REBOOT_EVENT_TRIGGER = int(os.getenv("MATTER_NODE_REBOOT_EVENT_TRIGGER", "0xFFF10001"), 0)

app = Flask(__name__)
DOCKER_CLIENT = docker.from_env()
MATTER_CONTROL_TARGETS: dict[str, list[str]] = {
    "openthread": ["openthread-border-router"],
    "matter-server": ["matter-server"],
}
MATTER_STANDARD_COMMANDS: dict[tuple[int, str], dict[str, Any]] = {
    (3, "Identify"): {"payload": {"identifyTime": 5}},
    (6, "Off"): {"payload": {}},
    (6, "On"): {"payload": {}},
    (6, "Toggle"): {"payload": {}},
}
MATTER_POLL_ENVIRONMENT_ATTRIBUTES: tuple[tuple[int, str, str], ...] = (
    (1, "1026", "0"),
    (2, "1029", "0"),
    (3, "1027", "0"),
    (3, "1027", "16"),
    (3, "1068", "0"),
    (3, "1066", "0"),
    (3, "1069", "0"),
)

_state_lock = threading.Lock()
_influx_lock = threading.Lock()
_otbr_diag_lock = threading.Lock()
_matter_nodes_snapshot_lock = threading.Lock()
_influx_client: InfluxDBClient | None = None
_influx_write_api: Any = None
_last_good_otbr_diag: dict[str, Any] | None = None
_last_good_matter_nodes_snapshot: list[dict[str, Any]] | None = None
_last_good_matter_nodes_snapshot_at: float | None = None
_matter_nodes_snapshot_refresh_inflight = False
_matter_nodes_snapshot_refresh_thread: threading.Thread | None = None
_stop_event = threading.Event()
_collector_thread: threading.Thread | None = None
_poller_thread: threading.Thread | None = None
_thread_diag_transport_thread: threading.Thread | None = None
_thread_diag_store = ThreadDiagStore(state_file=MATTER_THREAD_DIAG_STATE_FILE)

_state: dict[str, Any] = {
    "started_at": time.time(),
    "connected": False,
    "events_received": 0,
    "events_written": 0,
    "write_errors": 0,
    "parse_errors": 0,
    "reconnects": 0,
    "last_message_at": None,
    "last_connect_at": None,
    "last_disconnect_at": None,
    "last_event_type": None,
    "last_error": "",
    "thread_diag_transport_source": MATTER_THREAD_DIAG_SOURCE or "off",
    "thread_diag_transport_enabled": MATTER_THREAD_DIAG_SOURCE not in {"", "off", "disabled", "none"},
    "thread_diag_transport_last_fetch_at": None,
    "thread_diag_transport_last_ingest_at": None,
    "thread_diag_transport_last_line_count": 0,
    "thread_diag_transport_last_accepted": 0,
    "thread_diag_transport_last_rejected": 0,
    "thread_diag_transport_last_error": "",
}

_NO_STORE_PATHS = {
    "/",
    "/health",
    "/openthread/diag",
    "/thread-diag",
    "/thread-topology",
}

def _log(message: str) -> None:
    print(f"[matter-collector] {message}", flush=True)


def _set_state(**updates: Any) -> None:
    with _state_lock:
        _state.update(updates)


def _bump(name: str, inc: int = 1) -> None:
    with _state_lock:
        _state[name] = int(_state.get(name, 0)) + inc


def _state_snapshot() -> dict[str, Any]:
    with _state_lock:
        snap = dict(_state)
    now = time.time()
    last_message_at = snap.get("last_message_at")
    snap["last_message_age_sec"] = round(now - last_message_at, 1) if isinstance(last_message_at, (int, float)) else None
    return snap


def _ensure_influx_writer() -> Any | None:
    global _influx_client, _influx_write_api
    if not (INFLUX_URL and INFLUX_ORG and INFLUX_BUCKET and INFLUX_TOKEN):
        return None
    with _influx_lock:
        if _influx_write_api is not None:
            return _influx_write_api
        try:
            _influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            _influx_write_api = _influx_client.write_api(write_options=SYNCHRONOUS)
            _log("influx writer ready")
            return _influx_write_api
        except Exception as exc:
            _set_state(last_error=f"influx_init_error: {exc}")
            return None


def _write_event(record: dict[str, Any]) -> bool:
    writer = _ensure_influx_writer()
    if writer is None:
        _bump("write_errors")
        return False
    try:
        point = Point(MATTER_INFLUX_MEASUREMENT).tag("source", MATTER_SOURCE_TAG).tag("event_type", record["event_type"])
        for tag_key, tag_value in record["tags"].items():
            point = point.tag(tag_key, str(tag_value))
        for field_key, field_value in record["fields"].items():
            point = point.field(field_key, field_value)
        writer.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point, write_precision=WritePrecision.NS)
        _bump("events_written")
        return True
    except Exception as exc:
        _set_state(last_error=f"influx_write_error: {exc}")
        _bump("write_errors")
        return False


def _on_open(_ws: WebSocketApp) -> None:
    now = time.time()
    _set_state(connected=True, last_connect_at=now, last_error="")
    _log("websocket connected")
    try:
        # Matter server emits node/attribute updates only after start_listening.
        _ws.send(
            json.dumps(
                {
                    "message_id": "1",
                    "command": "start_listening",
                    "args": {},
                }
            )
        )
    except Exception as exc:
        _set_state(last_error=f"start_listening_error: {exc}")


def _on_close(_ws: WebSocketApp, close_status_code: Any, close_msg: Any) -> None:
    now = time.time()
    _set_state(connected=False, last_disconnect_at=now)
    if close_status_code is not None or close_msg:
        _set_state(last_error=f"ws_closed code={close_status_code} msg={close_msg}")
    _log(f"websocket closed code={close_status_code} msg={close_msg}")


def _on_error(_ws: WebSocketApp, error: Any) -> None:
    _set_state(last_error=f"ws_error: {error}")
    _log(f"websocket error: {error}")


def _on_message(_ws: WebSocketApp, raw_message: str) -> None:
    _bump("events_received")
    _set_state(last_message_at=time.time())
    try:
        payload = json.loads(raw_message)
    except Exception:
        _bump("parse_errors")
        _set_state(last_error="json_parse_error")
        return

    if not (isinstance(payload, dict) and isinstance(payload.get("event"), str)):
        return

    payload = dict(payload)
    payload["event_type"] = payload.get("event")

    record = extract_event(payload)
    if record is None:
        return

    _set_state(last_event_type=record["event_type"])
    _write_event(record)


def _collect_forever() -> None:
    while not _stop_event.is_set():
        _bump("reconnects")
        ws = WebSocketApp(
            MATTER_SERVER_WS_URL,
            on_open=_on_open,
            on_close=_on_close,
            on_error=_on_error,
            on_message=_on_message,
        )
        try:
            ws.run_forever(
                ping_interval=MATTER_WS_PING_INTERVAL_SEC,
                ping_timeout=MATTER_WS_PING_TIMEOUT_SEC,
            )
        except Exception as exc:
            _set_state(last_error=f"ws_run_error: {exc}", connected=False)
            _log(f"websocket run error: {exc}")
        if _stop_event.is_set():
            break
        time.sleep(max(0.5, MATTER_WS_RECONNECT_SEC))


def _read_attribute_once(node_id: int, attribute_path: str) -> Any | None:
    ws = None
    try:
        ws = create_connection(MATTER_SERVER_WS_URL, timeout=8)
        # Initial message contains server info.
        ws.recv()
        ws.send(
            json.dumps(
                {
                    "message_id": "1",
                    "command": "read_attribute",
                    "args": {
                        "node_id": node_id,
                        "attribute_path": attribute_path,
                    },
                }
            )
        )
        raw = ws.recv()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return result.get(attribute_path)
    except Exception:
        return None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _fetch_otbr_diag_snapshot(timeout_sec: float = 5.0) -> dict[str, Any]:
    global _last_good_otbr_diag
    try:
        diag = otbr_diag_snapshot(DOCKER_CLIENT)
    except Exception:
        with _otbr_diag_lock:
            return dict(_last_good_otbr_diag or {})

    meshdiag_routers = (
        (((diag.get("meshdiag") or {}).get("topology_children") or {}).get("parsed") or {}).get("routers") or []
    )
    is_good = bool(diag.get("available")) and not diag.get("errors") and bool(meshdiag_routers)
    with _otbr_diag_lock:
        if is_good:
            _last_good_otbr_diag = dict(diag)
            return diag
        if _last_good_otbr_diag is not None:
            cached = dict(_last_good_otbr_diag)
            cached["cached_due_to_incomplete_snapshot"] = True
            cached["latest_errors"] = list(diag.get("errors") or [])
            return cached
    return diag


def _control_target_payload(target: str) -> dict[str, Any]:
    services = {name: container_status(DOCKER_CLIENT, name) for name in MATTER_CONTROL_TARGETS.get(target, [])}
    return {
        "all_running": bool(services) and all(state == "running" for state in services.values()),
        "services": services,
    }


def _control_target_action(target: str, action: str) -> dict[str, Any]:
    if target not in MATTER_CONTROL_TARGETS:
        return {"success": False, "error": "unknown_target"}
    if action not in {"start", "stop", "restart"}:
        return {"success": False, "error": "unknown_action"}
    if target == "matter-server" and action in {"start", "restart"}:
        return {
            "success": False,
            "error": "host_restart_required",
            "details": "Start or restart matter-server with ./scripts/restart-matter-server.sh so the selected BLE mode is applied before container recreation.",
        }

    actions: list[str] = []
    try:
        for service_name in MATTER_CONTROL_TARGETS[target]:
            if action == "restart":
                actions.append(set_container_running(DOCKER_CLIENT, service_name, False))
                actions.append(set_container_running(DOCKER_CLIENT, service_name, True))
            else:
                actions.append(set_container_running(DOCKER_CLIENT, service_name, action == "start"))
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    state = _control_target_payload(target)
    return {"success": True, "target": target, "action": action, "actions": actions, **state}


def _matter_ws_request(command: str, args: dict[str, Any], timeout_sec: float = 8.0) -> dict[str, Any]:
    message_id = str(int(time.time() * 1000))
    ws = None
    try:
        ws = create_connection(MATTER_SERVER_WS_URL, timeout=timeout_sec)
        ws.recv()
        ws.send(json.dumps({"message_id": message_id, "command": command, "args": args}))
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            raw = ws.recv()
            payload = json.loads(raw)
            if not isinstance(payload, dict) or str(payload.get("message_id") or "") != message_id:
                continue
            return payload
        return {"error_code": 408, "details": "matter command timed out"}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _node_supports_standard_command(node_id: int, endpoint_id: int, cluster_id: int, command_name: str) -> bool:
    nodes = _get_matter_node_snapshot_cached(blocking=True)
    for node in nodes:
        if node.get("node_id") != node_id:
            continue
        if not node.get("available"):
            return False
        for control in node.get("standard_controls") or []:
            if (
                control.get("endpoint_id") == endpoint_id
                and control.get("cluster_id") == cluster_id
                and command_name in (control.get("commands") or [])
            ):
                return True
    return False


def _node_supports_air_reboot(node_id: int) -> bool:
    nodes = _get_matter_node_snapshot_cached(blocking=True)
    for node in nodes:
        if node.get("node_id") != node_id:
            continue
        if not node.get("available"):
            return False
        return bool(node.get("air_reboot_supported"))
    return False


def _reset_matter_nodes_snapshot_cache() -> None:
    global _last_good_matter_nodes_snapshot, _last_good_matter_nodes_snapshot_at
    global _matter_nodes_snapshot_refresh_inflight, _matter_nodes_snapshot_refresh_thread
    with _matter_nodes_snapshot_lock:
        _last_good_matter_nodes_snapshot = None
        _last_good_matter_nodes_snapshot_at = None
        _matter_nodes_snapshot_refresh_inflight = False
        _matter_nodes_snapshot_refresh_thread = None


def _refresh_matter_node_snapshot_cache() -> list[dict[str, Any]]:
    global _last_good_matter_nodes_snapshot, _last_good_matter_nodes_snapshot_at
    global _matter_nodes_snapshot_refresh_inflight
    try:
        snapshot = fetch_matter_node_snapshot(MATTER_SERVER_WS_URL)
        now = time.time()
        with _matter_nodes_snapshot_lock:
            _last_good_matter_nodes_snapshot = deepcopy(snapshot)
            _last_good_matter_nodes_snapshot_at = now
        return deepcopy(snapshot)
    finally:
        with _matter_nodes_snapshot_lock:
            _matter_nodes_snapshot_refresh_inflight = False


def _trigger_matter_node_snapshot_refresh(force: bool = False, blocking: bool = False) -> bool:
    global _matter_nodes_snapshot_refresh_inflight, _matter_nodes_snapshot_refresh_thread
    with _matter_nodes_snapshot_lock:
        ttl_sec = max(0.0, MATTER_NODE_SNAPSHOT_TTL_SEC)
        now = time.time()
        is_fresh = (
            not force
            and ttl_sec > 0
            and _last_good_matter_nodes_snapshot is not None
            and _last_good_matter_nodes_snapshot_at is not None
            and (now - _last_good_matter_nodes_snapshot_at) < ttl_sec
        )
        if is_fresh or _matter_nodes_snapshot_refresh_inflight:
            return False
        _matter_nodes_snapshot_refresh_inflight = True

    if blocking:
        _refresh_matter_node_snapshot_cache()
        return True

    def _runner() -> None:
        try:
            _refresh_matter_node_snapshot_cache()
        except Exception as exc:
            _set_state(last_error=f"matter_snapshot_refresh_error: {exc}")

    thread = threading.Thread(target=_runner, name="matter-node-snapshot-refresh", daemon=True)
    with _matter_nodes_snapshot_lock:
        _matter_nodes_snapshot_refresh_thread = thread
    thread.start()
    return True


def _get_matter_node_snapshot_cached(force: bool = False, blocking: bool = True) -> list[dict[str, Any]]:
    ttl_sec = max(0.0, MATTER_NODE_SNAPSHOT_TTL_SEC)
    now = time.time()
    with _matter_nodes_snapshot_lock:
        cached = deepcopy(_last_good_matter_nodes_snapshot) if _last_good_matter_nodes_snapshot is not None else None
        fetched_at = _last_good_matter_nodes_snapshot_at
    if cached is None and not blocking and not force:
        _trigger_matter_node_snapshot_refresh(force=True, blocking=False)
        return []
    if cached is None or force or ttl_sec <= 0:
        try:
            return _refresh_matter_node_snapshot_cache()
        except Exception:
            if cached is not None:
                return cached
            raise
    if fetched_at is not None and (now - fetched_at) >= ttl_sec:
        _trigger_matter_node_snapshot_refresh()
    return cached


def _matter_node_snapshot_pending() -> bool:
    with _matter_nodes_snapshot_lock:
        return _last_good_matter_nodes_snapshot is None and bool(_matter_nodes_snapshot_refresh_inflight)


def _poll_target_node_ids() -> list[int]:
    node_ids: list[int] = []
    if MATTER_POLL_NODE_ID > 0:
        node_ids.append(MATTER_POLL_NODE_ID)
    try:
        for node in _get_matter_node_snapshot_cached(blocking=False):
            node_id = node.get("node_id")
            if isinstance(node_id, int) and node_id > 0 and node_id not in node_ids:
                node_ids.append(node_id)
    except Exception as exc:
        _log(f"matter poll target discovery failed: {exc}")
    return node_ids


def _poll_numeric_attribute(node_id: int, endpoint_id: int, cluster_id: str, attribute_id: str = "0") -> None:
    value = _read_attribute_once(node_id, f"{endpoint_id}/{cluster_id}/{attribute_id}")
    if not isinstance(value, (int, float)):
        return
    record = {
        "event_type": "poll_attribute",
        "tags": {
            "node_id": str(node_id),
            "endpoint_id": str(endpoint_id),
            "cluster_id": cluster_id,
            "attribute_id": attribute_id,
        },
        "fields": {"value": float(value)},
    }
    _bump("events_received")
    _set_state(last_message_at=time.time(), last_event_type="poll_attribute")
    _write_event(record)


def _poll_node_snapshot_once() -> None:
    if MATTER_POLL_INTERVAL_SEC <= 0:
        return

    target_node_ids = _poll_target_node_ids()

    local_temp_centi = _read_attribute_once(MATTER_POLL_NODE_ID, "1/513/0")
    heat_setpoint_centi = _read_attribute_once(MATTER_POLL_NODE_ID, "1/513/18")
    fields: dict[str, float] = {}
    if isinstance(local_temp_centi, (int, float)):
        fields["thermostat_local_temperature_c"] = round(float(local_temp_centi) / 100.0, 2)
    if isinstance(heat_setpoint_centi, (int, float)):
        fields["thermostat_occupied_heating_setpoint_c"] = round(float(heat_setpoint_centi) / 100.0, 2)
    if fields:
        record = {
            "event_type": "poll_snapshot",
            "tags": {"node_id": str(MATTER_POLL_NODE_ID)},
            "fields": fields,
        }
        _bump("events_received")
        _set_state(last_message_at=time.time(), last_event_type="poll_snapshot")
        _write_event(record)

    if MATTER_POLL_NODE_ID > 0:
        for attribute_id in (11, 12, 26):
            _poll_numeric_attribute(MATTER_POLL_NODE_ID, MATTER_POLL_BATTERY_ENDPOINT_ID, "47", str(attribute_id))

    for node_id in target_node_ids:
        for endpoint_id, cluster_id, attribute_id in MATTER_POLL_ENVIRONMENT_ATTRIBUTES:
            _poll_numeric_attribute(node_id, endpoint_id, cluster_id, attribute_id)


def _poll_forever() -> None:
    while not _stop_event.is_set():
        _poll_node_snapshot_once()
        wait_sec = max(2.0, MATTER_POLL_INTERVAL_SEC)
        _stop_event.wait(wait_sec)


def _thread_diag_transport_forever() -> None:
    wait_sec = max(1.0, MATTER_THREAD_DIAG_POLL_SEC)
    while not _stop_event.is_set():
        try:
            lines = load_transport_lines(
                MATTER_THREAD_DIAG_SOURCE,
                file_path=MATTER_THREAD_DIAG_FILE,
                http_url=MATTER_THREAD_DIAG_HTTP_URL,
                timeout_sec=MATTER_THREAD_DIAG_HTTP_TIMEOUT_SEC,
            )
            _set_state(
                thread_diag_transport_last_fetch_at=time.time(),
                thread_diag_transport_last_line_count=len(lines),
                thread_diag_transport_last_error="",
            )
            if lines:
                records, errors = parse_thread_diag_lines(lines)
                _thread_diag_store.ingest_records(records)
                _set_state(
                    thread_diag_transport_last_ingest_at=time.time(),
                    thread_diag_transport_last_accepted=len(records),
                    thread_diag_transport_last_rejected=len(errors),
                    thread_diag_transport_last_error="" if not errors else f"rejected={len(errors)}",
                )
        except FileNotFoundError:
            _set_state(
                thread_diag_transport_last_fetch_at=time.time(),
                thread_diag_transport_last_line_count=0,
                thread_diag_transport_last_error="file_not_found",
            )
        except Exception as exc:
            _set_state(
                thread_diag_transport_last_fetch_at=time.time(),
                thread_diag_transport_last_line_count=0,
                thread_diag_transport_last_error=str(exc),
            )
            _log(f"thread diag transport error: {exc}")
        _stop_event.wait(wait_sec)


def _start_collector_once() -> None:
    global _collector_thread, _poller_thread, _thread_diag_transport_thread
    if _collector_thread and _collector_thread.is_alive():
        pass
    else:
        _collector_thread = threading.Thread(target=_collect_forever, name="matter-collector", daemon=True)
        _collector_thread.start()

    if MATTER_POLL_INTERVAL_SEC > 0 and not (_poller_thread and _poller_thread.is_alive()):
        _poller_thread = threading.Thread(target=_poll_forever, name="matter-poller", daemon=True)
        _poller_thread.start()

    if (
        MATTER_THREAD_DIAG_SOURCE not in {"", "off", "disabled", "none"}
        and not (_thread_diag_transport_thread and _thread_diag_transport_thread.is_alive())
    ):
        _thread_diag_transport_thread = threading.Thread(
            target=_thread_diag_transport_forever,
            name="thread-diag-transport",
            daemon=True,
        )
        _thread_diag_transport_thread.start()

    _trigger_matter_node_snapshot_refresh(force=True, blocking=False)


def _extract_thread_diag_lines() -> list[str]:
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            line = payload.get("line")
            lines = payload.get("lines")
            if isinstance(line, str):
                return [line]
            if isinstance(lines, list):
                return [str(item) for item in lines if str(item).strip()]
        if isinstance(payload, list):
            return [str(item) for item in payload if str(item).strip()]
    raw = request.get_data(as_text=True) or ""
    return [line.strip() for line in raw.splitlines() if line.strip()]


@app.route("/health", methods=["GET"])
def health() -> Any:
    snap = _state_snapshot()
    influx_ready = _ensure_influx_writer() is not None
    status = "ok" if snap["connected"] and influx_ready else "degraded"
    return jsonify(_health_payload(status, snap, influx_ready))


def _health_payload(status: str, snap: dict[str, Any], influx_ready: bool) -> dict[str, Any]:
    thread_diag_snapshot = _thread_diag_store.snapshot()
    return dict(
        status=status,
        connected=bool(snap["connected"]),
        influx_ready=influx_ready,
        ws_url=MATTER_SERVER_WS_URL,
        influx_url=INFLUX_URL,
        influx_org=INFLUX_ORG,
        influx_bucket=INFLUX_BUCKET,
        events_received=snap["events_received"],
        events_written=snap["events_written"],
        write_errors=snap["write_errors"],
        parse_errors=snap["parse_errors"],
        reconnects=snap["reconnects"],
        last_message_age_sec=snap["last_message_age_sec"],
        last_event_type=snap["last_event_type"],
        last_error=snap["last_error"],
        thread_diag=thread_diag_snapshot,
        thread_diag_transport=dict(
            source=snap["thread_diag_transport_source"],
            enabled=bool(snap["thread_diag_transport_enabled"]),
            file_path=MATTER_THREAD_DIAG_FILE if MATTER_THREAD_DIAG_SOURCE == "file" else "",
            http_url=MATTER_THREAD_DIAG_HTTP_URL if MATTER_THREAD_DIAG_SOURCE == "http" else "",
            poll_sec=MATTER_THREAD_DIAG_POLL_SEC,
            last_fetch_at=snap["thread_diag_transport_last_fetch_at"],
            last_ingest_at=snap["thread_diag_transport_last_ingest_at"],
            last_line_count=snap["thread_diag_transport_last_line_count"],
            last_accepted=snap["thread_diag_transport_last_accepted"],
            last_rejected=snap["thread_diag_transport_last_rejected"],
            last_error=snap["thread_diag_transport_last_error"],
        ),
    )


@app.route("/thread-diag", methods=["POST"])
def thread_diag_ingest() -> Any:
    lines = _extract_thread_diag_lines()
    if not lines:
        return jsonify(success=False, accepted=0, rejected=0, errors=[{"error": "no lines provided"}]), 400
    records, errors = parse_thread_diag_lines(lines)
    _thread_diag_store.ingest_records(records)
    return jsonify(
        success=True,
        accepted=len(records),
        rejected=len(errors),
        records=[record_to_dict(record) for record in records],
        errors=errors,
        thread_diag=_thread_diag_store.snapshot(),
    )


@app.route("/thread-diag", methods=["GET"])
def thread_diag_snapshot() -> Any:
    return jsonify(success=True, thread_diag=_thread_diag_store.snapshot())


@app.route("/openthread/diag", methods=["GET"])
def openthread_diag_snapshot() -> Any:
    diag = _fetch_otbr_diag_snapshot(timeout_sec=MATTER_THREAD_DIAG_HTTP_TIMEOUT_SEC)
    status = "ok" if diag.get("available") and not diag.get("errors") else "degraded"
    return jsonify(success=True, status=status, diag=diag)


@app.route("/control/openthread/health", methods=["GET"])
def openthread_control_health() -> Any:
    payload = _control_target_payload("openthread")
    return jsonify(success=True, openthread=payload, services=payload["services"])


@app.route("/control/openthread/<action>", methods=["POST"])
def openthread_control_action(action: str) -> Any:
    result = _control_target_action("openthread", action)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@app.route("/control/matter-server/health", methods=["GET"])
def matter_server_control_health() -> Any:
    payload = _control_target_payload("matter-server")
    return jsonify(success=True, matter_server=payload, services=payload["services"])


@app.route("/control/matter-server/<action>", methods=["POST"])
def matter_server_control_action(action: str) -> Any:
    result = _control_target_action("matter-server", action)
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


@app.route("/control/matter-server/wifi-credentials", methods=["POST"])
def matter_server_wifi_credentials() -> Any:
    payload = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}
    ssid = str(payload.get("ssid") or "").strip()
    credentials = str(payload.get("credentials") or "")
    if not ssid:
        return jsonify(success=False, error="missing_ssid"), 400
    if not credentials:
        return jsonify(success=False, error="missing_credentials"), 400

    result = _matter_ws_request(
        "set_wifi_credentials",
        {"ssid": ssid, "credentials": credentials},
        timeout_sec=20.0,
    )
    if result.get("error_code"):
        return jsonify(success=False, error="matter_server_error", details=result), 502
    return jsonify(success=True, ssid=ssid)


@app.route("/nodes/<int:node_id>/commands", methods=["POST"])
def matter_node_standard_command(node_id: int) -> Any:
    payload = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}
    endpoint_id = payload.get("endpoint_id")
    cluster_id = payload.get("cluster_id")
    command_name = str(payload.get("command_name") or "").strip()
    try:
        endpoint_id = int(endpoint_id)
        cluster_id = int(cluster_id)
    except (TypeError, ValueError):
        return jsonify(success=False, error="invalid_endpoint_or_cluster"), 400

    command_spec = MATTER_STANDARD_COMMANDS.get((cluster_id, command_name))
    if command_spec is None:
        return jsonify(success=False, error="unsupported_standard_command"), 400
    if not _node_supports_standard_command(node_id, endpoint_id, cluster_id, command_name):
        return jsonify(success=False, error="command_not_advertised_by_node"), 400

    args = {
        "node_id": node_id,
        "endpoint_id": endpoint_id,
        "cluster_id": cluster_id,
        "command_name": command_name,
        "payload": dict(command_spec["payload"]),
    }
    result = _matter_ws_request("device_command", args)
    if "error_code" in result:
        return jsonify(success=False, error=result.get("details") or "matter_command_failed", matter=result), 502

    _trigger_matter_node_snapshot_refresh(force=True, blocking=False)
    return jsonify(success=True, result=result.get("result"), command=args)


@app.route("/nodes/<int:node_id>/air-reboot", methods=["POST"])
def matter_node_air_reboot(node_id: int) -> Any:
    if not _node_supports_air_reboot(node_id):
        return jsonify(success=False, error="air_reboot_not_advertised_by_node"), 400

    args = {
        "node_id": node_id,
        "endpoint_id": 0,
        "cluster_id": 51,
        "command_name": "TestEventTrigger",
        "payload": {
            "enableKey": MATTER_NODE_REBOOT_ENABLE_KEY_B64,
            "eventTrigger": MATTER_NODE_REBOOT_EVENT_TRIGGER,
        },
    }
    result = _matter_ws_request("device_command", args)
    if "error_code" in result:
        return jsonify(success=False, error=result.get("details") or "matter_reboot_command_failed", matter=result), 502

    _trigger_matter_node_snapshot_refresh(force=True, blocking=False)
    return jsonify(success=True, result=result.get("result"), command=args)


@app.route("/thread-topology", methods=["GET"])
def thread_topology_snapshot() -> Any:
    matter_nodes = _get_matter_node_snapshot_cached(blocking=False)
    matter_snapshot_pending = _matter_node_snapshot_pending()
    thread_diag = _thread_diag_store.snapshot()
    otbr_diag = _fetch_otbr_diag_snapshot(timeout_sec=MATTER_THREAD_DIAG_HTTP_TIMEOUT_SEC)
    topology = build_thread_topology(matter_nodes, thread_diag, otbr_diag)
    return jsonify(
        success=True,
        matter_nodes=matter_nodes,
        matter_snapshot_pending=matter_snapshot_pending,
        thread_diag=thread_diag,
        otbr_diag=otbr_diag,
        topology=topology,
    )


@app.route("/", methods=["GET"])
def root() -> Any:
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/device-common.css", methods=["GET"])
def device_common_css() -> Any:
    return send_from_directory("/app", "device-common.css", mimetype="text/css")


@app.after_request
def add_no_store_headers(response: Response) -> Response:
    if request.path in _NO_STORE_PATHS:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


if __name__ == "__main__":
    _start_collector_once()
    app.run(host="0.0.0.0", port=MATTER_APP_PORT)
