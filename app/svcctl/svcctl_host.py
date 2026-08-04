from glob import glob
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def serial_devices_by_id() -> list[dict[str, str]]:
    by_id = Path("/dev/serial/by-id")
    devices: list[dict[str, str]] = []
    if not by_id.exists():
        return devices

    for entry in sorted(by_id.iterdir(), key=lambda item: item.name):
        try:
            target = entry.resolve(strict=False)
            devices.append({"name": entry.name, "path": str(entry), "target": str(target)})
        except Exception:
            devices.append({"name": entry.name, "path": str(entry), "target": ""})
    return devices


def usb_devices() -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for raw in sorted(glob("/sys/bus/usb/devices/*")):
        device = Path(raw)
        vendor = read_text(device / "idVendor").lower()
        product_id = read_text(device / "idProduct").lower()
        if not vendor or not product_id or vendor == "1d6b":
            continue

        manufacturer = read_text(device / "manufacturer")
        product = read_text(device / "product")
        serial = read_text(device / "serial")
        key = (vendor, product_id, manufacturer, product)
        if key in seen:
            continue
        seen.add(key)
        devices.append(
            {
                "vendor_id": vendor,
                "product_id": product_id,
                "manufacturer": manufacturer,
                "product": product,
                "serial": serial,
                "devpath": device.name,
            }
        )

    return devices


def read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat", "r", encoding="utf-8") as handle:
        first = handle.readline().strip()
    values = [int(item) for item in first.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_percent(prev_total: int | None, prev_idle: int | None) -> tuple[float, int, int]:
    total, idle = read_cpu_times()
    if prev_total is None or prev_idle is None:
        return 0.0, total, idle

    delta_total = total - prev_total
    delta_idle = idle - prev_idle
    if delta_total <= 0:
        return 0.0, total, idle

    busy = max(0.0, 1.0 - (float(delta_idle) / float(delta_total)))
    return round(busy * 100.0, 2), total, idle


def memory_stats() -> dict[str, float]:
    mem: dict[str, int] = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            key, rest = line.split(":", 1)
            mem[key] = int(rest.strip().split()[0])

    total_kb = int(mem.get("MemTotal", 0))
    available_kb = int(mem.get("MemAvailable", 0))
    used_kb = max(0, total_kb - available_kb)
    used_pct = round((float(used_kb) / float(total_kb)) * 100.0, 2) if total_kb > 0 else 0.0
    return {
        "total_mb": round(total_kb / 1024.0, 1),
        "used_mb": round(used_kb / 1024.0, 1),
        "available_mb": round(available_kb / 1024.0, 1),
        "used_pct": used_pct,
    }
