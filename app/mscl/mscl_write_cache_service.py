def update_write_cache(
    *,
    cached,
    sample_rate,
    tx_power,
    tx_enum,
    input_range,
    unit,
    cjc_unit,
    low_pass_filter,
    storage_limit_mode,
    lost_beacon_timeout,
    lost_beacon_enabled,
    diagnostic_interval,
    diagnostic_enabled,
    supports_default_mode,
    default_mode,
    supports_inactivity_timeout,
    inactivity_timeout,
    inactivity_enabled,
    supports_check_radio_interval,
    check_radio_interval,
    supports_transducer_type,
    transducer_type,
    supports_temp_sensor_options,
    sensor_type,
    wire_type,
    write_hw_effective,
    channels,
    now_ts,
):
    out = dict(cached or {})

    out["current_rate"] = int(sample_rate)
    out["current_power"] = int(tx_power)
    out["current_power_enum"] = int(tx_enum)

    if input_range is not None:
        out["current_input_range"] = int(input_range)
    if unit is not None:
        out["current_unit"] = int(unit)
    if cjc_unit is not None:
        out["current_cjc_unit"] = int(cjc_unit)
    if low_pass_filter is not None:
        out["current_low_pass"] = int(low_pass_filter)
    if storage_limit_mode is not None:
        out["current_storage_limit_mode"] = int(storage_limit_mode)
    if lost_beacon_timeout is not None:
        out["current_lost_beacon_timeout"] = 0 if not lost_beacon_enabled else int(lost_beacon_timeout)
    if diagnostic_interval is not None:
        out["current_diagnostic_interval"] = 0 if not diagnostic_enabled else int(diagnostic_interval)

    if supports_default_mode and default_mode is not None:
        out["current_default_mode"] = int(default_mode)
    if supports_inactivity_timeout and inactivity_timeout is not None:
        out["current_inactivity_timeout"] = 0 if not inactivity_enabled else int(inactivity_timeout)
    if supports_check_radio_interval and check_radio_interval is not None:
        out["current_check_radio_interval"] = int(check_radio_interval)

    hw = dict(write_hw_effective or {})
    if supports_transducer_type:
        if hw.get("transducer_type") is not None:
            out["current_transducer_type"] = int(hw.get("transducer_type"))
        elif transducer_type is not None:
            out["current_transducer_type"] = int(transducer_type)

    if supports_temp_sensor_options:
        if hw.get("sensor_type") is not None:
            out["current_sensor_type"] = int(hw.get("sensor_type"))
        elif sensor_type is not None:
            out["current_sensor_type"] = int(sensor_type)

        if hw.get("wire_type") is not None:
            out["current_wire_type"] = int(hw.get("wire_type"))
        elif wire_type is not None:
            out["current_wire_type"] = int(wire_type)

    enabled_ids = {int(ch_id) for ch_id in (channels or [1])}
    out["channels"] = [{"id": i, "enabled": (i in enabled_ids)} for i in (1, 2)]
    out["ts"] = float(now_ts)
    return out
