import math
import re
from typing import Any, Dict, Iterable, Tuple


_NUMERIC_STRING_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_ATTR_PATH_RE = re.compile(r"^(\d+)/(\d+)/(\d+)$")
_EVENT_TYPE_KEYS = {"event_type", "type", "message_type", "event", "command"}
_TAG_KEY_ALIASES = {
    "node_id": {"node_id", "nodeid", "node"},
    "endpoint_id": {"endpoint_id", "endpointid", "endpoint"},
    "cluster_id": {"cluster_id", "clusterid", "cluster"},
    "attribute_id": {"attribute_id", "attributeid", "attribute"},
    "device_id": {"device_id", "deviceid", "device"},
}


def _normalize_key(raw: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw).strip())
    key = re.sub(r"_+", "_", key).strip("_").lower()
    return key or "value"


def _path_matches(path: str, key: str) -> bool:
    return path == key or path.endswith(f"_{key}")


def _coerce_numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or not _NUMERIC_STRING_RE.match(text):
            return None
        try:
            numeric = float(text)
        except ValueError:
            return None
        if math.isfinite(numeric):
            return numeric
    return None


def _walk_scalars(payload: Any, prefix: str = "", depth: int = 0, max_depth: int = 6) -> Iterable[Tuple[str, Any]]:
    if depth > max_depth:
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_part = _normalize_key(key)
            next_prefix = f"{prefix}_{key_part}" if prefix else key_part
            yield from _walk_scalars(value, next_prefix, depth + 1, max_depth=max_depth)
        return
    if isinstance(payload, list):
        for idx, value in enumerate(payload):
            next_prefix = f"{prefix}_{idx}" if prefix else f"item_{idx}"
            yield from _walk_scalars(value, next_prefix, depth + 1, max_depth=max_depth)
        return
    if prefix:
        yield prefix, payload


_ATTR_UPDATED_HANDLED = object()  # sentinel: format recognized but no numeric data


def _try_attribute_updated(payload: dict) -> Dict[str, Any] | None | object:
    # python-matter-server sends attribute_updated as:
    # {"event": "attribute_updated", "data": [node_id, "endpoint/cluster/attribute", value]}
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 3:
        return None  # unknown format, fall through to generic walk
    node_id, attr_path, raw_value = data
    if not isinstance(node_id, int) or not isinstance(attr_path, str):
        return None
    m = _ATTR_PATH_RE.match(attr_path.strip())
    if not m:
        return None

    # Format recognized — do not fall through regardless of value type.
    endpoint_id, cluster_id, attribute_id = m.groups()
    tags = {
        "node_id": str(node_id),
        "endpoint_id": endpoint_id,
        "cluster_id": cluster_id,
        "attribute_id": attribute_id,
    }

    # Boolean → 0/1 (On/Off Light, Contact Sensor states)
    if isinstance(raw_value, bool):
        return {"event_type": "attribute_updated", "tags": tags, "fields": {"value": 1.0 if raw_value else 0.0}}

    numeric = _coerce_numeric(raw_value)
    if numeric is not None:
        return {"event_type": "attribute_updated", "tags": tags, "fields": {"value": numeric}}

    # Struct/list value — walk it and collect all numeric leaves
    fields: Dict[str, float] = {}
    for path, value in _walk_scalars(raw_value):
        if isinstance(value, bool):
            fields[path] = 1.0 if value else 0.0
        else:
            n = _coerce_numeric(value)
            if n is not None:
                fields[path] = n
        if len(fields) >= 64:
            break

    if not fields:
        return _ATTR_UPDATED_HANDLED  # recognized but no numeric data

    return {"event_type": "attribute_updated", "tags": tags, "fields": fields}


def extract_event(payload: Any, max_fields: int = 64) -> Dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("event_type") or payload.get("event") or ""
    if isinstance(event_type, str) and event_type.strip() == "attribute_updated":
        result = _try_attribute_updated(payload)
        if result is _ATTR_UPDATED_HANDLED:
            return None
        if result is not None:
            return result  # type: ignore[return-value]

    fields: Dict[str, float] = {}
    tags: Dict[str, str] = {}
    event_type = "unknown"

    for path, value in _walk_scalars(payload):
        if isinstance(value, str) and event_type == "unknown" and any(_path_matches(path, key) for key in _EVENT_TYPE_KEYS):
            event_type = value.strip() or "unknown"

        for tag_name, aliases in _TAG_KEY_ALIASES.items():
            if tag_name not in tags and isinstance(value, (str, int)) and any(_path_matches(path, alias) for alias in aliases):
                text = str(value).strip()
                if text:
                    tags[tag_name] = text

        numeric = _coerce_numeric(value)
        if numeric is None:
            continue
        if len(fields) >= max_fields:
            break
        field_name = path
        if field_name in fields:
            field_name = f"{field_name}_{len(fields)}"
        fields[field_name] = numeric

    if not fields:
        return None

    return {
        "event_type": event_type,
        "tags": tags,
        "fields": fields,
    }
