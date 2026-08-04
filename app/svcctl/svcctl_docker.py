from datetime import datetime

import docker


def container_status(client: docker.DockerClient, name: str) -> str:
    try:
        container = client.containers.get(name)
        container.reload()
        return container.status
    except docker.errors.NotFound:
        return "missing"
    except Exception:
        return "error"


def get_container(client: docker.DockerClient, name: str):
    return client.containers.get(name)


def status_payload(client: docker.DockerClient, target_map: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    return {
        target: {name: container_status(client, name) for name in names}
        for target, names in target_map.items()
    }


def read_since_logs(client: docker.DockerClient, name: str, since_ts: int) -> str:
    container = get_container(client, name)
    raw = container.logs(stdout=True, stderr=True, since=since_ts)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def started_at_epoch(client: docker.DockerClient, name: str) -> float | None:
    try:
        container = get_container(client, name)
        container.reload()
        started = container.attrs.get("State", {}).get("StartedAt", "")
        if not started or started.startswith("0001-01-01"):
            return None
        return datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def set_container_running(
    client: docker.DockerClient,
    name: str,
    should_run: bool,
    timeout: int = 15,
) -> str:
    try:
        container = get_container(client, name)
    except docker.errors.NotFound:
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
