import os
import re
import subprocess
import ipaddress
from typing import Any


def _env_or_default(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


AP_INTERFACE = _env_or_default("AP_UI_INTERFACE", "wlan0")
AP_PROFILE = _env_or_default("AP_UI_PROFILE", _env_or_default("AP_NAME", "rpi-ap"))
AP_LEASE_FILE = _env_or_default(
    "AP_UI_LEASE_FILE",
    f"/host/var/lib/NetworkManager/dnsmasq-{AP_INTERFACE}.leases",
)


def _run_command(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _read_single_value(args: list[str]) -> str:
    code, stdout, _stderr = _run_command(args)
    if code != 0:
        return ""
    for line in stdout.splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def normalize_mac_text(value: str) -> str:
    return str(value or "").strip().replace("\\:", ":")


def _first_int(value: str) -> int | None:
    match = re.search(r"-?\d+", value)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def parse_station_dump(text: str) -> list[dict[str, Any]]:
    clients: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("Station ") and " (on " in stripped:
            if current is not None:
                clients.append(current)
            mac = stripped.split()[1].lower()
            current = {"mac": mac}
            continue

        if current is None or ":" not in stripped:
            continue

        key, value = stripped.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "inactive time":
            parsed = _first_int(value)
            if parsed is not None:
                current["inactive_ms"] = parsed
        elif key == "rx bytes":
            parsed = _first_int(value)
            if parsed is not None:
                current["rx_bytes"] = parsed
        elif key == "rx packets":
            parsed = _first_int(value)
            if parsed is not None:
                current["rx_packets"] = parsed
        elif key == "tx bytes":
            parsed = _first_int(value)
            if parsed is not None:
                current["tx_bytes"] = parsed
        elif key == "tx packets":
            parsed = _first_int(value)
            if parsed is not None:
                current["tx_packets"] = parsed
        elif key == "tx failed":
            parsed = _first_int(value)
            if parsed is not None:
                current["tx_failed"] = parsed
        elif key in {"signal", "signal avg"}:
            parsed = _first_int(value)
            if parsed is not None and (key == "signal" or "signal_dbm" not in current):
                current["signal_dbm"] = parsed
        elif key == "tx bitrate":
            current["tx_bitrate"] = value
        elif key == "rx bitrate":
            current["rx_bitrate"] = value

    if current is not None:
        clients.append(current)
    return clients


def parse_dnsmasq_leases(text: str) -> dict[str, dict[str, str]]:
    leases: dict[str, dict[str, str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        _expiry, mac, ip, hostname, _client_id = parts[:5]
        entry: dict[str, str] = {"ip": ip}
        hostname = hostname.strip()
        if hostname and hostname != "*":
            entry["hostname"] = hostname
        leases[mac.lower()] = entry
    return leases


def parse_ip_neigh(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_line in text.splitlines():
        parts = raw_line.split()
        if len(parts) < 3:
            continue
        if "lladdr" not in parts:
            continue
        try:
            mac = parts[parts.index("lladdr") + 1].lower()
        except (ValueError, IndexError):
            continue
        address = parts[0]
        existing = mapping.get(mac, "")
        if _prefer_ip_address(address, existing):
            mapping[mac] = address
    return mapping


def _prefer_ip_address(candidate: str, current: str) -> bool:
    if not current:
        return True
    try:
        candidate_ip = ipaddress.ip_address(candidate)
        current_ip = ipaddress.ip_address(current)
    except ValueError:
        return True
    if candidate_ip.version == 4 and current_ip.version != 4:
        return True
    if candidate_ip.version != 4 and current_ip.version == 4:
        return False
    if candidate_ip.version == 6 and current_ip.version == 6:
        if candidate_ip.is_link_local and not current_ip.is_link_local:
            return False
        if not candidate_ip.is_link_local and current_ip.is_link_local:
            return True
    return False


def _signal_band(signal_dbm: int | None) -> str:
    if signal_dbm is None:
        return "unknown"
    if signal_dbm >= -55:
        return "excellent"
    if signal_dbm >= -67:
        return "good"
    if signal_dbm >= -75:
        return "fair"
    return "weak"


def _signal_quality(signal_dbm: int | None) -> int | None:
    if signal_dbm is None:
        return None
    return max(0, min(100, 2 * (signal_dbm + 100)))


def _normalize_signal_dbm(value: Any) -> int | None:
    try:
        signal_dbm = int(value)
    except (TypeError, ValueError):
        return None
    if signal_dbm >= 0:
        return None
    return signal_dbm


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _lease_file_for_interface(interface: str) -> str:
    override = os.getenv("AP_UI_LEASE_FILE", "").strip()
    if override:
        return override
    return f"/host/var/lib/NetworkManager/dnsmasq-{interface}.leases"


def load_ap_leases(path: str = AP_LEASE_FILE) -> dict[str, dict[str, str]]:
    return parse_dnsmasq_leases(_read_text_file(path))


def _display_name_for_client(client: dict[str, Any]) -> str:
    hostname = str(client.get("hostname") or "").strip()
    if hostname:
        return hostname
    return str(client.get("mac") or "").strip() or "unknown"


def _apply_signal(client: dict[str, Any], signal_dbm: int | None, source: str) -> None:
    normalized = _normalize_signal_dbm(signal_dbm)
    if normalized is None:
        return
    client["signal_dbm"] = normalized
    client["signal_quality"] = _signal_quality(normalized)
    client["signal_band"] = _signal_band(normalized)
    client["signal_source"] = source
    client["signal_quality_source"] = "rssi"


def list_ap_clients(interface: str = AP_INTERFACE) -> list[dict[str, Any]]:
    _code, station_stdout, _stderr = _run_command(["iw", "dev", interface, "station", "dump"])
    _code, neigh_stdout, _stderr = _run_command(["ip", "neigh", "show", "dev", interface])
    ip_by_mac = parse_ip_neigh(neigh_stdout)
    leases_by_mac = load_ap_leases(_lease_file_for_interface(interface))

    clients = parse_station_dump(station_stdout)
    for client in clients:
        lease = leases_by_mac.get(client["mac"], {})
        client["ip"] = ip_by_mac.get(client["mac"], lease.get("ip", ""))
        client["hostname"] = lease.get("hostname", "")

        signal_dbm = _normalize_signal_dbm(client.get("signal_dbm"))
        client.pop("signal_dbm", None)
        client["signal_band"] = "unknown"
        client["signal_quality"] = None
        client["signal_source"] = ""
        client["signal_quality_source"] = "unknown"
        _apply_signal(client, signal_dbm, "driver")

        client["display_name"] = _display_name_for_client(client)
    clients.sort(key=lambda item: item.get("signal_dbm", -999), reverse=True)
    return clients


def get_ap_profile(interface: str = AP_INTERFACE, profile: str = AP_PROFILE) -> dict[str, Any]:
    code, stdout, stderr = _run_command(
        [
            "nmcli",
            "-g",
            "connection.id,connection.interface-name,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless.band,802-11-wireless.channel,ipv4.method,ipv6.method",
            "connection",
            "show",
            profile,
        ]
    )

    profile_payload = {
        "name": profile,
        "exists": code == 0,
        "ssid": "",
        "mode": "",
        "band": "",
        "channel": "",
        "autoconnect": "",
        "ipv4_method": "",
        "ipv6_method": "",
        "interface_name": "",
    }
    if code == 0:
        values = [line.strip() for line in stdout.splitlines()]
        while len(values) < 9:
            values.append("")
        (
            profile_payload["name"],
            profile_payload["interface_name"],
            profile_payload["autoconnect"],
            profile_payload["ssid"],
            profile_payload["mode"],
            profile_payload["band"],
            profile_payload["channel"],
            profile_payload["ipv4_method"],
            profile_payload["ipv6_method"],
        ) = values[:9]
    else:
        profile_payload["error"] = stderr.strip() or "profile not found"

    effective_interface = profile_payload["interface_name"] or interface
    active_connection = _read_single_value(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", effective_interface])
    state = _read_single_value(["nmcli", "-g", "GENERAL.STATE", "device", "show", effective_interface])
    mac_address = normalize_mac_text(_read_single_value(["nmcli", "-g", "GENERAL.HWADDR", "device", "show", effective_interface]))
    ipv4_addresses = [
        line.strip()
        for line in _read_lines(["nmcli", "-g", "IP4.ADDRESS", "device", "show", effective_interface])
        if line.strip()
    ]

    return {
        "interface": effective_interface,
        "active": bool(active_connection and active_connection != "--"),
        "active_connection": active_connection if active_connection != "--" else "",
        "state": state,
        "mac_address": mac_address,
        "ipv4_addresses": ipv4_addresses,
        "profile": profile_payload,
    }


def _read_lines(args: list[str]) -> list[str]:
    code, stdout, _stderr = _run_command(args)
    if code != 0:
        return []
    return stdout.splitlines()


def build_ap_snapshot(
    interface: str = AP_INTERFACE,
    profile: str = AP_PROFILE,
) -> dict[str, Any]:
    profile_data = get_ap_profile(interface=interface, profile=profile)
    effective_interface = str(profile_data.get("interface") or interface).strip() or interface
    clients = list_ap_clients(interface=effective_interface)

    if profile_data["active"]:
        status = "ok"
    elif profile_data["profile"].get("exists"):
        status = "warn"
    else:
        status = "err"

    return {
        "status": status,
        "interface": effective_interface,
        "profile_name": profile,
        "active": profile_data["active"],
        "active_connection": profile_data["active_connection"],
        "state": profile_data["state"],
        "mac_address": profile_data["mac_address"],
        "ipv4_addresses": profile_data["ipv4_addresses"],
        "profile": profile_data["profile"],
        "clients": clients,
        "client_count": len(clients),
    }


def set_ap_state(action: str, profile: str = AP_PROFILE) -> tuple[bool, str]:
    normalized = str(action or "").strip().lower()
    if normalized == "start":
        code, stdout, stderr = _run_command(["nmcli", "connection", "up", profile])
    elif normalized == "stop":
        code, stdout, stderr = _run_command(["nmcli", "connection", "down", profile])
    elif normalized == "restart":
        _run_command(["nmcli", "connection", "down", profile])
        code, stdout, stderr = _run_command(["nmcli", "connection", "up", profile])
    else:
        return False, f"unsupported action: {action}"

    message = (stdout or stderr).strip()
    return code == 0, message
