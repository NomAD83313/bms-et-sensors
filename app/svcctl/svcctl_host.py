try:
    from usb_discovery import read_text, serial_devices_by_id, usb_devices
except ModuleNotFoundError:
    from app.shared.usb_discovery import read_text, serial_devices_by_id, usb_devices


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
