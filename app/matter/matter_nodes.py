from __future__ import annotations

import time
from datetime import datetime, timezone
import json
from typing import Any

try:
    from websocket import create_connection  # type: ignore
except ImportError:  # pragma: no cover - test env can run without websocket-client
    create_connection = None


AIR_REBOOT_PRODUCTS = {
    "ESP32-C3-SuperMini",
    "ESP32-C6-DevKitC",
    "ESP32-C6-Pico",
    "ESP32-C6-Zero Multinode",
}


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text)
    except ValueError:
        return None


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _normalize_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _lookup_any(mapping: dict[str, Any], *keys: str) -> Any:
    normalized = {_normalize_key(key): value for key, value in mapping.items()}
    for key in keys:
        token = _normalize_key(key)
        if token in normalized:
            return normalized[token]
    return None


def _normalize_hex(value: Any, width: int | None = None) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        text = format(value, "x")
    else:
        text = str(value).strip().lower()
        if text.startswith("0x"):
            text = text[2:]
    text = "".join(ch for ch in text if ch in "0123456789abcdef")
    if not text:
        return None
    if width is not None:
        text = text.zfill(width)[-width:]
    return text


def _normalize_ext_address(value: Any) -> str | None:
    return _normalize_hex(value, width=16)


def _normalize_rloc16(value: Any) -> str | None:
    text = _normalize_hex(value, width=4)
    return f"0x{text}" if text else None


def _thread_role_label(value: Any) -> str | None:
    labels = {
        0: "unspecified",
        1: "unassigned",
        2: "sleepy-child",
        3: "child",
        4: "reed",
        5: "router",
        6: "leader",
    }
    try:
        return labels.get(int(value))
    except (TypeError, ValueError):
        return None


def _network_type_label(feature_map: Any) -> str | None:
    try:
        flags = int(feature_map)
    except (TypeError, ValueError):
        return None
    if flags & 0x02:
        return "Thread"
    if flags & 0x01:
        return "WiFi"
    if flags & 0x04:
        return "Ethernet"
    return None


def _normalize_neighbor_entry(entry: dict[str, Any]) -> dict[str, Any]:
    role = _lookup_any(entry, "role", "neighbor_role")
    if role is None:
        is_child = _coerce_bool(_lookup_any(entry, "is_child", "ischild"))
        if is_child is True:
            role = "child"
    age = _coerce_int(_lookup_any(entry, "age", "ages", "agesec", "agesecs", "1"))
    lqi = _coerce_int(_lookup_any(entry, "link_quality_in", "linkqualityin", "lqiin", "lqi", "5"))
    return {
        "ext_address": _normalize_ext_address(_lookup_any(entry, "ext_address", "extaddress", "extendedmac", "0")),
        "rloc16": _normalize_rloc16(_lookup_any(entry, "rloc16", "rloc", "2")),
        "role": str(role).strip().lower() if role is not None else None,
        "is_child": _coerce_bool(_lookup_any(entry, "is_child", "ischild", "13")),
        "full_thread_device": _coerce_bool(_lookup_any(entry, "full_thread_device", "fullthreaddevice", "ftd", "11")),
        "rx_on_when_idle": _coerce_bool(_lookup_any(entry, "rx_on_when_idle", "rxonwhenidle", "10")),
        "full_network_data": _coerce_bool(_lookup_any(entry, "full_network_data", "fullnetworkdata", "12")),
        "link_quality_in": lqi,
        "link_quality_out": _coerce_int(_lookup_any(entry, "link_quality_out", "linkqualityout", "lqiout")),
        "average_rssi_dbm": _coerce_int(_lookup_any(entry, "average_rssi", "averagerssi", "avgrssi", "6")),
        "last_rssi_dbm": _coerce_int(_lookup_any(entry, "last_rssi", "lastrssi", "7")),
        "frame_error_rate": _coerce_int(_lookup_any(entry, "frame_error_rate", "frameerrorrate", "8")),
        "message_error_rate": _coerce_int(_lookup_any(entry, "message_error_rate", "messageerrorrate", "9")),
        "age_sec": age,
    }


def _normalize_route_entry(entry: dict[str, Any]) -> dict[str, Any]:
    age = _coerce_int(_lookup_any(entry, "age", "agesec", "7"))
    return {
        "ext_address": _normalize_ext_address(_lookup_any(entry, "ext_address", "extaddress", "extendedmac", "0")),
        "rloc16": _normalize_rloc16(_lookup_any(entry, "rloc16", "rloc", "1")),
        "router_id": _coerce_int(_lookup_any(entry, "router_id", "routerid", "2")),
        "next_hop": _coerce_int(_lookup_any(entry, "next_hop", "nexthop", "3")),
        "path_cost": _coerce_int(_lookup_any(entry, "path_cost", "pathcost", "4")),
        "link_quality_in": _coerce_int(_lookup_any(entry, "link_quality_in", "linkqualityin", "lqiin", "5")),
        "link_quality_out": _coerce_int(_lookup_any(entry, "link_quality_out", "linkqualityout", "lqiout", "6")),
        "age_sec": age,
    }


