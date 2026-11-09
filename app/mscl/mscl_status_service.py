from datetime import datetime, timezone


def trim_ts(value):
    try:
        s = str(value)
        if "." in s:
            return s.split(".")[0]
        return s
    except Exception:
        return value


def comm_age_sec(value, now):
    if not value:
        return None
    try:
        s = str(value).split(".")[0]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        ts_local = dt.timestamp()
        ts_utc = dt.replace(tzinfo=timezone.utc).timestamp()
        return min(abs(now - ts_local), abs(now - ts_utc))
    except Exception:
        return None


def read_base_info(base_station):
    info = {
        "base_model": None,
        "base_fw": None,
        "base_serial": None,
        "base_region": None,
        "base_radio": None,
        "base_last_comm": None,
        "base_link": None,
    }
    if base_station is None:
        return info

    try:
        info["base_model"] = str(base_station.model())
    except Exception:
        pass
    try:
        info["base_fw"] = str(base_station.firmwareVersion())
    except Exception:
        pass
    try:
        info["base_serial"] = str(base_station.serial())
    except Exception:
        try:
            info["base_serial"] = str(base_station.serialNumber())
        except Exception:
            pass
    try:
        info["base_region"] = str(base_station.regionCode())
    except Exception:
        pass
    try:
        info["base_radio"] = str(base_station.frequency())
    except Exception:
        pass
    try:
        info["base_last_comm"] = trim_ts(base_station.lastCommunicationTime())
    except Exception:
        pass
    try:
        info["base_link"] = str(base_station.lastDeviceState())
    except Exception:
        pass

    return info


def compute_link_health(*, ping_age_sec, comm_age_sec_value, ping_ttl_sec):
    link_health = "offline"
    link_health_reason = "No active BaseStation object"

    if ping_age_sec is not None and ping_age_sec <= ping_ttl_sec:
        link_health = "healthy"
        link_health_reason = f"Ping fresh ({ping_age_sec:.1f}s)"
    elif ping_age_sec is not None and ping_age_sec <= (ping_ttl_sec * 3):
        link_health = "degraded"
        link_health_reason = f"Ping stale ({ping_age_sec:.1f}s)"
    elif ping_age_sec is not None:
        link_health = "offline"
        link_health_reason = f"No fresh ping ({ping_age_sec:.1f}s)"
    else:
        link_health = "degraded"
        link_health_reason = "No successful ping yet"

    if comm_age_sec_value is not None:
        if comm_age_sec_value > 120:
            link_health = "offline"
            link_health_reason = f"No base comm {comm_age_sec_value:.0f}s"
        elif comm_age_sec_value > 30 and link_health == "healthy":
            link_health = "degraded"
            link_health_reason = f"Base comm stale {comm_age_sec_value:.0f}s"

    return link_health, link_health_reason


def build_status_payload(state, now):
    ok = state.BASE_STATION is not None
    msg = state.LAST_BASE_STATUS.get("message", "Not connected")
    port = state.LAST_BASE_STATUS.get("port", "N/A")

    info = read_base_info(state.BASE_STATION)

    ping_age_sec = None
    if ok and float(getattr(state, "LAST_PING_OK_TS", 0.0) or 0.0) > 0:
        ping_age_sec = max(0.0, now - float(state.LAST_PING_OK_TS))

    comm_age = comm_age_sec(info.get("base_last_comm"), now) if ok else None

    if ok:
        link_health, link_health_reason = compute_link_health(
            ping_age_sec=ping_age_sec,
            comm_age_sec_value=comm_age,
            ping_ttl_sec=float(state.PING_TTL_SEC),
        )
    else:
        link_health, link_health_reason = ("offline", "No active BaseStation object")

    return {
        "connected": bool(ok),
        "port": port,
        "message": msg,
        "beacon_state": state.BASE_BEACON_STATE,
        "base_connection": f"Serial, {port}, {state.BAUDRATE}" if port and port != "N/A" else None,
        "ts": state.LAST_BASE_STATUS.get("ts"),
        "base_model": info.get("base_model"),
        "base_fw": info.get("base_fw"),
        "base_serial": info.get("base_serial"),
        "base_region": info.get("base_region"),
        "base_radio": info.get("base_radio"),
        "base_last_comm": info.get("base_last_comm"),
        "base_link": info.get("base_link"),
        "link_health": link_health,
        "link_health_reason": link_health_reason,
        "ping_age_sec": round(ping_age_sec, 2) if ping_age_sec is not None else None,
        "comm_age_sec": round(comm_age, 2) if comm_age is not None else None,
    }
