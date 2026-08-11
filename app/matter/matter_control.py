import threading
from typing import Any

try:
    from .matter_docker import container_status, create_client, set_container_running
except ImportError:
    from matter_docker import container_status, create_client, set_container_running


CONTROL_TARGETS: dict[str, list[str]] = {
    "openthread": ["openthread-border-router"],
    "matter-server": ["matter-server"],
}

_docker_client: Any | None = None
_docker_client_lock = threading.Lock()


def get_docker_client() -> Any:
    global _docker_client
    if _docker_client is not None:
        return _docker_client
    with _docker_client_lock:
        if _docker_client is None:
            _docker_client = create_client()
    return _docker_client


def reset_docker_client() -> None:
    global _docker_client
    with _docker_client_lock:
        _docker_client = None


def control_target_payload(target: str) -> dict[str, Any]:
    client = get_docker_client()
    services = {name: container_status(client, name) for name in CONTROL_TARGETS.get(target, [])}
    return {
        "all_running": bool(services) and all(state == "running" for state in services.values()),
        "services": services,
    }


def control_target_action(target: str, action: str) -> dict[str, Any]:
    if target not in CONTROL_TARGETS:
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
        client = get_docker_client()
        for service_name in CONTROL_TARGETS[target]:
            if action == "restart":
                actions.append(set_container_running(client, service_name, False))
                actions.append(set_container_running(client, service_name, True))
            else:
                actions.append(set_container_running(client, service_name, action == "start"))
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    state = control_target_payload(target)
    return {"success": True, "target": target, "action": action, "actions": actions, **state}
