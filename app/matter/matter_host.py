try:
    from usb_discovery import read_text, serial_devices_by_id, usb_devices
except ModuleNotFoundError:
    from app.shared.usb_discovery import read_text, serial_devices_by_id, usb_devices
