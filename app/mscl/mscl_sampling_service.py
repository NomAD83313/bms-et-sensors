import time


def send_idle_sensorconnect_style(node, node_id, stage_tag, mscl_mod, log_func):
    """Set-to-idle flow from official example: setToIdle -> complete() -> result()."""
    command_sent = False
    transport_alive = False
    state_confirmed = False
    state_text = None
    last_reason = "not completed"
    idle_result = "pending"

    try:
        status = node.setToIdle()
        command_sent = True
        log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle started node_id={node_id}")
    except Exception as e:
        last_reason = f"setToIdle failed: {e}"
        idle_result = "failed"
        log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} node setToIdle failed node_id={node_id}: {e}")
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": state_confirmed,
            "state_text": state_text,
            "reason": last_reason,
            "idle_result": idle_result,
        }

    complete = False
    for poll in range(1, 41):
        try:
            if status.complete(300):
                complete = True
                transport_alive = True
                break
            if poll in (10, 20, 30, 40):
                log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} waiting node_id={node_id} poll {poll}/40")
        except Exception as e:
            last_reason = f"status.complete failed: {e}"
            log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} wait failed node_id={node_id} poll {poll}/40 ({last_reason})")
            break

    if not complete:
        return {
            "command_sent": command_sent,
            "transport_alive": transport_alive,
            "state_confirmed": False,
            "state_text": state_text,
            "reason": last_reason,
            "idle_result": idle_result,
        }

    try:
        result = status.result()
        success_val = getattr(mscl_mod.SetToIdleStatus, "setToIdleResult_success", None)
        canceled_val = getattr(mscl_mod.SetToIdleStatus, "setToIdleResult_canceled", None)

        if result == success_val:
            state_confirmed = True
            state_text = "Idle"
            last_reason = "confirmed:status.result=success"
            idle_result = "success"
            log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} confirmed node_id={node_id} by status.result")
        elif canceled_val is not None and result == canceled_val:
            last_reason = "status.result=canceled"
            idle_result = "canceled"
            log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} canceled node_id={node_id}")
        else:
            last_reason = f"status.result={result}"
            idle_result = "failed"
            log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} not-confirmed node_id={node_id} ({last_reason})")
    except Exception as e:
        last_reason = f"status.result failed: {e}"
        idle_result = "failed"
        log_func(f"[mscl-web] [PREP-IDLE] {stage_tag} result read failed node_id={node_id}: {e}")

    return {
        "command_sent": command_sent,
        "transport_alive": transport_alive,
        "state_confirmed": state_confirmed,
        "state_text": state_text,
        "reason": last_reason,
        "idle_result": idle_result,
    }


def start_sampling_best_effort(node, node_id, log_func):
    """Try primary sync start, then sync-resend, then non-sync fallback."""
    errors = []
    try:
        if callable(getattr(node, "startSyncSampling", None)):
            node.startSyncSampling()
            log_func(f"[mscl-web] [SAMPLE] startSyncSampling sent node_id={node_id}")
            return "sync"
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE] startSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"startSync={e}")
    try:
        if callable(getattr(node, "startNonSyncSampling", None)):
            node.startNonSyncSampling()
            log_func(f"[mscl-web] [SAMPLE] startNonSyncSampling sent node_id={node_id}")
            return "non-sync"
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE] startNonSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"non-sync={e}")
    try:
        if callable(getattr(node, "resendStartSyncSampling", None)):
            node.resendStartSyncSampling()
            log_func(f"[mscl-web] [SAMPLE] resendStartSyncSampling sent node_id={node_id}")
            return "sync-resend"
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE] resendStartSyncSampling failed node_id={node_id}: {e}")
        errors.append(f"resendSync={e}")
    raise RuntimeError("; ".join(errors) if errors else "No sampling start method available")


