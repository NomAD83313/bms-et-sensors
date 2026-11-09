from typing import Any


def _config_issues_text(mscl_mod, issues):
    texts = []
    try:
        for issue in issues:
            try:
                txt = str(issue.description()).strip()
            except Exception:
                txt = str(issue).strip()
            if txt:
                texts.append(txt)
    except Exception:
        pass
    return "; ".join(texts)


def _verify_node_config(mscl_mod, node, cfg):
    try:
        issues = mscl_mod.ConfigIssues()
        ok = bool(node.verifyConfig(cfg, issues))
        if ok:
            return True, None
        details = _config_issues_text(mscl_mod, issues) or "verifyConfig returned false"
        return False, details
    except Exception:
        # Some MSCL stacks may not support verifyConfig consistently across models/fw.
        return True, None


def apply_write_connected(
    *,
    node_id: int,
    data: dict[str, Any],
    base_station: Any,
    node_read_cache: dict[int, dict[str, Any]],
    ensure_beacon_on_fn,
    mscl_mod,
    normalize_write_payload_fn,
    normalize_tx_power_fn,
    is_tc_link_200_model_fn,
    feature_supported_fn,
    build_write_config_fn,
    update_write_cache_fn,
    ch1_mask_fn,
    ch2_mask_fn,
    get_temp_sensor_options_fn,
    set_temp_sensor_options_fn,
    wt_fn,
    data_mode_labels,
    unit_labels,
    log_func,
    now_ts_fn,
    jsonify_fn,
):
    ensure_beacon_on_fn()
    node = mscl_mod.WirelessNode(int(node_id), base_station)
    node.readWriteRetries(15)
    cached = node_read_cache.get(int(node_id), {})
    parsed = normalize_write_payload_fn(data=data, cached=cached)
    sample_rate = parsed["sample_rate"]
    tx_power = parsed["tx_power"]
    channels = parsed["channels"]
    input_range = parsed["input_range"]
    unit = parsed["unit"]
    cjc_unit = parsed["cjc_unit"]
    low_pass_filter = parsed["low_pass_filter"]
    storage_limit_mode = parsed["storage_limit_mode"]
    lost_beacon_timeout = parsed["lost_beacon_timeout"]
    diagnostic_interval = parsed["diagnostic_interval"]
    lost_beacon_enabled = parsed["lost_beacon_enabled"]
    diagnostic_enabled = parsed["diagnostic_enabled"]
    default_mode = parsed["default_mode"]
    inactivity_timeout = parsed["inactivity_timeout"]
    inactivity_enabled = parsed["inactivity_enabled"]
    check_radio_interval = parsed["check_radio_interval"]
    data_mode = parsed["data_mode"]
    transducer_type = parsed["transducer_type"]
    sensor_type = parsed["sensor_type"]
    wire_type = parsed["wire_type"]
    model_hint = cached.get("model")
    tx_res = normalize_tx_power_fn(tx_power, model_hint, is_tc_link_200_model_fn)
    tx_power = int(tx_res["tx_power"])
    tx_enum = int(tx_res["tx_enum"])
    if tx_res.get("warning"):
        log_func(f"[mscl-web] Write warn node_id={node_id}: {tx_res.get('warning')}")
    full_mask = mscl_mod.ChannelMask()
    for ch_id in channels:
        full_mask.enable(ch_id)

    features = None
    try:
        features = node.features()
    except Exception:
        features = None
    supports_default_mode = feature_supported_fn(features, "supportsDefaultMode") if features is not None else False
    supports_inactivity_timeout = (
        feature_supported_fn(features, "supportsInactivityTimeout") if features is not None else False
    )
    supports_check_radio_interval = (
        feature_supported_fn(features, "supportsCheckRadioInterval") if features is not None else False
    )
    supports_transducer_type = feature_supported_fn(features, "supportsTransducerType") if features is not None else False
    supports_temp_sensor_options = (
        feature_supported_fn(features, "supportsTempSensorOptions") if features is not None else False
    )

    write_hw_effective = {"transducer_type": None, "sensor_type": None, "wire_type": None}
    build_res = build_write_config_fn(
        mscl_mod=mscl_mod,
        node=node,
        node_id=int(node_id),
        log_func=log_func,
        sample_rate=sample_rate,
        tx_enum=tx_enum,
        full_mask=full_mask,
        input_range=input_range,
        unit=unit,
        cjc_unit=cjc_unit,
        low_pass_filter=low_pass_filter,
        storage_limit_mode=storage_limit_mode,
        lost_beacon_timeout=lost_beacon_timeout,
        lost_beacon_enabled=lost_beacon_enabled,
        diagnostic_interval=diagnostic_interval,
        diagnostic_enabled=diagnostic_enabled,
        include_default_mode=True,
        default_mode=default_mode,
        inactivity_timeout=inactivity_timeout,
        inactivity_enabled=inactivity_enabled,
        check_radio_interval=check_radio_interval,
        data_mode=data_mode,
        transducer_type=transducer_type,
        sensor_type=sensor_type,
        wire_type=wire_type,
        supports_default_mode=supports_default_mode,
        supports_inactivity_timeout=supports_inactivity_timeout,
        supports_check_radio_interval=supports_check_radio_interval,
        supports_transducer_type=supports_transducer_type,
        supports_temp_sensor_options=supports_temp_sensor_options,
        ch1_mask_fn=ch1_mask_fn,
        ch2_mask_fn=ch2_mask_fn,
        get_temp_sensor_options_fn=get_temp_sensor_options_fn,
        set_temp_sensor_options_fn=set_temp_sensor_options_fn,
        wt_fn=wt_fn,
        data_mode_labels=data_mode_labels,
        unit_labels=unit_labels,
    )
    config = build_res["cfg"]
    supports_default_mode = bool(build_res["supports_default_mode"])
    supports_inactivity_timeout = bool(build_res["supports_inactivity_timeout"])
    supports_check_radio_interval = bool(build_res["supports_check_radio_interval"])
    supports_transducer_type = bool(build_res["supports_transducer_type"])
    supports_temp_sensor_options = bool(build_res["supports_temp_sensor_options"])
    write_hw_effective = dict(build_res["write_hw_effective"])
    verify_ok, verify_err = _verify_node_config(mscl_mod, node, config)
    if not verify_ok:
        return jsonify_fn(success=False, error=f"Configuration is not supported by node: {verify_err}")
    try:
        node.applyConfig(config)
    except Exception as e:
        emsg = str(e)
        if default_mode is not None and "Default Mode is not supported" in emsg:
            log_func(f"[mscl-web] Write warn node_id={node_id}: retry without Default Mode")
            supports_default_mode = False
            build_res2 = build_write_config_fn(
                mscl_mod=mscl_mod,
                node=node,
                node_id=int(node_id),
                log_func=log_func,
                sample_rate=sample_rate,
                tx_enum=tx_enum,
                full_mask=full_mask,
                input_range=input_range,
                unit=unit,
                cjc_unit=cjc_unit,
                low_pass_filter=low_pass_filter,
                storage_limit_mode=storage_limit_mode,
                lost_beacon_timeout=lost_beacon_timeout,
                lost_beacon_enabled=lost_beacon_enabled,
                diagnostic_interval=diagnostic_interval,
                diagnostic_enabled=diagnostic_enabled,
                include_default_mode=False,
                default_mode=default_mode,
                inactivity_timeout=inactivity_timeout,
                inactivity_enabled=inactivity_enabled,
                check_radio_interval=check_radio_interval,
                data_mode=data_mode,
                transducer_type=transducer_type,
                sensor_type=sensor_type,
                wire_type=wire_type,
                supports_default_mode=supports_default_mode,
                supports_inactivity_timeout=supports_inactivity_timeout,
                supports_check_radio_interval=supports_check_radio_interval,
                supports_transducer_type=supports_transducer_type,
                supports_temp_sensor_options=supports_temp_sensor_options,
                ch1_mask_fn=ch1_mask_fn,
                ch2_mask_fn=ch2_mask_fn,
                get_temp_sensor_options_fn=get_temp_sensor_options_fn,
                set_temp_sensor_options_fn=set_temp_sensor_options_fn,
                wt_fn=wt_fn,
                data_mode_labels=data_mode_labels,
                unit_labels=unit_labels,
            )
            config2 = build_res2["cfg"]
            supports_default_mode = bool(build_res2["supports_default_mode"])
            supports_inactivity_timeout = bool(build_res2["supports_inactivity_timeout"])
            supports_check_radio_interval = bool(build_res2["supports_check_radio_interval"])
            supports_transducer_type = bool(build_res2["supports_transducer_type"])
            supports_temp_sensor_options = bool(build_res2["supports_temp_sensor_options"])
            write_hw_effective = dict(build_res2["write_hw_effective"])
            verify_ok2, verify_err2 = _verify_node_config(mscl_mod, node, config2)
            if not verify_ok2:
                return jsonify_fn(success=False, error=f"Configuration is not supported by node: {verify_err2}")
            node.applyConfig(config2)
        else:
            raise

    node_key = int(node_id)
    cached = node_read_cache.get(node_key, {})
    cached = update_write_cache_fn(
        cached=cached,
        sample_rate=sample_rate,
        tx_power=tx_power,
        tx_enum=tx_enum,
        input_range=input_range,
        unit=unit,
        cjc_unit=cjc_unit,
        low_pass_filter=low_pass_filter,
        storage_limit_mode=storage_limit_mode,
        lost_beacon_timeout=lost_beacon_timeout,
        lost_beacon_enabled=lost_beacon_enabled,
        diagnostic_interval=diagnostic_interval,
        diagnostic_enabled=diagnostic_enabled,
        supports_default_mode=supports_default_mode,
        default_mode=default_mode,
        supports_inactivity_timeout=supports_inactivity_timeout,
        inactivity_timeout=inactivity_timeout,
        inactivity_enabled=inactivity_enabled,
        supports_check_radio_interval=supports_check_radio_interval,
        check_radio_interval=check_radio_interval,
        supports_transducer_type=supports_transducer_type,
        transducer_type=transducer_type,
        supports_temp_sensor_options=supports_temp_sensor_options,
        sensor_type=sensor_type,
        wire_type=wire_type,
        write_hw_effective=write_hw_effective,
        channels=channels,
        now_ts=now_ts_fn(),
    )
    node_read_cache[node_key] = cached
    log_func(f"[mscl-web] Write success node_id={node_id}")
    return jsonify_fn(success=True)


__all__ = ["apply_write_connected"]
