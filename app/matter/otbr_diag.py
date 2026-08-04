import os
import re
import time
from pathlib import PurePosixPath
from typing import Any

try:
    import docker
except ImportError:
    docker = None  # type: ignore[assignment]

try:
    from .matter_docker import container_status, get_container
    from .matter_host import serial_devices_by_id, usb_devices
except ImportError:
    from matter_docker import container_status, get_container
    from matter_host import serial_devices_by_id, usb_devices

OTBR_CONTAINER_NAME = "openthread-border-router"
OTBR_DIAG_MAX_TEXT = 12000


def primary_value(output_text: str) -> str | None:
    lines = [line.strip() for line in output_text.splitlines() if line.strip()]
    meaningful = [line for line in lines if line.lower() != "done"]
    if not meaningful:
        return None
    return meaningful[0]


def parse_otctl_table(text: str) -> dict:
    headers: list[str] = []
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("+") or line.lower() == "done":
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not headers:
                headers = cells
            else:
                rows.append(cells)
    return {"headers": headers, "rows": rows}


def parse_meshdiag_topology_children(text: str) -> dict:
    routers: list[dict] = []
    current: dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.lower() == "done":
            continue

        router_match = re.search(
            r"id:(?P<id>\d+)\s+rloc16:(?P<rloc16>0x[0-9a-fA-F]+)\s+ext-addr:(?P<ext>[0-9a-fA-F]+)",
            line,
        )
        if router_match:
            flags = [part.strip() for part in line[router_match.end():].split("-") if part.strip() and not part.strip().startswith("ver:")]
            current = {
                "id": int(router_match.group("id")),
                "rloc16": router_match.group("rloc16").lower(),
                "ext_address": router_match.group("ext").lower(),
                "flags": flags,
                "links": [],
                "children": [],
            }
            routers.append(current)
            continue

        if current is None:
            continue

        links_match = re.search(r"(\d+)-links:\{\s*(?P<ids>[^}]*)\}", line)
        if links_match:
            current["links"] = [int(value) for value in re.findall(r"\d+", links_match.group("ids"))]
            continue

        child_match = re.search(
            r"rloc16:(?P<rloc16>0x[0-9a-fA-F]+)\s+lq:(?P<lq>-?\d+),\s+mode:(?P<mode>\S+)",
            line,
        )
        if child_match:
            current["children"].append(
                {
                    "rloc16": child_match.group("rloc16").lower(),
                    "link_quality_in": int(child_match.group("lq")),
                    "mode": child_match.group("mode"),
                }
            )

    return {"routers": routers}


def extract_signal_rows(table_text: str) -> list[dict]:
    rows: list[dict] = []
    for raw_line in table_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("done") or "rloc16" in lower or set(line) <= {"|", "-", "+", " "}:
            continue
        values = [int(match) for match in re.findall(r"-?\d+", line)]
        signal_dbm = [value for value in values if -130 <= value <= -1]
        if not signal_dbm:
            signal_dbm = [value for value in values if -130 <= value <= 20]
        if not signal_dbm:
            continue
        rows.append(
            {
                "line": line,
                "signal_dbm": signal_dbm,
                "signal_best_dbm": max(signal_dbm),
                "signal_worst_dbm": min(signal_dbm),
            }
        )
    return rows


def neighbor_signal_summary(table_text: str) -> dict:
    rows = extract_signal_rows(table_text)
    summary: dict = {
        "rows": rows,
        "node_count": len(rows),
        "best_dbm": None,
        "worst_dbm": None,
    }
    if rows:
        summary["best_dbm"] = max(row["signal_best_dbm"] for row in rows)
        summary["worst_dbm"] = min(row["signal_worst_dbm"] for row in rows)
    return summary


def serial_link_matches_device(serial_link: dict, device_path: str) -> bool:
    configured = str(device_path or "").strip()
    if not configured:
        return False

    candidates = {
        configured,
        PurePosixPath(configured).name,
    }
    for key in ("path", "target", "name"):
        value = str(serial_link.get(key, "")).strip()
        if not value:
            continue
        if value in candidates or PurePosixPath(value).name in candidates:
            return True
    return False