def start_sampling_via_sync_network(node, node_id, mscl_mod, state, log_func):
    """Prefer SensorConnect-like sync network start when available."""
    errors = []
    sync_cls = getattr(mscl_mod, "SyncSamplingNetwork", None)
    if sync_cls is None:
        raise RuntimeError("SyncSamplingNetwork is not available in MSCL")

    def mk():
        return sync_cls(state.BASE_STATION)

    def try_step(label, fn):
        try:
            fn()
            log_func(f"[mscl-web] [SAMPLE] {label} sent node_id={node_id}")
            return True
        except Exception as e:
            errors.append(f"{label}={e}")
            log_func(f"[mscl-web] [SAMPLE] {label} failed node_id={node_id}: {e}")
            return False

    def attempt1():
        net = mk()
        try:
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            cp = int(cached.get("comm_protocol")) if isinstance(cached, dict) and cached.get("comm_protocol") is not None else None
            if cp is None:
                try:
                    cp = int(node.communicationProtocol())
                except Exception:
                    cp = 1
            net.communicationProtocol(int(cp))
            log_func(f"[mscl-web] [SAMPLE] sync-network set commProtocol node_id={node_id}: {cp}")
        except Exception as e:
            log_func(f"[mscl-web] [SAMPLE] sync-network set commProtocol failed node_id={node_id}: {e}")
        try:
            net.lossless(True)
            log_func(f"[mscl-web] [SAMPLE] sync-network set lossless node_id={node_id}: True")
        except Exception as e:
            log_func(f"[mscl-web] [SAMPLE] sync-network set lossless failed node_id={node_id}: {e}")
        net.addNode(node)
        try:
            net.refresh()
        except Exception:
            pass
        net.applyConfiguration()
        try:
            net_ok = bool(net.ok()) if callable(getattr(net, "ok", None)) else None
            net_bw = float(net.percentBandwidth()) if callable(getattr(net, "percentBandwidth", None)) else None
            ninfo = None
            try:
                ninfo = net.getNodeNetworkInfo(int(node_id))
            except Exception:
                ninfo = None
            if ninfo is not None:
                try:
                    ns = int(ninfo.status())
                except Exception:
                    ns = None
                try:
                    nbw = float(ninfo.percentBandwidth())
                except Exception:
                    nbw = None
                try:
                    tdma = int(ninfo.tdmaAddress())
                except Exception:
                    tdma = None
                log_func(
                    f"[mscl-web] [SAMPLE] sync-network info node_id={node_id}: "
                    f"net_ok={net_ok} net_bw={net_bw} node_status={ns} node_bw={nbw} tdma={tdma}"
                )
            else:
                log_func(f"[mscl-web] [SAMPLE] sync-network info node_id={node_id}: net_ok={net_ok} net_bw={net_bw}")
        except Exception as e:
            log_func(f"[mscl-web] [SAMPLE] sync-network info read failed node_id={node_id}: {e}")
        if callable(getattr(net, "ok", None)) and (not bool(net.ok())):
            raise RuntimeError("SyncSamplingNetwork not OK after applyConfiguration")
        net.startSampling()

    if try_step("sync-network(startSampling)", attempt1):
        return "sync-network"

    def attempt2():
        net = mk()
        try:
            cached = state.NODE_READ_CACHE.get(int(node_id), {})
            cp = int(cached.get("comm_protocol")) if isinstance(cached, dict) and cached.get("comm_protocol") is not None else None
            if cp is None:
                try:
                    cp = int(node.communicationProtocol())
                except Exception:
                    cp = 1
            net.communicationProtocol(int(cp))
        except Exception:
            pass
        try:
            net.lossless(True)
        except Exception:
            pass
        net.addNode(node)
        try:
            net.refresh()
        except Exception:
            pass
        net.applyConfiguration()
        if callable(getattr(net, "ok", None)) and (not bool(net.ok())):
            raise RuntimeError("SyncSamplingNetwork not OK after applyConfiguration")
        net.startSampling_noBeacon()

    if try_step("sync-network(startSampling_noBeacon)", attempt2):
        return "sync-network-no-beacon"

    raise RuntimeError("; ".join(errors) if errors else "SyncSamplingNetwork start failed")


def start_sampling_via_sync_network_multi(nodes, node_ids, mscl_mod, state, log_func):
    """Build one SyncSamplingNetwork for all configured nodes and start once."""
    sync_cls = getattr(mscl_mod, "SyncSamplingNetwork", None)
    if sync_cls is None:
        raise RuntimeError("SyncSamplingNetwork is not available in MSCL")
    if not nodes:
        raise RuntimeError("No nodes available for SyncSamplingNetwork")

    net = sync_cls(state.BASE_STATION)

    cp = None
    for node_id in node_ids:
        cached = state.NODE_READ_CACHE.get(int(node_id), {})
        if isinstance(cached, dict) and cached.get("comm_protocol") is not None:
            try:
                cp = int(cached.get("comm_protocol"))
                break
            except Exception:
                pass
    if cp is None:
        for node in nodes:
            try:
                cp = int(node.communicationProtocol())
                break
            except Exception:
                continue
    if cp is None:
        cp = 1

    try:
        net.communicationProtocol(int(cp))
        log_func(f"[mscl-web] [SAMPLE-GROUP] sync-network set commProtocol: {cp}")
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE-GROUP] sync-network set commProtocol failed: {e}")

    try:
        net.lossless(True)
        log_func("[mscl-web] [SAMPLE-GROUP] sync-network set lossless: True")
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE-GROUP] sync-network set lossless failed: {e}")

    for node_id, node in zip(node_ids, nodes):
        net.addNode(node)
        log_func(f"[mscl-web] [SAMPLE-GROUP] sync-network addNode node_id={node_id}")

    try:
        net.refresh()
    except Exception:
        pass

    net.applyConfiguration()
    try:
        net_ok = bool(net.ok()) if callable(getattr(net, "ok", None)) else None
        net_bw = float(net.percentBandwidth()) if callable(getattr(net, "percentBandwidth", None)) else None
        log_func(
            f"[mscl-web] [SAMPLE-GROUP] sync-network info "
            f"node_ids={','.join(str(x) for x in node_ids)} net_ok={net_ok} net_bw={net_bw}"
        )
    except Exception as e:
        log_func(f"[mscl-web] [SAMPLE-GROUP] sync-network info read failed: {e}")

    if callable(getattr(net, "ok", None)) and (not bool(net.ok())):
        raise RuntimeError("SyncSamplingNetwork not OK after applyConfiguration")

    net.startSampling()
    return "sync-network-group"


