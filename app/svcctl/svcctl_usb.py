import time
from pathlib import Path


def usb_device_present(vendor_id: str, product_id: str) -> bool:
    root = Path("/sys/bus/usb/devices")
    if not root.exists():
        return False
    for device in root.iterdir():
        vendor_path = device / "idVendor"
        product_path = device / "idProduct"
        if not vendor_path.exists() or not product_path.exists():
            continue
        try:
            vendor = vendor_path.read_text(encoding="utf-8").strip().lower()
            product = product_path.read_text(encoding="utf-8").strip().lower()
        except Exception:
            continue
        if vendor == vendor_id.lower() and product == product_id.lower():
            return True
    return False


def build_usb_guard_state(
    *,
    enabled: bool,
    vendor_id: str,
    product_id: str,
    present_since: float | None,
    last_action: str,
    last_reason: str,
    service_status: str,
) -> dict:
    present = usb_device_present(vendor_id, product_id)
    stable_for = 0.0
    if present and present_since is not None:
        stable_for = max(0.0, time.time() - present_since)
    return {
        "enabled": enabled,
        "usb_vendor_id": vendor_id,
        "usb_product_id": product_id,
        "usb_present": present,
        "usb_stable_for_sec": round(stable_for, 1),
        "last_action": last_action,
        "last_reason": last_reason,
        "service_status": service_status,
    }
