def build_health_payload(*, state, now, metric_snapshot_fn):
    connected = bool(state.BASE_STATION is not None)
    ping_age_sec = None
    if state.LAST_PING_OK_TS:
        ping_age_sec = max(0.0, now - float(state.LAST_PING_OK_TS))

    stream_pause_until = float(getattr(state, "STREAM_PAUSE_UNTIL", 0.0) or 0.0)
    stream_paused = now < stream_pause_until
    queue_depth = int(metric_snapshot_fn().get("stream_queue_depth", 0))

    status = "ok"
    reasons = []
    if not connected:
        status = "degraded"
        reasons.append("base_disconnected")
    if ping_age_sec is not None and ping_age_sec > float(state.PING_TTL_SEC):
        status = "degraded"
        reasons.append("ping_stale")
    if stream_paused:
        status = "degraded"
        reasons.append("stream_paused")

    return {
        "status": status,
        "ts": int(now),
        "connected": connected,
        "base_port": state.CURRENT_PORT,
        "ping_age_sec": round(ping_age_sec, 3) if ping_age_sec is not None else None,
        "ping_ttl_sec": float(state.PING_TTL_SEC),
        "stream_paused": bool(stream_paused),
        "stream_pause_remaining_sec": round(max(0.0, stream_pause_until - now), 3),
        "stream_queue_depth": queue_depth,
        "reasons": reasons,
    }
