def allowed_tx_powers(model_hint, is_tc_link_200_oem_model_fn):
    if is_tc_link_200_oem_model_fn(model_hint):
        return [10, 5, 0]
    return [16, 10, 5, 0]


def tx_power_to_enum(tx_power_dbm):
    p_map = {16: 1, 10: 2, 5: 3, 0: 4}
    try:
        return p_map.get(int(tx_power_dbm), 1)
    except Exception:
        return 1


def normalize_tx_power(tx_power, model_hint, is_tc_link_200_oem_model_fn):
    allowed = allowed_tx_powers(model_hint, is_tc_link_200_oem_model_fn)

    if tx_power is None:
        tx_power = allowed[0]

    try:
        tx_int = int(tx_power)
    except Exception:
        tx_int = allowed[0]

    warning = None
    if tx_int not in allowed:
        fallback_tx = next((p for p in allowed if tx_int >= p), allowed[-1])
        warning = (
            f"tx_power={tx_int} unsupported for model={model_hint}; "
            f"using {fallback_tx} dBm"
        )
        tx_int = fallback_tx

    return {
        "tx_power": int(tx_int),
        "tx_enum": tx_power_to_enum(tx_int),
        "allowed": list(allowed),
        "warning": warning,
    }
