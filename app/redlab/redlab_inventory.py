from typing import Any, Iterable

_BLOCKED_UNIQUE_IDS = {"", "NO PERMISSION", "UNKNOWN"}


def is_valid_redlab_unique_id(value: Any) -> bool:
    return str(value or "").strip().upper() not in _BLOCKED_UNIQUE_IDS


def descriptor_has_valid_unique_id(descriptor: Any) -> bool:
    return is_valid_redlab_unique_id(getattr(descriptor, "unique_id", ""))


def canonical_redlab_device_id(unique_id: Any) -> str:
    clean = str(unique_id or "").strip()
    if not clean:
        return "redlab_unknown"
    return f"redlab_{clean}"


def redlab_descriptor_matches(descriptor: Any, device_ref: Any) -> bool:
    ref = str(device_ref or "").strip()
    if not ref:
        return False
    unique_id = str(getattr(descriptor, "unique_id", "") or "").strip()
    if not is_valid_redlab_unique_id(unique_id):
        return False
    return ref in {unique_id, canonical_redlab_device_id(unique_id)}


def select_redlab_descriptor(descriptors: list[Any], device_ref: Any = "") -> Any | None:
    valid_descriptors = [
        descriptor for descriptor in descriptors
        if descriptor_has_valid_unique_id(descriptor)
    ]
    if not valid_descriptors:
        return None
    ref = str(device_ref or "").strip()
    if not ref:
        return valid_descriptors[0]
    for descriptor in valid_descriptors:
        if redlab_descriptor_matches(descriptor, ref):
            return descriptor
    return None


def _json_safe_product_id(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, (int, str)):
        return value
    try:
        return int(value)
    except Exception:
        return str(value)


def normalize_redlab_descriptor(
    descriptor: Any,
    *,
    index: int | None = None,
    active_unique_id: str | None = None,
    active_device_ids: set[str] | None = None,
) -> dict[str, Any]:
    unique_id = str(getattr(descriptor, "unique_id", "") or "").strip()
    product_name = str(getattr(descriptor, "product_name", "") or "USB-TC").strip()
    product_id = _json_safe_product_id(getattr(descriptor, "product_id", None))
    device_id = canonical_redlab_device_id(unique_id)
    active_ref = str(active_unique_id or "").strip()
    active_ids = active_device_ids or set()
    active = device_id in active_ids or bool(active_ref and active_ref in {unique_id, device_id})

    return {
        "index": index,
        "device_id": device_id,
        "unique_id": unique_id,
        "product_name": product_name,
        "product_id": product_id,
        "connected": True,
        "active": active,
        "display_name": f"{product_name} {unique_id}".strip(),
    }


def normalize_redlab_inventory(
    descriptors: Iterable[Any],
    *,
    active_unique_id: str | None = None,
    active_device_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    valid_descriptors = [
        descriptor for descriptor in descriptors
        if descriptor_has_valid_unique_id(descriptor)
    ]
    return [
        normalize_redlab_descriptor(
            descriptor,
            index=index,
            active_unique_id=active_unique_id,
            active_device_ids=active_device_ids,
        )
        for index, descriptor in enumerate(valid_descriptors)
    ]