def dongle_score(device: dict) -> int:
    manufacturer = str(device.get("manufacturer", "")).lower()
    product = str(device.get("product", "")).lower()
    serial = str(device.get("serial", "")).lower()
    text = " ".join([manufacturer, product, serial])
    score = 0
    for token in ("sonoff", "zbdongle", "thread", "openthread", "silicon labs", "cp210", "efr"):
        if token in text:
            score += 2
    vendor = str(device.get("vendor_id", "")).lower()
    if vendor in {"10c4", "1a86", "0483"}:
        score += 1
    return score


def best_usb_dongle_guess(devices: list[dict], serial_matches: list[dict]) -> dict | None:
    if not devices or not serial_matches:
        return None
    serial_tokens: set[str] = set()
    serial_match_names = " ".join(str(item.get("name", "")).lower() for item in serial_matches)
    for item in serial_matches:
        name = str(item.get("name", ""))
        serial_tokens.update(token.lower() for token in re.findall(r"[A-Za-z0-9]{6,}", name))

    def score(device: dict) -> int:
        base = dongle_score(device)
        serial = str(device.get("serial", "")).strip().lower()
        manufacturer = str(device.get("manufacturer", "")).lower()
        product = str(device.get("product", "")).lower()
        if serial and serial in serial_tokens:
            base += 12
        if manufacturer and manufacturer in serial_match_names:
            base += 4
        if product and product in serial_match_names:
            base += 4
        return base

    ranked = sorted(devices, key=score, reverse=True)
    if score(ranked[0]) <= 0:
        return None
    return ranked[0]


def rcp_transport_info(rcp_device: str) -> dict[str, Any]:
    tcp_endpoint = os.getenv("OTBR_RCP_TCP_ENDPOINT", "").strip()
    if PurePosixPath(str(rcp_device or "")).name == "ttyOTBR":
        return {
            "kind": "network_rcp_bridge" if tcp_endpoint else "serial_pty",
            "label": "WLAN RCP bridge" if tcp_endpoint else "RCP bridge pty",
            "endpoint": tcp_endpoint or None,
        }
    return {
        "kind": "usb_serial",
        "label": "USB serial RCP",
        "endpoint": None,
    }


def truncate_text(value: str, max_chars: int = OTBR_DIAG_MAX_TEXT) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...<truncated>"


