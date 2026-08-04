import threading
import time


def start_sampling_run(
    *,
    node_id,
    body,
    internal_connect,
    state,
    ensure_beacon_on,
    mscl_mod,
    log_func,
    rate_map,
    sampling_mode_labels,
    duration_to_seconds_fn,
    set_sampling_mode_fn,
    set_sampling_data_type_fn,
    start_sampling_via_sync_network_fn,
    schedule_idle_after_fn,
):
    ok, msg = internal_connect()
    if not ok or state.BASE_STATION is None:
        return {"success": False, "error": f"Base station not connected: {msg}"}

    mode_key = str(body.get("log_transmit_mode") or "transmit").lower()
    data_type_raw = str(body.get("data_type") or "float").strip().lower()
    data_type = "calibrated" if data_type_raw == "calibrated" else "float"
    continuous = bool(body.get("continuous", False))
    duration_value = body.get("duration_value", 60)
    duration_units = body.get("duration_units", "seconds")
    duration_sec = duration_to_seconds_fn(duration_value, duration_units, continuous)
    sample_rate = body.get("sample_rate")
    if sample_rate is not None and str(sample_rate) != "":
        try:
            sample_rate = int(sample_rate)
        except Exception:
            sample_rate = None
    else:
        sample_rate = None

    try:
        ensure_beacon_on()
        node = mscl_mod.WirelessNode(node_id, state.BASE_STATION)
        node.readWriteRetries(10)
        cfg = mscl_mod.WirelessNodeConfig()
        # Keep runtime sampling config explicit and minimal.
        try:
            cfg.samplingMode(mscl_mod.WirelessTypes.samplingMode_sync)
        except Exception:
            pass
        try:
            cfg.unlimitedDuration(True)
        except Exception:
            pass

        # Preserve active channels for runtime start; default config may enable
        # extra channels and reduce max supported sample rate on this node.
        try:
            active_mask = mscl_mod.ChannelMask()
            enabled = []
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            ch_list = cached.get("channels") if isinstance(cached, dict) else None
            if isinstance(ch_list, list) and ch_list:
                for ch in ch_list:
                    try:
                        cid = int(ch.get("id"))
                        cen = bool(ch.get("enabled"))
                    except Exception:
                        continue
                    if cen and cid in (1, 2):
                        enabled.append(cid)
            if not enabled:
                try:
                    cur_mask = node.getActiveChannels()
                    for cid in (1, 2):
                        try:
                            if cur_mask.enabled(cid):
                                enabled.append(cid)
                        except Exception:
                            continue
                except Exception:
                    pass
            if not enabled:
                enabled = [1]
            for cid in enabled:
                active_mask.enable(int(cid))
            cfg.activeChannels(active_mask)
        except Exception as e:
            log_func(f"[mscl-web] [S-RUN] activeChannels not set node_id={node_id}: {e}")

        sample_rate_set = False
        if sample_rate is not None:
            try:
                cfg.sampleRate(int(sample_rate))
                sample_rate_set = True
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] sampleRate not set node_id={node_id}: {e}")

        mode_value, mode_err = set_sampling_mode_fn(cfg, mode_key)
        if mode_value is None:
            return {
                "success": False,
                "error": f"Log/Transmit mode not set: {mode_err}",
                "rate": sample_rate,
                "mode": mode_key,
            }

        data_type_value, data_type_err = set_sampling_data_type_fn(cfg, data_type)
        if data_type_value is None:
            return {
                "success": False,
                "error": f"Data type not set: {data_type_err}",
                "rate": sample_rate,
                "mode": mode_key,
                "data_type": data_type,
            }

        apply_ok = False
        try:
            # SensorConnect-like flow:
            # 1) verify/apply node config
            # 2) add node to sync network
            # 3) apply network configuration
            # 4) start network sampling
            try:
                issues = mscl_mod.ConfigIssues()
                if not node.verifyConfig(cfg, issues):
                    issue_texts = []
                    try:
                        for issue in issues:
                            issue_texts.append(str(issue.description()))
                    except Exception:
                        pass
                    joined = "; ".join([t for t in issue_texts if t]) or "verifyConfig returned false"
                    log_func(
                        f"[mscl-web] [S-RUN] verifyConfig failed node_id={node_id} "
                        f"rate={sample_rate} data_type={data_type} mode={mode_key}: {joined}"
                    )
                    return {
                        "success": False,
                        "error": f"Sampling config verify failed: {joined}",
                        "rate": sample_rate,
                        "mode": mode_key,
                    }
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] verifyConfig warning node_id={node_id}: {e}")

            node.applyConfig(cfg)
            try:
                eff_rate = int(node.getSampleRate())
                log_func(
                    f"[mscl-web] [S-RUN] post-apply sampleRate node_id={node_id}: {eff_rate} ({rate_map.get(eff_rate, 'unknown')})"
                )
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-apply sampleRate read failed node_id={node_id}: {e}")
            try:
                dm = int(node.getDataMode())
                log_func(f"[mscl-web] [S-RUN] post-apply dataMode node_id={node_id}: {dm}")
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-apply dataMode read failed node_id={node_id}: {e}")
            try:
                cm = int(node.getDataCollectionMethod())
                log_func(f"[mscl-web] [S-RUN] post-apply collectionMethod node_id={node_id}: {cm}")
            except Exception as e:
                log_func(
                    f"[mscl-web] [S-RUN] post-apply collectionMethod read failed node_id={node_id}: {e}"
                )
            try:
                sm = node.getSamplingMode()
                log_func(f"[mscl-web] [S-RUN] post-apply samplingMode node_id={node_id}: {sm}")
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-apply samplingMode read failed node_id={node_id}: {e}")
            start_mode = start_sampling_via_sync_network_fn(node, node_id)
            try:
                eff_rate2 = int(node.getSampleRate())
                log_func(
                    f"[mscl-web] [S-RUN] post-start sampleRate node_id={node_id}: {eff_rate2} ({rate_map.get(eff_rate2, 'unknown')})"
                )
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-start sampleRate read failed node_id={node_id}: {e}")
            try:
                dm2 = int(node.getDataMode())
                log_func(f"[mscl-web] [S-RUN] post-start dataMode node_id={node_id}: {dm2}")
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-start dataMode read failed node_id={node_id}: {e}")
            try:
                cm2 = int(node.getDataCollectionMethod())
                log_func(f"[mscl-web] [S-RUN] post-start collectionMethod node_id={node_id}: {cm2}")
            except Exception as e:
                log_func(
                    f"[mscl-web] [S-RUN] post-start collectionMethod read failed node_id={node_id}: {e}"
                )
            try:
                sm2 = node.getSamplingMode()
                log_func(f"[mscl-web] [S-RUN] post-start samplingMode node_id={node_id}: {sm2}")
            except Exception as e:
                log_func(f"[mscl-web] [S-RUN] post-start samplingMode read failed node_id={node_id}: {e}")
            apply_ok = True
        except Exception as net_err:
            log_func(f"[mscl-web] [S-RUN] sync-network start failed node_id={node_id}: {net_err}")
            return {
                "success": False,
                "error": f"Sync network start failed: {net_err}",
                "rate": sample_rate,
                "mode": mode_key,
            }

        token = time.time()
        state.SAMPLE_STOP_TOKENS[node_id] = token
        run = {
            "run_id": f"{node_id}-{int(token)}",
            "state": "running",
            "started_at": int(time.time()),
            "duration_sec": int(duration_sec),
            "continuous": bool(duration_sec == 0),
            "mode_key": mode_key,
            "mode_label": sampling_mode_labels.get(mode_key, mode_key),
            "mode_value": mode_value,
            "data_type": data_type,
            "data_type_value": data_type_value,
            "sample_rate": sample_rate,
            "sample_rate_set": sample_rate_set,
            "apply_config_ok": apply_ok,
            "start_method": start_mode,
        }
        state.SAMPLE_RUNS[node_id] = run
        if duration_sec > 0:
            t = threading.Thread(target=schedule_idle_after_fn, args=(node_id, duration_sec, token), daemon=True)
            t.start()

        if sample_rate is not None:
            rate_txt = rate_map.get(int(sample_rate), f"{sample_rate} (unknown)")
            rate_log = f"{sample_rate} ({rate_txt})"
        else:
            rate_log = "keep"
        log_func(
            f"[mscl-web] [S-RUN] start ok node_id={node_id} mode={run['mode_label']} "
            f"dur={duration_sec}s rate={rate_log} data_type={data_type}"
        )
        return {"success": True, "run": run}
    except Exception as e:
        log_func(f"[mscl-web] [S-RUN] start failed node_id={node_id}: {e}")
        return {"success": False, "error": str(e)}


