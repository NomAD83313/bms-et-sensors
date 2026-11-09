class ExportRequestValidationError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = int(status_code)


def _query_bool_from_raw(raw_value, default=False):
    if raw_value is None:
        return bool(default)
    s = str(raw_value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return bool(default)


def parse_export_storage_request(args, parse_iso_utc_to_ns_fn):
    export_format = str(args.get("format", "csv") or "csv").strip().lower()
    if export_format not in ("csv", "json", "none"):
        raise ExportRequestValidationError("Unsupported format. Use 'csv', 'json', or 'none'.", 400)

    ingest_influx = _query_bool_from_raw(args.get("ingest_influx"), True)

    ui_from_raw = args.get("ui_from")
    ui_to_raw = args.get("ui_to")
    ui_window_from_ns = None
    ui_window_to_ns = None
    if ui_from_raw is not None or ui_to_raw is not None:
        if not ui_from_raw or not ui_to_raw:
            raise ExportRequestValidationError("Both ui_from and ui_to are required", 400)
        try:
            ui_window_from_ns = parse_iso_utc_to_ns_fn(ui_from_raw, "ui_from")
            ui_window_to_ns = parse_iso_utc_to_ns_fn(ui_to_raw, "ui_to")
        except ValueError as ve:
            raise ExportRequestValidationError(str(ve), 400) from ve
        if int(ui_window_to_ns) <= int(ui_window_from_ns):
            raise ExportRequestValidationError("ui_to must be greater than ui_from", 400)

    host_hours_raw = args.get("host_hours")
    host_hours = None
    if host_hours_raw is not None and str(host_hours_raw).strip() != "":
        try:
            host_hours = float(host_hours_raw)
        except (TypeError, ValueError) as exc:
            raise ExportRequestValidationError("Invalid host_hours. Use a positive number.", 400) from exc
        if host_hours <= 0:
            raise ExportRequestValidationError("host_hours must be > 0", 400)

    return {
        "export_format": export_format,
        "ingest_influx": ingest_influx,
        "ui_from_raw": ui_from_raw,
        "ui_to_raw": ui_to_raw,
        "ui_window_from_ns": ui_window_from_ns,
        "ui_window_to_ns": ui_window_to_ns,
        "host_hours": host_hours,
    }
