from typing import Any, Mapping, Optional


EXPORT_STORAGE_TRANSIENT_HINT = (
    "Node datalog session info is unstable (MSCL read error). "
    "Try: stop sampling -> set Idle -> wait 5-10s -> retry export."
)

EXPORT_STORAGE_TRANSIENT_MARKERS = (
    "Failed to download data from the Node",
    "Failed to get the Datalog Session Info",
    "Failed to get the Datalogging Session Info",
    "EEPROM",
)


def parse_raw_node_id(raw_node_id: Any) -> Optional[int]:
    if isinstance(raw_node_id, bool):
        return None
    if isinstance(raw_node_id, int):
        return int(raw_node_id)
    if isinstance(raw_node_id, str):
        s = raw_node_id.strip()
        if s.isdigit():
            return int(s)
    return None


def cached_node_snapshot(raw_node_id: Any, node_read_cache: Mapping[int, dict[str, Any]]) -> dict[str, Any]:
    node_id = parse_raw_node_id(raw_node_id)
    if node_id is None:
        return {}
    cached = node_read_cache.get(int(node_id), {})
    return dict(cached) if isinstance(cached, dict) else {}


def map_export_storage_error(err_text: str) -> tuple[int, str]:
    message = str(err_text)
    if any(marker in message for marker in EXPORT_STORAGE_TRANSIENT_MARKERS):
        return 409, EXPORT_STORAGE_TRANSIENT_HINT
    return 500, message


__all__ = [
    "EXPORT_STORAGE_TRANSIENT_HINT",
    "cached_node_snapshot",
    "map_export_storage_error",
    "parse_raw_node_id",
]
