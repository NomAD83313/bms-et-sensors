try:
    from mscl_write_payload_helpers import normalize_write_payload
except ImportError:  # pragma: no cover - fallback for unit test import style
    from app.mscl.mscl_write_payload_helpers import normalize_write_payload


class WriteRequestValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = int(status_code)


def validate_write_request(data, cached):
    if not isinstance(data, dict):
        raise WriteRequestValidationError("Invalid JSON body", 400)

    node_id_raw = data.get("node_id")
    try:
        node_id = int(node_id_raw)
    except (TypeError, ValueError) as exc:
        raise WriteRequestValidationError("node_id is required and must be integer", 400) from exc

    parsed = normalize_write_payload(data=data, cached=cached or {})
    if parsed.get("sample_rate") is None:
        raise WriteRequestValidationError(
            "Sample Rate is unknown. Run FULL READ once or set node in SensorConnect.",
            400,
        )

    return node_id, parsed
