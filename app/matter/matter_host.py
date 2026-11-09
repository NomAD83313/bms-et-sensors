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