def start_sampling_group_run(
    *,
    node_ids,
    body,
    internal_connect,
    state,
    ensure_beacon_on,
    mscl_mod,
    log_func,
    rate_map,
    sampling_mode_labels,
    duration_to_seconds_fn,
    set_sampling_mode_fn,
    set_sampling_data_type_fn,
    start_sampling_via_sync_network_multi_fn,
    schedule_idle_after_fn,
):
    ok, msg = internal_connect()
    if not ok or state.BASE_STATION is None:
        return {"success": False, "error": f"Base station not connected: {msg}"}

    node_ids = [int(x) for x in (node_ids or [])]
    if not node_ids:
        return {"success": False, "error": "No configured nodes"}

    mode_key = str(body.get("log_transmit_mode") or "transmit").lower()
    data_type_raw = str(body.get("data_type") or "float").strip().lower()
    data_type = "calibrated" if data_type_raw == "calibrated" else "float"
    continuous = bool(body.get("continuous", False))
    duration_value = body.get("duration_value", 60)
    duration_units = body.get("duration_units", "seconds")
    duration_sec = duration_to_seconds_fn(duration_value, duration_units, continuous)
    sample_rate = body.get("sample_rate")
    if sample_rate is not None and str(sample_rate) != "":
        try:
            sample_rate = int(sample_rate)
        except Exception:
            sample_rate = None
    else:
        sample_rate = None

    try:
        ensure_beacon_on()
        nodes = []
        active_node_ids = []
        cfgs = []
        skipped_nodes = []

        def skip_node(node_id, reason):
            reason_text = str(reason)
            skipped_nodes.append({"node_id": int(node_id), "reason": reason_text})
            log_func(f"[mscl-web] [S-GROUP] skip node_id={node_id}: {reason_text}")

        for node_id in node_ids:
            node = mscl_mod.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(10)
            cfg = mscl_mod.WirelessNodeConfig()
            try:
                cfg.samplingMode(mscl_mod.WirelessTypes.samplingMode_sync)
            except Exception:
                pass
            try:
                cfg.unlimitedDuration(True)
            except Exception:
                pass
            try:
                active_mask = mscl_mod.ChannelMask()
                enabled = []
                cached = state.NODE_READ_CACHE.get(int(node_id), {})
                ch_list = cached.get("channels") if isinstance(cached, dict) else None
                if isinstance(ch_list, list) and ch_list:
                    for ch in ch_list:
                        try:
                            cid = int(ch.get("id"))
                            cen = bool(ch.get("enabled"))
                        except Exception:
                            continue
                        if cen and cid in (1, 2):
                            enabled.append(cid)
                if not enabled:
                    try:
                        cur_mask = node.getActiveChannels()
                        for cid in (1, 2):
                            try:
                                if cur_mask.enabled(cid):
                                    enabled.append(cid)
                            except Exception:
                                continue
                    except Exception:
                        pass
                if not enabled:
                    enabled = [1]
                for cid in enabled:
                    active_mask.enable(int(cid))
                cfg.activeChannels(active_mask)
            except Exception as e:
                log_func(f"[mscl-web] [S-GROUP] activeChannels not set node_id={node_id}: {e}")

            sample_rate_set = False
            if sample_rate is not None:
                try:
                    cfg.sampleRate(int(sample_rate))
                    sample_rate_set = True
                except Exception as e:
                    log_func(f"[mscl-web] [S-GROUP] sampleRate not set node_id={node_id}: {e}")

            mode_value, mode_err = set_sampling_mode_fn(cfg, mode_key)
            if mode_value is None:
                skip_node(node_id, f"Log/Transmit mode not set: {mode_err}")
                continue

            data_type_value, data_type_err = set_sampling_data_type_fn(cfg, data_type)
            if data_type_value is None:
                skip_node(node_id, f"Data type not set: {data_type_err}")
                continue

            try:
                issues = mscl_mod.ConfigIssues()
                if not node.verifyConfig(cfg, issues):
                    issue_texts = []
                    try:
                        for issue in issues:
                            issue_texts.append(str(issue.description()))
                    except Exception:
                        pass
                    joined = "; ".join([t for t in issue_texts if t]) or "verifyConfig returned false"
                    log_func(f"[mscl-web] [S-GROUP] verifyConfig failed node_id={node_id}: {joined}")
                    skip_node(node_id, f"Sampling config verify failed: {joined}")
                    continue
            except Exception as e:
                log_func(f"[mscl-web] [S-GROUP] verifyConfig warning node_id={node_id}: {e}")
                skip_node(node_id, f"verifyConfig unavailable: {e}")
                continue

            try:
                node.applyConfig(cfg)
            except Exception as e:
                skip_node(node_id, f"applyConfig failed: {e}")
                continue
            nodes.append(node)
            active_node_ids.append(node_id)
            cfgs.append(
                {
                    "node_id": node_id,
                    "sample_rate_set": sample_rate_set,
                    "mode_value": mode_value,
                    "data_type_value": data_type_value,
                }
            )

        if not nodes:
            skipped_txt = "; ".join(
                f"{item['node_id']}: {item['reason']}" for item in skipped_nodes
            )
            return {
                "success": False,
                "error": f"No configured nodes available for group sampling"
                + (f" ({skipped_txt})" if skipped_txt else ""),
                "skipped_nodes": skipped_nodes,
            }

        start_method = start_sampling_via_sync_network_multi_fn(nodes, active_node_ids)
        token = time.time()
        for item in cfgs:
            node_id = int(item["node_id"])
            state.SAMPLE_STOP_TOKENS[node_id] = token
            run = {
                "run_id": f"group-{int(token)}-{node_id}",
                "group_run_id": f"group-{int(token)}",
                "state": "running",
                "started_at": int(time.time()),
                "duration_sec": int(duration_sec),
                "continuous": bool(duration_sec == 0),
                "mode_key": mode_key,
                "mode_label": sampling_mode_labels.get(mode_key, mode_key),
                "mode_value": item["mode_value"],
                "data_type": data_type,
                "data_type_value": item["data_type_value"],
                "sample_rate": sample_rate,
                "sample_rate_set": item["sample_rate_set"],
                "apply_config_ok": True,
                "start_method": start_method,
                "group_nodes": list(active_node_ids),
                "skipped_nodes": list(skipped_nodes),
            }
            state.SAMPLE_RUNS[node_id] = run
            if duration_sec > 0:
                t = threading.Thread(target=schedule_idle_after_fn, args=(node_id, duration_sec, token), daemon=True)
                t.start()

        rate_log = "keep"
        if sample_rate is not None:
            rate_txt = rate_map.get(int(sample_rate), f"{sample_rate} (unknown)")
            rate_log = f"{sample_rate} ({rate_txt})"
        log_func(
            f"[mscl-web] [S-GROUP] start ok node_ids={','.join(str(x) for x in active_node_ids)} "
            f"mode={sampling_mode_labels.get(mode_key, mode_key)} dur={duration_sec}s rate={rate_log} data_type={data_type}"
        )
        if skipped_nodes:
            skipped_txt = "; ".join(f"{item['node_id']}: {item['reason']}" for item in skipped_nodes)
            log_func(f"[mscl-web] [S-GROUP] skipped nodes: {skipped_txt}")
        return {
            "success": True,
            "run": {
                "group_run_id": f"group-{int(token)}",
                "node_ids": list(active_node_ids),
                "requested_node_ids": list(node_ids),
                "skipped_nodes": list(skipped_nodes),
                "duration_sec": int(duration_sec),
                "continuous": bool(duration_sec == 0),
                "mode_key": mode_key,
                "mode_label": sampling_mode_labels.get(mode_key, mode_key),
                "sample_rate": sample_rate,
                "data_type": data_type,
                "start_method": start_method,
            },
        }
    except Exception as e:
        log_func(f"[mscl-web] [S-GROUP] start failed node_ids={','.join(str(x) for x in node_ids)}: {e}")
        return {"success": False, "error": str(e)}
