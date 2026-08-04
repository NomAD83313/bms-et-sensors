from __future__ import annotations

import os
import json
import glob
import threading
import time
from pathlib import Path
from typing import Dict, List

import docker
from flask import Flask, jsonify
from svcctl_docker import (
    container_status,
    set_container_running,
    status_payload,
)
from svcctl_host import cpu_percent, memory_stats, serial_devices_by_id, usb_devices
from svcctl_usb import build_usb_guard_state, usb_device_present

app = Flask(__name__)
SVCCTL_PORT = 3020
MSCL_GUARD_ENABLED = True
MSCL_GUARD_INTERVAL_SEC = 5.0
MSCL_SERIAL_STABLE_SEC = 4.0
REDLAB_GUARD_ENABLED = True
REDLAB_GUARD_INTERVAL_SEC = 5.0
REDLAB_USB_VENDOR_ID = "09db"
REDLAB_USB_PRODUCT_ID = "0090"
REDLAB_USB_STABLE_SEC = 4.0
ALMEMO_GUARD_ENABLED = True
ALMEMO_GUARD_INTERVAL_SEC = 5.0
ALMEMO_USB_VENDOR_ID = "10c4"
ALMEMO_USB_PRODUCT_ID = "ea60"
ALMEMO_USB_STABLE_SEC = 4.0
MSCL_UI_ALLOW_WITHOUT_SERIAL = os.getenv("MSCL_UI_ALLOW_WITHOUT_SERIAL", "1").strip().lower() in {"1", "true", "yes", "on"}
KEEP_DEGRADED_SERVICES_RUNNING = os.getenv("SVCCTL_KEEP_DEGRADED_SERVICES_RUNNING", "1").strip().lower() in {"1", "true", "yes", "on"}
DEGRADED_START_RETRY_SEC = float(os.getenv("SVCCTL_DEGRADED_START_RETRY_SEC", "60.0"))
MANUAL_PAUSE_FILE = Path(os.getenv("SVCCTL_MANUAL_PAUSE_FILE", "/tmp/svcctl_manual_pauses.json"))

DOCKER_CLIENT = docker.DockerClient(base_url="unix://var/run/docker.sock")
ACTION_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
MANUAL_PAUSE_LOCK = threading.Lock()

TARGET_MAP: Dict[str, List[str]] = {
    # Keep data storage independent from app-level start/stop actions.
    "redlab": ["redlab-collector"],
    "almemo": ["almemo-collector"],
    "mscl": ["mscl-collector"],
    "pyrometer": ["pyrometer-collector"],
    "optris": ["pyrometer-collector"],
    "messkluppe": ["messkluppe-collector"],
    # Keep Grafana control isolated: InfluxDB is shared by other stacks.
    "grafana": ["grafana"],
}

_CPU_PREV_TOTAL: int | None = None
_CPU_PREV_IDLE: int | None = None
_MSCL_PRESENT_SINCE: float | None = None
_MSCL_LAST_ACTION: str = "init"
_MSCL_LAST_REASON: str = "startup"
_REDLAB_PRESENT_SINCE: float | None = None
_REDLAB_LAST_ACTION: str = "init"
_REDLAB_LAST_REASON: str = "startup"
_ALMEMO_PRESENT_SINCE: float | None = None
_ALMEMO_LAST_ACTION: str = "init"
_ALMEMO_LAST_REASON: str = "startup"
_DEGRADED_LAST_START_AT: Dict[str, float] = {}
_MANUAL_PAUSES: Dict[str, dict] = {}


def _log(msg: str) -> None:
    print(f"[svcctl] {msg}", flush=True)