def _normalize_table(value: Any, normalizer: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = normalizer(item)
        if any(v is not None for v in row.values()):
            rows.append(row)
    return rows


def _coerce_int_list(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    values: set[int] = set()
    for item in value:
        coerced = _coerce_int(item)
        if coerced is not None:
            values.add(coerced)
    return values


def _standard_controls(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    endpoints = sorted(
        {
            endpoint
            for key in attrs
            if isinstance(key, str) and key.count("/") == 2
            for endpoint in [key.split("/", 1)[0]]
            if _coerce_int(endpoint) is not None
        },
        key=lambda value: int(value),
    )
    for endpoint in endpoints:
        endpoint_id = int(endpoint)
        onoff_commands = _coerce_int_list(attrs.get(f"{endpoint}/6/65529"))
        onoff: list[str] = []
        if 0 in onoff_commands:
            onoff.append("Off")
        if 1 in onoff_commands:
            onoff.append("On")
        if 2 in onoff_commands:
            onoff.append("Toggle")
        if onoff:
            controls.append(
                {
                    "endpoint_id": endpoint_id,
                    "cluster_id": 6,
                    "cluster_name": "OnOff",
                    "commands": onoff,
                    "on": _coerce_bool(attrs.get(f"{endpoint}/6/0")),
                }
            )

        identify_commands = _coerce_int_list(attrs.get(f"{endpoint}/3/65529"))
        if 0 in identify_commands:
            controls.append(
                {
                    "endpoint_id": endpoint_id,
                    "cluster_id": 3,
                    "cluster_name": "Identify",
                    "commands": ["Identify"],
                }
            )
    return controls


def _air_reboot_supported(attrs: dict[str, Any]) -> bool:
    product_name = str(attrs.get("0/40/3") or "").strip()
    accepted_general_diag = _coerce_int_list(attrs.get("0/51/65529"))
    test_event_enabled = _coerce_bool(attrs.get("0/51/8"))
    return (
        product_name in AIR_REBOOT_PRODUCTS
        and 0 in accepted_general_diag
        and test_event_enabled is True
    )


def _boot_reason_label(value: Any) -> str | None:
    labels = {
        0: "unspecified",
        1: "power-on",
        2: "brown-out",
        3: "software-watchdog",
        4: "hardware-watchdog",
        5: "software-update",
        6: "software-reset",
    }
    boot_reason = _coerce_int(value)
    if boot_reason is None:
        return None
    return labels.get(boot_reason, f"unknown-{boot_reason}")


def _iso_utc(epoch_sec: float) -> str:
    return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_diagnostics(attrs: dict[str, Any], observed_at: float | None = None) -> dict[str, Any]:
    uptime_sec = _coerce_int(attrs.get("0/51/2"))
    diagnostics = {
        "reboot_count": _coerce_int(attrs.get("0/51/1")),
        "uptime_sec": uptime_sec,
        "total_operational_hours": _coerce_int(attrs.get("0/51/3")),
        "boot_reason": _coerce_int(attrs.get("0/51/4")),
        "boot_reason_label": _boot_reason_label(attrs.get("0/51/4")),
        "diagnostics_observed_at": _iso_utc(observed_at) if observed_at is not None else None,
        "estimated_last_boot_at": None,
    }
    if uptime_sec is not None and observed_at is not None and uptime_sec >= 0:
        diagnostics["estimated_last_boot_at"] = _iso_utc(observed_at - uptime_sec)
    return diagnostics


def normalize_matter_node_payload(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    nodes: list[dict[str, Any]] = []
    observed_at = time.time()
    for item in items:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes")
        if not isinstance(attrs, dict):
            attrs = {}
        thread_role = _thread_role_label(attrs.get("0/53/1"))
        neighbors = _normalize_table(attrs.get("0/53/7"), _normalize_neighbor_entry)
        routes = _normalize_table(attrs.get("0/53/8"), _normalize_route_entry)
        nodes.append(
            {
                "node_id": item.get("node_id"),
                "available": bool(item.get("available")),
                "last_interview": item.get("last_interview"),
                "vendor_name": attrs.get("0/40/1"),
                "product_name": attrs.get("0/40/3"),
                "serial_number": attrs.get("0/40/15"),
                "software_version": attrs.get("0/40/9"),
                "software_version_string": attrs.get("0/40/10"),
                "network_type": _network_type_label(attrs.get("0/49/65532")),
                "standard_controls": _standard_controls(attrs),
                "air_reboot_supported": _air_reboot_supported(attrs),
                **_runtime_diagnostics(attrs, observed_at),
                "thread_role": thread_role,
                "channel": _coerce_int(attrs.get("0/53/0")),
                "ext_address": _normalize_ext_address(attrs.get("0/53/63")),
                "rloc16": _normalize_rloc16(attrs.get("0/53/64")),
                "thread_diagnostics": {
                    "neighbor_table": neighbors,
                    "route_table": routes,
                    "neighbor_count": len(neighbors),
                    "route_count": len(routes),
                },
            }
        )
    return nodes


def _overlay_runtime_diagnostics(node: dict[str, Any], attrs: dict[str, Any], observed_at: float) -> dict[str, Any]:
    merged = dict(node)
    merged.update({key: value for key, value in _runtime_diagnostics(attrs, observed_at).items() if value is not None})
    return merged


def _overlay_thread_attributes(node: dict[str, Any], attrs: dict[str, Any]) -> dict[str, Any]:
    merged = dict(node)
    diagnostics = dict(merged.get("thread_diagnostics") or {})

    ext_address = _normalize_ext_address(attrs.get("0/53/63"))
    rloc16 = _normalize_rloc16(attrs.get("0/53/64"))
    neighbors = _normalize_table(attrs.get("0/53/7"), _normalize_neighbor_entry)
    routes = _normalize_table(attrs.get("0/53/8"), _normalize_route_entry)

    if ext_address:
        merged["ext_address"] = ext_address
    if rloc16:
        merged["rloc16"] = rloc16
    if neighbors:
        diagnostics["neighbor_table"] = neighbors
        diagnostics["neighbor_count"] = len(neighbors)
    if routes:
        diagnostics["route_table"] = routes
        diagnostics["route_count"] = len(routes)
    merged["thread_diagnostics"] = diagnostics
    return merged


def _refresh_runtime_diagnostics(ws: Any, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    message_id = 1000
    for node in nodes:
        merged = dict(node)
        node_id = node.get("node_id")
        if not isinstance(node_id, int) or not node.get("available"):
            refreshed.append(merged)
            continue
        attrs: dict[str, Any] = {}
        observed_at = time.time()
        for attribute_path in ("0/51/1", "0/51/2", "0/51/3", "0/51/4"):
            try:
                value = _read_attribute_result(ws, message_id, node_id, attribute_path)
                message_id += 1
            except Exception:
                continue
            if value is not None:
                attrs[attribute_path] = value
        if attrs:
            merged = _overlay_runtime_diagnostics(merged, attrs, observed_at)
        refreshed.append(merged)
    return refreshed


def _read_attribute_result(ws: Any, message_id: int, node_id: int, attribute_path: str) -> Any | None:
    expected_message_id = str(message_id)
    ws.send(
        json.dumps(
            {
                "message_id": str(message_id),
                "command": "read_attribute",
                "args": {
                    "node_id": node_id,
                    "attribute_path": attribute_path,
                },
            }
        )
    )
    for _ in range(50):
        raw = ws.recv()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("message_id") or "") != expected_message_id:
            continue
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return result.get(attribute_path)
    return None


def _refresh_thread_snapshots(ws: Any, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refreshed: list[dict[str, Any]] = []
    message_id = 2
    for node in nodes:
        merged = dict(node)
        if str(node.get("network_type") or "").strip().lower() != "thread":
            refreshed.append(merged)
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, int):
            refreshed.append(merged)
            continue
        attrs: dict[str, Any] = {}
        for attribute_path in ("0/53/63", "0/53/64", "0/53/7", "0/53/8"):
            try:
                value = _read_attribute_result(ws, message_id, node_id, attribute_path)
                message_id += 1
            except Exception:
                continue
            if value is not None:
                attrs[attribute_path] = value
        if attrs:
            merged = _overlay_thread_attributes(merged, attrs)
        refreshed.append(merged)
    return refreshed


def fetch_matter_node_snapshot(ws_url: str, timeout: float = 8.0) -> list[dict[str, Any]]:
    ws = None
    try:
        if create_connection is None:
            return []
        ws = create_connection(ws_url, timeout=timeout)
        ws.recv()
        ws.send(
            json.dumps(
                {
                    "message_id": "1",
                    "command": "start_listening",
                    "args": {},
                }
            )
        )
        raw = ws.recv()
        payload = json.loads(raw)
        result = payload.get("result") if isinstance(payload, dict) else None
        nodes = normalize_matter_node_payload(result)
        nodes = _refresh_runtime_diagnostics(ws, nodes)
        return _refresh_thread_snapshots(ws, nodes)
    except Exception:
        return []
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