def schedule_idle_after(
    *,
    node_id,
    seconds,
    token,
    state,
    log_func,
    internal_connect,
    ensure_beacon_on,
    node_state_info_fn,
    send_idle_fn,
    mscl_mod,
):
    if seconds <= 0:
        return
    time.sleep(seconds)
    with state.OP_LOCK:
        if state.SAMPLE_STOP_TOKENS.get(node_id) != token:
            return
        ok, msg = internal_connect()
        if not ok or state.BASE_STATION is None:
            log_func(f"[mscl-web] [SAMPLE] auto-idle skipped node_id={node_id}: {msg}")
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update(
                {
                    "auto_idle_confirmed": False,
                    "auto_idle_last_reason": msg,
                    "auto_idle_attempts": 0,
                    "auto_idle_at": int(time.time()),
                }
            )
            state.SAMPLE_RUNS[node_id] = run
            return
        try:
            ensure_beacon_on()
            node = mscl_mod.WirelessNode(node_id, state.BASE_STATION)
            node.readWriteRetries(15)

            idle_status = {}
            confirmed = False
            attempts_done = 0
            for attempt in range(1, 4):
                attempts_done = attempt
                idle_status = send_idle_fn(node, node_id, f"auto-idle#{attempt}")
                if bool(idle_status.get("state_confirmed")):
                    confirmed = True
                    break
                try:
                    _, st_txt, _ = node_state_info_fn(node)
                    if str(st_txt or "").strip().lower() == "idle":
                        confirmed = True
                        idle_status = dict(idle_status or {})
                        idle_status["state_confirmed"] = True
                        idle_status["reason"] = "confirmed by node state"
                        break
                except Exception:
                    pass
                if attempt < 3:
                    time.sleep(0.8 * attempt)

            run = state.SAMPLE_RUNS.get(node_id, {})
            if confirmed:
                run.update(
                    {
                        "state": "stopped",
                        "stopped_at": int(time.time()),
                        "idle_result": idle_status.get("idle_result"),
                        "stop_reason": "auto-idle",
                        "auto_idle_confirmed": True,
                        "auto_idle_last_reason": idle_status.get("reason"),
                        "auto_idle_attempts": attempts_done,
                        "auto_idle_at": int(time.time()),
                    }
                )
                state.SAMPLE_RUNS[node_id] = run
                log_func(
                    f"[mscl-web] [SAMPLE] auto-idle confirmed node_id={node_id} "
                    f"after {seconds}s attempts={attempts_done} reason={idle_status.get('reason')}"
                )
            else:
                run.update(
                    {
                        "auto_idle_confirmed": False,
                        "auto_idle_last_reason": idle_status.get("reason"),
                        "auto_idle_attempts": attempts_done,
                        "auto_idle_at": int(time.time()),
                    }
                )
                state.SAMPLE_RUNS[node_id] = run
                log_func(
                    f"[mscl-web] [SAMPLE] auto-idle not confirmed node_id={node_id} "
                    f"after {seconds}s attempts={attempts_done} reason={idle_status.get('reason')}"
                )
        except Exception as e:
            log_func(f"[mscl-web] [SAMPLE] auto-idle failed node_id={node_id}: {e}")
            run = state.SAMPLE_RUNS.get(node_id, {})
            run.update(
                {
                    "auto_idle_confirmed": False,
                    "auto_idle_last_reason": str(e),
                    "auto_idle_at": int(time.time()),
                }
            )
            state.SAMPLE_RUNS[node_id] = run


__all__ = [
    "schedule_idle_after",
    "send_idle_sensorconnect_style",
    "start_sampling_best_effort",
    "start_sampling_via_sync_network",
]