def _load_manual_pauses() -> None:
    global _MANUAL_PAUSES
    try:
        payload = json.loads(MANUAL_PAUSE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except Exception as exc:
        _log(f"manual_pause_load_error error={exc}")
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    with MANUAL_PAUSE_LOCK:
        _MANUAL_PAUSES = {
            str(target): value
            for target, value in payload.items()
            if isinstance(value, dict)
        }


def _save_manual_pauses() -> None:
    with MANUAL_PAUSE_LOCK:
        payload = dict(_MANUAL_PAUSES)
    try:
        MANUAL_PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_PAUSE_FILE.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        _log(f"manual_pause_save_error error={exc}")


def _manual_pause_target(target: str, reason: str = "manual_stop") -> None:
    with MANUAL_PAUSE_LOCK:
        _MANUAL_PAUSES[target] = {"reason": reason, "ts": time.time()}
    _save_manual_pauses()


def _manual_resume_target(target: str) -> None:
    with MANUAL_PAUSE_LOCK:
        changed = _MANUAL_PAUSES.pop(target, None) is not None
    if changed:
        _save_manual_pauses()


def _manual_pause_snapshot() -> dict:
    with MANUAL_PAUSE_LOCK:
        return dict(_MANUAL_PAUSES)


def _target_manual_paused(target: str) -> bool:
    with MANUAL_PAUSE_LOCK:
        return target in _MANUAL_PAUSES


def _mark_manual_pause_wait(target: str, action_attr: str, reason_attr: str) -> bool:
    if not _target_manual_paused(target):
        return False
    globals()[action_attr] = "manual_pause"
    globals()[reason_attr] = "user_stopped"
    return True


def _container_status(name: str) -> str:
    return container_status(DOCKER_CLIENT, name)


def _redlab_usb_present() -> bool:
    return usb_device_present(REDLAB_USB_VENDOR_ID, REDLAB_USB_PRODUCT_ID)


def _almemo_usb_present() -> bool:
    return usb_device_present(ALMEMO_USB_VENDOR_ID, ALMEMO_USB_PRODUCT_ID)


def _mscl_serial_candidates() -> list[str]:
    configured = os.getenv("MSCL_PORT", "").strip()
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(sorted(glob.glob("/dev/serial/by-id/*WSDA-Base-200*")))
    return candidates


def _mscl_serial_present() -> tuple[bool, str]:
    candidates = _mscl_serial_candidates()
    if not candidates:
        return False, "mscl_port_unset"
    for path in candidates:
        if Path(path).exists():
            return True, path
    return False, candidates[0]


def _mscl_guard_state() -> dict:
    with STATE_LOCK:
        present_since = _MSCL_PRESENT_SINCE
        last_action = _MSCL_LAST_ACTION
        last_reason = _MSCL_LAST_REASON
    present, detected_path = _mscl_serial_present()
    stable_for = 0.0
    if present and present_since is not None:
        stable_for = max(0.0, time.time() - present_since)
    return {
        "enabled": MSCL_GUARD_ENABLED,
        "ui_without_serial": MSCL_UI_ALLOW_WITHOUT_SERIAL,
        "serial_present": present,
        "serial_detected_path": detected_path,
        "serial_candidates": _mscl_serial_candidates(),
        "serial_stable_for_sec": round(stable_for, 1),
        "last_action": last_action,
        "last_reason": last_reason,
        "manual_paused": _target_manual_paused("mscl"),
        "service_status": _container_status("mscl-collector"),
    }


def _redlab_guard_state() -> dict:
    with STATE_LOCK:
        present_since = _REDLAB_PRESENT_SINCE
        last_action = _REDLAB_LAST_ACTION
        last_reason = _REDLAB_LAST_REASON
    state = build_usb_guard_state(
        enabled=REDLAB_GUARD_ENABLED,
        vendor_id=REDLAB_USB_VENDOR_ID,
        product_id=REDLAB_USB_PRODUCT_ID,
        present_since=present_since,
        last_action=last_action,
        last_reason=last_reason,
        service_status=_container_status("redlab-collector"),
    )
    state["manual_paused"] = _target_manual_paused("redlab")
    return state


def _almemo_guard_state() -> dict:
    with STATE_LOCK:
        present_since = _ALMEMO_PRESENT_SINCE
        last_action = _ALMEMO_LAST_ACTION
        last_reason = _ALMEMO_LAST_REASON
    state = build_usb_guard_state(
        enabled=ALMEMO_GUARD_ENABLED,
        vendor_id=ALMEMO_USB_VENDOR_ID,
        product_id=ALMEMO_USB_PRODUCT_ID,
        present_since=present_since,
        last_action=last_action,
        last_reason=last_reason,
        service_status=_container_status("almemo-collector"),
    )
    state["manual_paused"] = _target_manual_paused("almemo")
    return state


def _target_state_payload(target: str) -> dict:
    services = status_payload(DOCKER_CLIENT, TARGET_MAP).get(target, {})
    return {
        "manual_paused": _target_manual_paused(target),
        "all_running": bool(services) and all(state == "running" for state in services.values()),
        "services": services,
    }


def _start_mscl(reason: str) -> None:
    global _MSCL_LAST_ACTION, _MSCL_LAST_REASON
    action = _set_container_running("mscl-collector", True)
    with STATE_LOCK:
        _MSCL_LAST_ACTION = action
        _MSCL_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _stop_mscl(reason: str) -> None:
    global _MSCL_LAST_ACTION, _MSCL_LAST_REASON
    action = _set_container_running("mscl-collector", False)
    with STATE_LOCK:
        _MSCL_LAST_ACTION = action
        _MSCL_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _cpu_percent() -> float:
    global _CPU_PREV_TOTAL, _CPU_PREV_IDLE
    percent, _CPU_PREV_TOTAL, _CPU_PREV_IDLE = cpu_percent(_CPU_PREV_TOTAL, _CPU_PREV_IDLE)
    return percent


def _set_container_running(name: str, should_run: bool, timeout: int = 15) -> str:
    return set_container_running(DOCKER_CLIENT, name, should_run, timeout)


def _degraded_start_allowed(service_name: str, now: float) -> tuple[bool, float]:
    with STATE_LOCK:
        last_started_at = _DEGRADED_LAST_START_AT.get(service_name)
        if last_started_at is not None:
            age = now - last_started_at
            if age < DEGRADED_START_RETRY_SEC:
                return False, round(DEGRADED_START_RETRY_SEC - age, 1)
        _DEGRADED_LAST_START_AT[service_name] = now
    return True, 0.0


def _start_targets(names: List[str]) -> List[str]:
    return [_set_container_running(name, True) for name in names]


def _stop_targets(names: List[str]) -> List[str]:
    return [_set_container_running(name, False) for name in reversed(names)]


def _start_redlab(reason: str) -> None:
    global _REDLAB_LAST_ACTION, _REDLAB_LAST_REASON
    action = _set_container_running("redlab-collector", True)
    with STATE_LOCK:
        _REDLAB_LAST_ACTION = action
        _REDLAB_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _stop_redlab(reason: str) -> None:
    global _REDLAB_LAST_ACTION, _REDLAB_LAST_REASON
    action = _set_container_running("redlab-collector", False)
    with STATE_LOCK:
        _REDLAB_LAST_ACTION = action
        _REDLAB_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _start_almemo(reason: str) -> None:
    global _ALMEMO_LAST_ACTION, _ALMEMO_LAST_REASON
    action = _set_container_running("almemo-collector", True)
    with STATE_LOCK:
        _ALMEMO_LAST_ACTION = action
        _ALMEMO_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _stop_almemo(reason: str) -> None:
    global _ALMEMO_LAST_ACTION, _ALMEMO_LAST_REASON
    action = _set_container_running("almemo-collector", False)
    with STATE_LOCK:
        _ALMEMO_LAST_ACTION = action
        _ALMEMO_LAST_REASON = reason
    _log(f"{action} reason={reason}")


def _ensure_mscl_guard_step() -> None:
    global _MSCL_PRESENT_SINCE, _MSCL_LAST_ACTION, _MSCL_LAST_REASON

    if not MSCL_GUARD_ENABLED:
        return
    if _mark_manual_pause_wait("mscl", "_MSCL_LAST_ACTION", "_MSCL_LAST_REASON"):
        return

    now = time.time()
    present, detected_path = _mscl_serial_present()
    if not present:
        _MSCL_PRESENT_SINCE = None
        if MSCL_UI_ALLOW_WITHOUT_SERIAL or KEEP_DEGRADED_SERVICES_RUNNING:
            reason = "serial_missing_ui_mode" if MSCL_UI_ALLOW_WITHOUT_SERIAL else "serial_missing_degraded_mode"
            if _container_status("mscl-collector") != "running":
                allowed, wait_for = _degraded_start_allowed("mscl-collector", now)
                if allowed:
                    _start_mscl(reason)
                else:
                    with STATE_LOCK:
                        _MSCL_LAST_ACTION = "wait"
                        _MSCL_LAST_REASON = f"{reason}_retry:{wait_for}s"
            else:
                with STATE_LOCK:
                    _MSCL_LAST_ACTION = "noop"
                    _MSCL_LAST_REASON = f"{reason}:{detected_path}"
            return
        if _container_status("mscl-collector") == "running":
            _stop_mscl("serial_missing")
        else:
            with STATE_LOCK:
                _MSCL_LAST_ACTION = "noop"
                _MSCL_LAST_REASON = f"serial_missing:{detected_path}"
        return

    if _MSCL_PRESENT_SINCE is None:
        _MSCL_PRESENT_SINCE = now

    stable_for = now - _MSCL_PRESENT_SINCE
    if stable_for < MSCL_SERIAL_STABLE_SEC:
        with STATE_LOCK:
            _MSCL_LAST_ACTION = "wait"
            _MSCL_LAST_REASON = f"serial_settling:{round(stable_for,1)}s"
        return

    if _container_status("mscl-collector") != "running":
        _start_mscl("serial_stable")
        return

    with STATE_LOCK:
        _MSCL_LAST_ACTION = "noop"
        _MSCL_LAST_REASON = f"serial_present:{detected_path}"


def _ensure_redlab_guard_step() -> None:
    global _REDLAB_PRESENT_SINCE, _REDLAB_LAST_ACTION, _REDLAB_LAST_REASON

    if not REDLAB_GUARD_ENABLED:
        return
    if _mark_manual_pause_wait("redlab", "_REDLAB_LAST_ACTION", "_REDLAB_LAST_REASON"):
        return

    now = time.time()
    present = _redlab_usb_present()
    if not present:
        _REDLAB_PRESENT_SINCE = None
        if KEEP_DEGRADED_SERVICES_RUNNING:
            if _container_status("redlab-collector") != "running":
                allowed, wait_for = _degraded_start_allowed("redlab-collector", now)
                if allowed:
                    _start_redlab("usb_missing_degraded_mode")
                else:
                    with STATE_LOCK:
                        _REDLAB_LAST_ACTION = "wait"
                        _REDLAB_LAST_REASON = f"usb_missing_degraded_retry:{wait_for}s"
            else:
                with STATE_LOCK:
                    _REDLAB_LAST_ACTION = "noop"
                    _REDLAB_LAST_REASON = "usb_missing_degraded_mode"
            return
        if _container_status("redlab-collector") == "running":
            _stop_redlab("usb_missing")
        else:
            with STATE_LOCK:
                _REDLAB_LAST_ACTION = "noop"
                _REDLAB_LAST_REASON = "usb_missing"
        return

    if _REDLAB_PRESENT_SINCE is None:
        _REDLAB_PRESENT_SINCE = now

    stable_for = now - _REDLAB_PRESENT_SINCE
    if stable_for < REDLAB_USB_STABLE_SEC:
        with STATE_LOCK:
            _REDLAB_LAST_ACTION = "wait"
            _REDLAB_LAST_REASON = f"usb_settling:{round(stable_for,1)}s"
        return

    status = _container_status("redlab-collector")
    if status != "running":
        _start_redlab("usb_stable")
        return

    with STATE_LOCK:
        _REDLAB_LAST_ACTION = "noop"
        _REDLAB_LAST_REASON = "usb_present"


def _ensure_almemo_guard_step() -> None:
    global _ALMEMO_PRESENT_SINCE, _ALMEMO_LAST_ACTION, _ALMEMO_LAST_REASON

    if not ALMEMO_GUARD_ENABLED:
        return
    if _mark_manual_pause_wait("almemo", "_ALMEMO_LAST_ACTION", "_ALMEMO_LAST_REASON"):
        return

    now = time.time()
    present = _almemo_usb_present()
    if not present:
        _ALMEMO_PRESENT_SINCE = None
        if KEEP_DEGRADED_SERVICES_RUNNING:
            if _container_status("almemo-collector") != "running":
                allowed, wait_for = _degraded_start_allowed("almemo-collector", now)
                if allowed:
                    _start_almemo("usb_missing_degraded_mode")
                else:
                    with STATE_LOCK:
                        _ALMEMO_LAST_ACTION = "wait"
                        _ALMEMO_LAST_REASON = f"usb_missing_degraded_retry:{wait_for}s"
            else:
                with STATE_LOCK:
                    _ALMEMO_LAST_ACTION = "noop"
                    _ALMEMO_LAST_REASON = "usb_missing_degraded_mode"
            return
        if _container_status("almemo-collector") == "running":
            _stop_almemo("usb_missing")
        else:
            with STATE_LOCK:
                _ALMEMO_LAST_ACTION = "noop"
                _ALMEMO_LAST_REASON = "usb_missing"
        return

    if _ALMEMO_PRESENT_SINCE is None:
        _ALMEMO_PRESENT_SINCE = now

    stable_for = now - _ALMEMO_PRESENT_SINCE
    if stable_for < ALMEMO_USB_STABLE_SEC:
        with STATE_LOCK:
            _ALMEMO_LAST_ACTION = "wait"
            _ALMEMO_LAST_REASON = f"usb_settling:{round(stable_for,1)}s"
        return

    status = _container_status("almemo-collector")
    if status != "running":
        _start_almemo("usb_stable")
        return

    with STATE_LOCK:
        _ALMEMO_LAST_ACTION = "noop"
        _ALMEMO_LAST_REASON = "usb_present"


def _mscl_guard_loop() -> None:
    _log(
        "mscl guard enabled "
        f"interval={MSCL_GUARD_INTERVAL_SEC}s stable={MSCL_SERIAL_STABLE_SEC}s "
        f"configured_port={os.getenv('MSCL_PORT', '').strip() or '<unset>'}"
    )
    while True:
        try:
            with ACTION_LOCK:
                _ensure_mscl_guard_step()
        except Exception as exc:
            _log(f"mscl_guard_error error={exc}")
        time.sleep(max(1.0, MSCL_GUARD_INTERVAL_SEC))


def _redlab_guard_loop() -> None:
    _log(
        "redlab guard enabled "
        f"vendor={REDLAB_USB_VENDOR_ID} product={REDLAB_USB_PRODUCT_ID} "
        f"interval={REDLAB_GUARD_INTERVAL_SEC}s stable={REDLAB_USB_STABLE_SEC}s"
    )
    while True:
        try:
            with ACTION_LOCK:
                _ensure_redlab_guard_step()
        except Exception as exc:
            _log(f"redlab_guard_error error={exc}")
        time.sleep(max(1.0, REDLAB_GUARD_INTERVAL_SEC))


def _almemo_guard_loop() -> None:
    _log(
        "almemo guard enabled "
        f"vendor={ALMEMO_USB_VENDOR_ID} product={ALMEMO_USB_PRODUCT_ID} "
        f"interval={ALMEMO_GUARD_INTERVAL_SEC}s stable={ALMEMO_USB_STABLE_SEC}s"
    )
    while True:
        try:
            with ACTION_LOCK:
                _ensure_almemo_guard_step()
        except Exception as exc:
            _log(f"almemo_guard_error error={exc}")
        time.sleep(max(1.0, ALMEMO_GUARD_INTERVAL_SEC))


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        status="ok",
        targets=sorted(TARGET_MAP.keys()),
        manual_pauses=_manual_pause_snapshot(),
        serial_devices_by_id=serial_devices_by_id(),
        usb_devices=usb_devices(),
    )


