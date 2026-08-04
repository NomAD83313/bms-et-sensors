from __future__ import annotations

import os
from pathlib import Path


def parse_usb_ids(raw_value: str) -> set[tuple[str, str]]:
    parsed: set[tuple[str, str]] = set()
    for raw_item in raw_value.split(","):
        item = raw_item.strip().lower()
        if not item or ":" not in item:
            continue
        vendor, product = (part.strip() for part in item.split(":", 1))
        if vendor and product:
            parsed.add((vendor, product))
    return parsed


def serial_usb_ids(path: str) -> tuple[str, str] | None:
    real_path = os.path.realpath(path) if os.path.exists(path) else path
    tty_name = os.path.basename(real_path)
    sys_path = Path("/sys/class/tty") / tty_name / "device"
    if not sys_path.exists():
        return None
    try:
        current = sys_path.resolve()
    except Exception:
        current = sys_path
    for current in (current, *current.parents):
        vendor_path = current / "idVendor"
        product_path = current / "idProduct"
        if vendor_path.exists() and product_path.exists():
            try:
                return (
                    vendor_path.read_text(encoding="ascii").strip().lower(),
                    product_path.read_text(encoding="ascii").strip().lower(),
                )
            except Exception:
                return None
    return None


def filter_excluded_usb_ports(paths: list[str], excluded_usb_ids: str) -> list[str]:
    excluded = parse_usb_ids(excluded_usb_ids)
    if not excluded:
        return list(paths)
    return [path for path in paths if serial_usb_ids(path) not in excluded]
