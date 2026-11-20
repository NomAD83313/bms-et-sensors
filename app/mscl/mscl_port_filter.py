try:
    from usb_discovery import parse_usb_ids, serial_usb_ids
except ModuleNotFoundError:
    from app.shared.usb_discovery import parse_usb_ids, serial_usb_ids


def filter_excluded_usb_ports(paths: list[str], excluded_usb_ids: str) -> list[str]:
    excluded = parse_usb_ids(excluded_usb_ids)
    if not excluded:
        return list(paths)
    return [path for path in paths if serial_usb_ids(path) not in excluded]
