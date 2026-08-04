from typing import Any

try:
    import docker
except ImportError:
    docker = None  # type: ignore[assignment]


def _docker_not_found_error():
    return getattr(getattr(docker, "errors", None), "NotFound", Exception)


def container_status(client: Any, name: str) -> str:
    try:
        container = client.containers.get(name)
        container.reload()
        return container.status
    except _docker_not_found_error():
        return "missing"
    except Exception:
        return "error"


def get_container(client: Any, name: str):
    return client.containers.get(name)


def set_container_running(
    client: Any,
    name: str,
    should_run: bool,
    timeout: int = 15,
) -> str:
    try:
        container = get_container(client, name)
    except _docker_not_found_error():
        if should_run:
            return f"missing:{name}:compose_up_required"
        return f"noop:{name}:missing"
    container.reload()
    if should_run:
        if container.status != "running":
            container.start()
            return f"start:{name}"
        return f"noop:{name}"
    if container.status == "running":
        container.stop(timeout=timeout)
        return f"stop:{name}"
    return f"noop:{name}"