def decode_exec_output(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def otbr_agent_socket_error(output: str) -> bool:
    text = str(output or "").lower()
    return (
        "connect session failed" in text
        or "connection refused" in text
        or "connection reset by peer" in text
        or "no such file or directory" in text
    )


def _docker_not_found_error():
    return getattr(getattr(docker, "errors", None), "NotFound", Exception)


def otbr_restart_agent(container: Any) -> None:
    try:
        container.exec_run(["service", "otbr-agent", "restart"], stdout=True, stderr=True)
        time.sleep(2.0)
    except Exception:
        return


def otbr_exec_otctl(
    client: Any,
    args: list[str],
    retries: int = 2,
    restart_on_socket_error: bool = False,
) -> dict:
    try:
        container = get_container(client, OTBR_CONTAINER_NAME)
    except _docker_not_found_error():
        return {"ok": False, "exit_code": 127, "error": "container_not_found", "output": "", "command": " ".join(["ot-ctl"] + args)}
    except Exception as exc:
        return {"ok": False, "exit_code": 1, "error": f"container_error:{exc}", "output": "", "command": " ".join(["ot-ctl"] + args)}

    command = ["ot-ctl"] + args
    restart_attempted = False
    last_error: str | None = None
    last_result: dict[str, Any] | None = None

    for attempt in range(max(1, retries)):
        try:
            result = container.exec_run(command, stdout=True, stderr=True)
            output = decode_exec_output(getattr(result, "output", b""))
            exit_code = int(getattr(result, "exit_code", 1))
            last_result = {
                "ok": exit_code == 0,
                "exit_code": exit_code,
                "output": truncate_text(output.strip()),
                "command": " ".join(command),
            }
            if exit_code == 0:
                return last_result
            if restart_on_socket_error and otbr_agent_socket_error(output) and not restart_attempted:
                otbr_restart_agent(container)
                restart_attempted = True
                continue
            if otbr_agent_socket_error(output) and attempt + 1 < max(1, retries):
                time.sleep(0.5)
                continue
            return last_result
        except Exception as exc:
            last_error = f"exec_error:{exc}"
            if attempt + 1 < max(1, retries):
                time.sleep(0.5)
                continue

    if last_result is not None:
        return last_result
    return {"ok": False, "exit_code": 1, "error": last_error or "exec_error:unknown", "output": "", "command": " ".join(command)}


def otbr_diag_snapshot(client: Any) -> dict:
    container_state = container_status(client, OTBR_CONTAINER_NAME)
    rcp_device = "/dev/ttyACM0"
    rcp_baud = ""
    try:
        otbr_container = get_container(client, OTBR_CONTAINER_NAME)
    except Exception:
        otbr_container = None
    if otbr_container is not None:
        try:
            otbr_container.reload()
            env_items = otbr_container.attrs.get("Config", {}).get("Env", [])
            env_map = {
                str(item).split("=", 1)[0]: str(item).split("=", 1)[1]
                for item in env_items
                if isinstance(item, str) and "=" in item
            }
            radio_url = str(env_map.get("OT_RCP_DEVICE", "")).strip()
            if radio_url:
                match = re.match(r"^[^:]+://(?P<path>[^?]+)(?:\?uart-baudrate=(?P<baud>\d+))?$", radio_url)
                if match:
                    rcp_device = match.group("path") or rcp_device
                    if match.group("baud"):
                        rcp_baud = match.group("baud")
        except Exception:
            pass
    serial_links = serial_devices_by_id()
    usb_list = usb_devices()
    matched_serial = [item for item in serial_links if serial_link_matches_device(item, rcp_device)]
    usb_guess = best_usb_dongle_guess(usb_list, matched_serial)
    transport = rcp_transport_info(rcp_device)
    payload: dict[str, Any] = {
        "container_status": container_state,
        "service_state": {OTBR_CONTAINER_NAME: container_state},
        "available": container_state == "running",
        "dongle": {
            "rcp_device": rcp_device,
            "rcp_baud": rcp_baud or None,
            "transport": transport,
            "serial_matches": matched_serial,
            "usb_guess": usb_guess,
            "usb_devices": usb_list,
        },
        "settings": {},
        "tables": {},
        "errors": [],
        "commands": {},
    }
    if container_state != "running":
        payload["errors"].append("openthread_border_router_not_running")
        return payload

    settings_commands = {
        "state": ["state"],
        "network_name": ["networkname"],
        "channel": ["channel"],
        "panid": ["panid"],
        "partitionid": ["partitionid"],
        "rloc16": ["rloc16"],
        "extaddr": ["extaddr"],
        "version": ["version"],
    }
    for key, args in settings_commands.items():
        result = otbr_exec_otctl(client, args, restart_on_socket_error=True)
        payload["commands"][key] = result
        if result.get("ok"):
            payload["settings"][key] = primary_value(result.get("output") or "")
        else:
            payload["settings"][key] = None
            payload["errors"].append(f"{key}_failed")

    state_value = str(payload["settings"].get("state") or "").strip().lower()
    if state_value in {"", "disabled", "detached"}:
        payload["tables"]["neighbor_signal"] = neighbor_signal_summary("")
        payload["topology_pending"] = True
        return payload

    table_commands = {
        "neighbor_table": ["neighbor", "table"],
        "router_table": ["router", "table"],
        "child_table": ["child", "table"],
    }
    for key, args in table_commands.items():
        result = otbr_exec_otctl(client, args, restart_on_socket_error=True)
        payload["commands"][key] = result
        table_text = result.get("output", "")
        payload["tables"][key] = {
            "ok": bool(result.get("ok")),
            "raw": table_text,
            "parsed": parse_otctl_table(table_text),
            "line_count": len([line for line in table_text.splitlines() if line.strip()]),
        }
        if not result.get("ok"):
            payload["errors"].append(f"{key}_failed")

    result = otbr_exec_otctl(client, ["meshdiag", "topology", "children"], restart_on_socket_error=True)
    raw = result.get("output", "")
    payload["commands"]["meshdiag_topology_children"] = result
    payload["meshdiag"] = {
        "topology_children": {
            "ok": bool(result.get("ok")),
            "raw": raw,
            "parsed": parse_meshdiag_topology_children(raw),
            "line_count": len([line for line in raw.splitlines() if line.strip()]),
        }
    }
    if not result.get("ok"):
        payload["errors"].append("meshdiag_topology_children_failed")

    payload["tables"]["neighbor_signal"] = neighbor_signal_summary(payload["tables"].get("neighbor_table", {}).get("raw", ""))
    return payload