@app.route("/host-metrics", methods=["GET"])
def host_metrics():
    return jsonify(
        status="ok",
        cpu_percent=_cpu_percent(),
        memory=memory_stats(),
    )


@app.route("/services", methods=["GET"])
def services():
    return jsonify(
        success=True,
        services=status_payload(DOCKER_CLIENT, TARGET_MAP),
        manual_pauses=_manual_pause_snapshot(),
        serial_devices_by_id=serial_devices_by_id(),
        usb_devices=usb_devices(),
    )


@app.route("/services/redlab/health", methods=["GET"])
def redlab_health():
    return jsonify(success=True, redlab=_redlab_guard_state(), services=status_payload(DOCKER_CLIENT, TARGET_MAP).get("redlab", {}))


@app.route("/services/mscl/health", methods=["GET"])
def mscl_health():
    return jsonify(success=True, mscl=_mscl_guard_state(), services=status_payload(DOCKER_CLIENT, TARGET_MAP).get("mscl", {}))


@app.route("/services/almemo/health", methods=["GET"])
def almemo_health():
    return jsonify(success=True, almemo=_almemo_guard_state(), services=status_payload(DOCKER_CLIENT, TARGET_MAP).get("almemo", {}))


@app.route("/services/<target>/<action>", methods=["POST"])
def service_action(target: str, action: str):
    if target not in TARGET_MAP:
        return jsonify(error="unknown target"), 404
    if action not in {"start", "stop", "restart"}:
        return jsonify(error="unknown action"), 400

    names = TARGET_MAP[target]
    try:
        with ACTION_LOCK:
            if action == "stop":
                _manual_pause_target(target)
            else:
                _manual_resume_target(target)

            if action == "start":
                done = _start_targets(names)
            elif action == "stop":
                done = _stop_targets(names)
            else:
                done = _stop_targets(names) + _start_targets(names)
        return jsonify(
            success=True,
            target=target,
            action=action,
            steps=done,
            manual_pauses=_manual_pause_snapshot(),
            services=status_payload(DOCKER_CLIENT, TARGET_MAP),
        )
    except docker.errors.NotFound as exc:
        return jsonify(error=f"container not found: {exc}"), 404
    except Exception as exc:
        return jsonify(error=str(exc)), 500


if __name__ == "__main__":
    _load_manual_pauses()
    if MSCL_GUARD_ENABLED:
        threading.Thread(target=_mscl_guard_loop, name="svcctl-mscl-guard", daemon=True).start()
    if REDLAB_GUARD_ENABLED:
        threading.Thread(target=_redlab_guard_loop, name="svcctl-redlab-guard", daemon=True).start()
    if ALMEMO_GUARD_ENABLED:
        threading.Thread(target=_almemo_guard_loop, name="svcctl-almemo-guard", daemon=True).start()
    app.run(host="0.0.0.0", port=SVCCTL_PORT)
