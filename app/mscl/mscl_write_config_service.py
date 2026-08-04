def build_write_config(
    *,
    mscl_mod,
    node,
    node_id,
    log_func,
    sample_rate,
    tx_enum,
    full_mask,
    input_range,
    unit,
    cjc_unit,
    low_pass_filter,
    storage_limit_mode,
    lost_beacon_timeout,
    lost_beacon_enabled,
    diagnostic_interval,
    diagnostic_enabled,
    include_default_mode,
    default_mode,
    inactivity_timeout,
    inactivity_enabled,
    check_radio_interval,
    data_mode,
    transducer_type,
    sensor_type,
    wire_type,
    supports_default_mode,
    supports_inactivity_timeout,
    supports_check_radio_interval,
    supports_transducer_type,
    supports_temp_sensor_options,
    ch1_mask_fn,
    ch2_mask_fn,
    get_temp_sensor_options_fn,
    set_temp_sensor_options_fn,
    wt_fn,
    data_mode_labels,
    unit_labels,
):
    cfg = mscl_mod.WirelessNodeConfig()
    cfg.samplingMode(mscl_mod.WirelessTypes.samplingMode_sync)
    cfg.sampleRate(int(sample_rate))
    cfg.transmitPower(int(tx_enum))
    cfg.activeChannels(full_mask)

    write_hw_effective = {"transducer_type": None, "sensor_type": None, "wire_type": None}
    sup_default = bool(supports_default_mode)
    sup_inactivity = bool(supports_inactivity_timeout)
    sup_radio = bool(supports_check_radio_interval)
    sup_transducer = bool(supports_transducer_type)
    sup_temp_opts = bool(supports_temp_sensor_options)

    if input_range is not None:
        ir_set = False
        ir_errs = []
        for setter in (
            lambda: cfg.inputRange(ch1_mask_fn(), int(input_range)),
            lambda: cfg.inputRange(int(input_range)),
        ):
            try:
                setter()
                ir_set = True
                break
            except Exception as e:
                ir_errs.append(str(e))
        if not ir_set:
            raise RuntimeError("Input Range not set: " + " | ".join(ir_errs))

    if unit is not None:
        unit_set = False
        unit_errs = []
        for setter in (
            lambda: cfg.unit(ch1_mask_fn(), int(unit)),
            lambda: cfg.unit(int(unit)),
        ):
            try:
                setter()
                unit_set = True
                break
            except Exception as e:
                unit_errs.append(str(e))
        if not unit_set:
            log_func(f"[mscl-web] Write warn node_id={node_id}: unit not set: {' | '.join(unit_errs)}")
        else:
            unit_label = unit_labels.get(int(unit), f"Value {int(unit)}")
            log_func(
                f"[mscl-web] Write unit node_id={node_id}: "
                f"requested={unit_label} ({int(unit)})"
            )

    if cjc_unit is not None:
        cjc_set = False
        cjc_errs = []
        for setter in (
            lambda: cfg.unit(ch2_mask_fn(), int(cjc_unit)),
        ):
            try:
                setter()
                cjc_set = True
                break
            except Exception as e:
                cjc_errs.append(str(e))
        if not cjc_set:
            log_func(f"[mscl-web] Write warn node_id={node_id}: cjc_unit not set: {' | '.join(cjc_errs)}")
        else:
            cjc_label = unit_labels.get(int(cjc_unit), f"Value {int(cjc_unit)}")
            log_func(
                f"[mscl-web] Write cjc-unit node_id={node_id}: "
                f"requested={cjc_label} ({int(cjc_unit)})"
            )

    if low_pass_filter is not None:
        lp_set = False
        lp_errs = []
        for setter in (
            lambda: cfg.lowPassFilter(ch1_mask_fn(), int(low_pass_filter)),
            lambda: cfg.lowPassFilter(full_mask, int(low_pass_filter)),
            lambda: cfg.lowPassFilter(int(low_pass_filter)),
        ):
            try:
                setter()
                lp_set = True
                break
            except Exception as e:
                lp_errs.append(str(e))
        if not lp_set:
            raise RuntimeError("Low Pass Filter not set: " + " | ".join(lp_errs))

    if storage_limit_mode is not None:
        cfg.storageLimitMode(int(storage_limit_mode))
    if lost_beacon_timeout is not None:
        try:
            cfg.lostBeaconTimeout(0 if not lost_beacon_enabled else int(lost_beacon_timeout))
        except Exception as e:
            if lost_beacon_enabled:
                raise
            log_func(f"[mscl-web] Write warn node_id={node_id}: lostBeaconTimeout off not set: {e}")
    if diagnostic_interval is not None:
        try:
            cfg.diagnosticInterval(0 if not diagnostic_enabled else int(diagnostic_interval))
        except Exception as e:
            if diagnostic_enabled:
                raise
            log_func(f"[mscl-web] Write warn node_id={node_id}: diagnosticInterval off not set: {e}")

    # SensorConnect-style behavior: try setters even when supports* reports NO.
    if include_default_mode and default_mode is not None:
        try:
            cfg.defaultMode(int(default_mode))
            sup_default = True
        except Exception as e:
            log_func(f"[mscl-web] Write warn node_id={node_id}: defaultMode not set: {e}")
    if inactivity_timeout is not None:
        try:
            cfg.inactivityTimeout(0 if not inactivity_enabled else int(inactivity_timeout))
            sup_inactivity = True
        except Exception as e:
            if inactivity_enabled:
                log_func(f"[mscl-web] Write warn node_id={node_id}: inactivityTimeout not set: {e}")
            else:
                log_func(f"[mscl-web] Write warn node_id={node_id}: inactivityTimeout off not set: {e}")
    if check_radio_interval is not None:
        try:
            cfg.checkRadioInterval(int(check_radio_interval))
            sup_radio = True
        except Exception as e:
            log_func(f"[mscl-web] Write warn node_id={node_id}: checkRadioInterval not set: {e}")
    if data_mode is not None:
        dm_set = False
        dm_errs = []
        for setter in (
            lambda: cfg.dataMode(int(data_mode)),
            lambda: cfg.dataCollectionMethod(int(data_mode)),
        ):
            try:
                setter()
                dm_set = True
                break
            except Exception as e:
                dm_errs.append(str(e))
        if not dm_set:
            log_func(f"[mscl-web] Write warn node_id={node_id}: data_mode not set: {' | '.join(dm_errs)}")
        else:
            log_func(
                f"[mscl-web] Write data_mode node_id={node_id}: "
                f"requested={data_mode_labels.get(int(data_mode), f'Value {int(data_mode)}')} ({int(data_mode)})"
            )

    # Hardware -> temp sensor options (SensorConnect-style best effort)
    if transducer_type is not None or sensor_type is not None or wire_type is not None:
        try:
            tso, tso_err = get_temp_sensor_options_fn(node)
            if tso is None:
                raise RuntimeError(tso_err or "getTempSensorOptions failed")

            try:
                cur_transducer = int(tso.transducerType())
            except Exception:
                cur_transducer = None
            try:
                cur_rtd_type = int(tso.rtdType())
            except Exception:
                cur_rtd_type = None
            try:
                cur_thermistor_type = int(tso.thermistorType())
            except Exception:
                cur_thermistor_type = None
            try:
                cur_thermocouple_type = int(tso.thermocoupleType())
            except Exception:
                cur_thermocouple_type = None
            try:
                cur_rtd_wire = int(tso.rtdWireType())
            except Exception:
                cur_rtd_wire = None

            eff_transducer = int(transducer_type) if transducer_type is not None else cur_transducer
            new_tso = None
            hw_effective = {"transducer_type": None, "sensor_type": None, "wire_type": None}
            if eff_transducer == wt_fn("transducer_rtd", 1):
                eff_sensor = int(sensor_type) if sensor_type is not None else cur_rtd_type
                eff_wire = int(wire_type) if wire_type is not None else cur_rtd_wire
                if eff_sensor is None:
                    eff_sensor = wt_fn("rtd_uncompensated", 0)
                if eff_wire is None:
                    eff_wire = wt_fn("rtd_2wire", 0)
                new_tso = mscl_mod.TempSensorOptions.RTD(int(eff_wire), int(eff_sensor))
                hw_effective["transducer_type"] = int(eff_transducer)
                hw_effective["sensor_type"] = int(eff_sensor)
                hw_effective["wire_type"] = int(eff_wire)
            elif eff_transducer == wt_fn("transducer_thermistor", 2):
                eff_sensor = int(sensor_type) if sensor_type is not None else cur_thermistor_type
                if eff_sensor is None:
                    eff_sensor = wt_fn("thermistor_uncompensated", 0)
                new_tso = mscl_mod.TempSensorOptions.Thermistor(int(eff_sensor))
                hw_effective["transducer_type"] = int(eff_transducer)
                hw_effective["sensor_type"] = int(eff_sensor)
                hw_effective["wire_type"] = None
            elif eff_transducer == wt_fn("transducer_thermocouple", 0):
                eff_sensor = int(sensor_type) if sensor_type is not None else cur_thermocouple_type
                if eff_sensor is None:
                    eff_sensor = 0
                new_tso = mscl_mod.TempSensorOptions.Thermocouple(int(eff_sensor))
                hw_effective["transducer_type"] = int(eff_transducer)
                hw_effective["sensor_type"] = int(eff_sensor)
                hw_effective["wire_type"] = None
            else:
                raise RuntimeError(f"Unsupported transducer type: {eff_transducer}")

            ok_tso, err_tso = set_temp_sensor_options_fn(cfg, new_tso)
            if not ok_tso:
                log_func(f"[mscl-web] Write warn node_id={node_id}: tempSensorOptions not set: {err_tso}")
            else:
                sup_transducer = True
                sup_temp_opts = True
                write_hw_effective["transducer_type"] = hw_effective["transducer_type"]
                write_hw_effective["sensor_type"] = hw_effective["sensor_type"]
                write_hw_effective["wire_type"] = hw_effective["wire_type"]
                requested_hw = {
                    "transducer_type": (int(transducer_type) if transducer_type is not None else None),
                    "sensor_type": (int(sensor_type) if sensor_type is not None else None),
                    "wire_type": (int(wire_type) if wire_type is not None else None),
                }
                if (
                    (requested_hw["transducer_type"] is not None and requested_hw["transducer_type"] != hw_effective["transducer_type"])
                    or (requested_hw["sensor_type"] is not None and requested_hw["sensor_type"] != hw_effective["sensor_type"])
                    or (requested_hw["wire_type"] is not None and requested_hw["wire_type"] != hw_effective["wire_type"])
                ):
                    log_func(
                        f"[mscl-web] Write hardware node_id={node_id}: "
                        f"requested(transducer={requested_hw['transducer_type']}, sensor={requested_hw['sensor_type']}, wire={requested_hw['wire_type']}) "
                        f"effective(transducer={hw_effective['transducer_type']}, "
                        f"sensor={hw_effective['sensor_type']}, wire={hw_effective['wire_type']})"
                    )
        except Exception as e:
            log_func(f"[mscl-web] Write warn node_id={node_id}: tempSensorOptions flow failed: {e}")

    return {
        "cfg": cfg,
        "supports_default_mode": sup_default,
        "supports_inactivity_timeout": sup_inactivity,
        "supports_check_radio_interval": sup_radio,
        "supports_transducer_type": sup_transducer,
        "supports_temp_sensor_options": sup_temp_opts,
        "write_hw_effective": write_hw_effective,
    }
