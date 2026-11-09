import json
import time
from typing import Any, Callable

from websocket import create_connection  # type: ignore


class MatterCommandClient:
    def __init__(
        self,
        server_url: str,
        connection_factory: Callable[..., Any] = create_connection,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._server_url = server_url
        self._connection_factory = connection_factory
        self._time_fn = time_fn

    def read_attribute(self, node_id: int, attribute_path: str, timeout_sec: float = 8.0) -> Any | None:
        try:
            payload = self.request(
                "read_attribute",
                {"node_id": node_id, "attribute_path": attribute_path},
                timeout_sec=timeout_sec,
                message_id="1",
            )
        except Exception:
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return result.get(attribute_path)

    def request(
        self,
        command: str,
        args: dict[str, Any],
        timeout_sec: float = 8.0,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = message_id or str(int(self._time_fn() * 1000))
        ws = None
        try:
            ws = self._connection_factory(self._server_url, timeout=timeout_sec)
            ws.recv()
            ws.send(json.dumps({"message_id": request_id, "command": command, "args": args}))
            deadline = self._time_fn() + timeout_sec
            while self._time_fn() < deadline:
                payload = json.loads(ws.recv())
                if not isinstance(payload, dict) or str(payload.get("message_id") or "") != request_id:
                    continue
                return payload
            return {"error_code": 408, "details": "matter command timed out"}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass


def supports_standard_command(
    nodes: list[dict[str, Any]],
    node_id: int,
    endpoint_id: int,
    cluster_id: int,
    command_name: str,
) -> bool:
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


def supports_air_reboot(nodes: list[dict[str, Any]], node_id: int) -> bool:
    for node in nodes:
        if node.get("node_id") != node_id:
            continue
        if not node.get("available"):
            return False
        return bool(node.get("air_reboot_supported"))
    return False
