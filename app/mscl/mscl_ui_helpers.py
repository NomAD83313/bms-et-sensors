def sampling_duration_to_seconds(duration_value, duration_units, continuous):
    if continuous:
        return 0
    try:
        value = float(duration_value)
    except Exception:
        value = 0.0
    if value < 0:
        value = 0.0
    unit = str(duration_units or "seconds").lower()
    multiplier = 1.0
    if unit.startswith("min"):
        multiplier = 60.0
    elif unit.startswith("hour"):
        multiplier = 3600.0
    seconds = int(value * multiplier)
    return max(0, min(seconds, 86400))


def collection_method_text(method_value, sampling_mode_map, sampling_mode_labels):
    try:
        method_int = int(method_value)
    except Exception:
        return None
    for key, value in sampling_mode_map.items():
        try:
            if int(value) == method_int:
                return sampling_mode_labels.get(key, key)
        except Exception:
            continue
    return f"Value {method_int}"


def is_tc_link_200_oem_model(model):
    return "tc-link-200-oem" in str(model or "").strip().lower()


def filter_default_modes_for_model(
    model,
    default_mode_options,
    default_mode_labels,
    current_default_mode=None,
):
    options = []
    for item in list(default_mode_options or []):
        try:
            value_int = int(item.get("value"))
        except Exception:
            continue
        label = str(item.get("label") or default_mode_labels.get(value_int, f"Value {value_int}"))
        if value_int == 6:
            label = "Sample"
        options.append({"value": value_int, "label": label})

    if is_tc_link_200_oem_model(model):
        allowed = {0, 5, 6}
        order = {0: 0, 5: 1, 6: 2}
        options = [item for item in options if int(item.get("value")) in allowed]
        options.sort(key=lambda item: order.get(int(item.get("value")), 99))

    if current_default_mode is not None:
        try:
            current_value = int(current_default_mode)
            if all(int(item.get("value")) != current_value for item in options):
                label = "Sample" if current_value == 6 else default_mode_labels.get(current_value, f"Value {current_value}")
                options.insert(0, {"value": current_value, "label": label})
        except Exception:
            pass
    return options


def tx_power_options_for_model(model, current_power=None):
    base = [10, 5, 0] if is_tc_link_200_oem_model(model) else [16, 10, 5, 0]
    options = [{"value": power, "label": f"{power} dBm"} for power in base]
    if current_power is not None:
        try:
            current_value = int(current_power)
            if all(int(item.get("value")) != current_value for item in options):
                options.insert(0, {"value": current_value, "label": f"{current_value} dBm"})
        except Exception:
            pass
    return options
