"""HTTP interface for ALMEMO serial and streaming services."""

import os
import time
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
import serial

if __package__:
    from . import almemo_settings as settings, almemo_protocol as protocol
    from .almemo_serial import AlmemoSession
    from .almemo_streaming import AlmemoStreaming
else:
    import almemo_settings as settings
    import almemo_protocol as protocol
    from almemo_serial import AlmemoSession
    from almemo_streaming import AlmemoStreaming

app = Flask(__name__)
session = AlmemoSession()
streams = AlmemoStreaming(session)
session.streams = streams


def _no_cache_headers(content_type: str) -> dict[str, str]:
    return {
        "Content-Type": content_type,
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


@app.get("/")
def index():
    path = Path(__file__).with_name("ui.html")
    return path.read_text(encoding="utf-8"), 200, _no_cache_headers("text/html; charset=utf-8")


@app.get("/ui.js")
def ui_js():
    path = Path(__file__).with_name("ui.js")
    return path.read_text(encoding="utf-8"), 200, _no_cache_headers("application/javascript")


@app.get("/device-common.css")
def device_common_css():
    path = Path(__file__).with_name("device-common.css")
    return path.read_text(encoding="utf-8"), 200, _no_cache_headers("text/css")


def _cache_health_result(res: dict[str, Any]):
    enriched = dict(res)
    enriched["serial_baud"] = settings.ALMEMO_BAUD
    enriched["serial_flow_control"] = "XON/XOFF"
    with session.HEALTH_CACHE_LOCK:
        session._LAST_HEALTH_DATA = enriched
        session._LAST_HEALTH_AT = time.monotonic()
    return jsonify(enriched)


@app.get("/health")
@app.get("/healthz")
@app.get("/api/health")
def health():
    force_refresh = request.args.get("refresh") in ("1", "true")

    # Fast path: serve from cache if fresh enough
    with session.HEALTH_CACHE_LOCK:
        now = time.monotonic()
        if not force_refresh and session._LAST_HEALTH_DATA and (now - session._LAST_HEALTH_AT) < settings.HEALTH_CACHE_TTL:
            return jsonify(session._LAST_HEALTH_DATA)

    port = session._resolve_port()
    port_present = os.path.exists(port)
    if not port_present:
        return _cache_health_result({"status": "missing", "port": port, "port_present": False})

    recent_missing = session._recent_missing_reason(settings.ALMEMO_MISSING_CACHE_SEC)
    if recent_missing:
        return _cache_health_result({"status": "cable_only", "port": port, "port_present": True, "reason": recent_missing})

    if streams._live_is_running() and streams._live_data_fresh():
        return _cache_health_result({"status": "ok", "port": port, "port_present": True, "source": "live", "version": "V6/V7 (active)"})

    if streams._fast_live_is_running() and streams._fast_live_data_fresh():
        return _cache_health_result({"status": "ok", "port": port, "port_present": True, "source": "fast_live", "version": "V6/V7 (active)"})

    # Only one probe at a time — concurrent callers skip the probe and reuse cache or recent state.
    # This prevents multiple back-to-back G00+t0 bursts that corrupt XON/XOFF flow control.
    probe_acquired = session.HEALTH_PROBE_LOCK.acquire(blocking=False)
    if not probe_acquired:
        with session.HEALTH_CACHE_LOCK:
            if session._LAST_HEALTH_DATA:
                return jsonify(session._LAST_HEALTH_DATA)
        status = "ok" if session._device_recently_ok(max_age_sec=max(5.0, float(settings.ALMEMO_TIMEOUT_MS) / 300.0)) else "cable_only"
        return _cache_health_result({"status": status, "port": port, "port_present": True, "reason": "probe_busy"})

    try:
        # Re-check cache under probe lock: another thread may have just updated it
        with session.HEALTH_CACHE_LOCK:
            now = time.monotonic()
            if not force_refresh and session._LAST_HEALTH_DATA and (now - session._LAST_HEALTH_AT) < settings.HEALTH_CACHE_TTL:
                return jsonify(session._LAST_HEALTH_DATA)

        acquired = session.SERIAL_LOCK.acquire(timeout=3.0)
        if not acquired:
            status = "ok" if session._device_recently_ok(max_age_sec=max(5.0, float(settings.ALMEMO_TIMEOUT_MS) / 300.0)) else "cable_only"
            return _cache_health_result({"status": status, "port": port, "port_present": True, "reason": "serial_busy"})

        device_ok = False
        version = ""
        try:
            try:
                ser = session._get_pser()
                version, lines = session._probe_device(ser, max(600, settings.ALMEMO_HEALTH_PROBE_TIMEOUT_MS), full_reset=False)
                device_ok = bool(version)
                if device_ok:
                    session._mark_device_ok()
                else:
                    session._mark_device_missing("ALMEMO probe returned no device version")
            except Exception as exc:
                session._mark_device_missing(str(exc))
                session._close_pser()
        finally:
            session.SERIAL_LOCK.release()

        status = "ok" if device_ok else "cable_only"
        return _cache_health_result({"status": status, "port": port, "port_present": True, "version": version})
    finally:
        session.HEALTH_PROBE_LOCK.release()


@app.get("/api/version")
def api_version():
    response = session._send_command(
        "t0",
        timeout_ms=settings.ALMEMO_TIMEOUT_MS,
        read_lines=3,
        raw=False,
        eol=settings.ALMEMO_EOL,
    )
    version = protocol._extract_device_version(response.get("lines") or [])
    if version:
        response["version"] = version
    return jsonify(response)


@app.post("/api/command")
def api_command():
    payload = request.get_json(silent=True) or {}
    command = str(payload.get("command", "")).strip()
    timeout_ms = int(payload.get("timeout_ms", settings.ALMEMO_TIMEOUT_MS))
    read_lines = int(payload.get("read_lines", 0))
    raw = bool(payload.get("raw", False))
    return jsonify(
        session._send_command(
            command,
            timeout_ms=timeout_ms,
            read_lines=read_lines,
            raw=raw,
            eol=settings.ALMEMO_EOL,
        )
    )


@app.post("/api/command-sequence")
def api_command_sequence():
    payload = request.get_json(silent=True) or {}
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return jsonify({"ok": False, "error": "missing steps", "sequence": []})
    return jsonify(session._send_command_sequence(steps))


@app.get("/api/scan")
def api_scan():
    results: list[dict[str, Any]] = []
    version = session._get_protocol_version()
    # Scan channels 0-15 (basic range)
    for ch in range(0, 16):
        if version == "V7":
            channel = f"{ch}.0"
        else:
            channel = f"{ch:02d}"

        select_cmd = f"M{channel}"
        select_res = session._send_command(select_cmd, timeout_ms=settings.ALMEMO_TIMEOUT_MS, read_lines=0, raw=False)
        if not select_res.get("ok"):
            results.append({"channel": channel, "ok": False, "error": "select failed", "detail": select_res})
            continue
        read_res = session._send_command("p", timeout_ms=settings.ALMEMO_TIMEOUT_MS, read_lines=3, raw=False)
        lines = read_res.get("lines") or []
        data_lines = [line for line in lines if line.strip() and line.strip() != "p"]
        results.append(
            {
                "channel": channel,
                "ok": read_res.get("ok", False),
                "lines": lines,
                "data": data_lines,
                "raw": read_res.get("raw", ""),
            }
        )
    return jsonify({"ok": True, "results": results})


@app.get("/api/p15")
def api_p15():
    res = session._send_command("P15", timeout_ms=settings.ALMEMO_TIMEOUT_MS * 3, read_lines=60, raw=False)
    lines = res.get("lines") or []
    active: list[dict[str, str]] = []
    for line in lines:
        text = line.strip()
        if len(text) < 3:
            continue
        # V6: "01: ...", V7: "001.0: ..." or "0.0: ..."
        if ":" not in text:
            continue
        parts = text.split(":", 1)
        chan_part = parts[0].strip()
        if chan_part.replace(".", "").isdigit():
            active.append({"channel": chan_part, "line": text})
    return jsonify({"ok": res.get("ok", False), "response": res, "active": active})


@app.get("/api/read")
def api_read():
    channel = str(request.args.get("channel", "")).strip()
    with_time = request.args.get("with_time", "0") in ("1", "true", "yes")
    read_lines = int(request.args.get("read_lines", "0"))
    if not channel:
        return jsonify({"ok": False, "error": "missing channel"})

    version = session._get_protocol_version()
    if "." in channel:
        select_cmd = f"M{channel}"
    else:
        if version == "V7":
            # V7 uses xxx.x format
            select_cmd = f"M{channel}.0"
        else:
            select_cmd = f"M{channel.zfill(2)}"

    select_res = session._send_command(select_cmd, timeout_ms=settings.ALMEMO_TIMEOUT_MS, read_lines=0, raw=False)
    if not select_res.get("ok"):
        return jsonify({"ok": False, "error": "select failed", "detail": select_res})

    read_cmd = "P01" if with_time else "p"
    read_res = session._send_command(read_cmd, timeout_ms=settings.ALMEMO_TIMEOUT_MS, read_lines=read_lines, raw=False)
    lines = read_res.get("lines") or []
    data_lines = [line for line in lines if line.strip() and line.strip() != read_cmd]
    fallback = None
    if not data_lines and not with_time:
        fallback = session._send_command("P35", timeout_ms=settings.ALMEMO_TIMEOUT_MS, read_lines=read_lines, raw=False)
    return jsonify(
        {
            "ok": read_res.get("ok", False),
            "select": select_cmd,
            "read": read_cmd,
            "response": read_res,
            "fallback": fallback,
        }
    )


@app.post("/api/live/start")
def api_live_start():
    payload = request.get_json(silent=True) or {}
    cycle = protocol._normalize_live_cycle(str(payload.get("cycle", streams.LIVE_CYCLE)))
    with session.SESSION_SWITCH_LOCK:
        if streams._fast_live_is_running():
            streams._stop_stream(streams.FAST_LIVE_THREAD, streams.FAST_LIVE_STOP)
        if streams._live_is_running():
            if streams.LIVE_CYCLE == cycle and streams._live_data_fresh():
                return jsonify({"ok": True, "running": True, "cycle": streams.LIVE_CYCLE})
            streams._stop_stream(streams.LIVE_THREAD, streams.LIVE_STOP)
        streams._start_live_stream(cycle)
        return jsonify({"ok": True, "running": True, "cycle": streams.LIVE_CYCLE})


@app.post("/api/live/stop")
def api_live_stop():
    with session.SESSION_SWITCH_LOCK:
        if streams._live_is_running():
            streams._stop_stream(streams.LIVE_THREAD, streams.LIVE_STOP)
        stopping = streams._live_is_running()
        return jsonify({"ok": True, "running": False, "stopping": stopping, "cycle": streams.LIVE_CYCLE})


@app.get("/api/live/poll")
def api_live_poll():
    with streams.LIVE_LOCK:
        lines = list(streams.LIVE_LINES)
        streams.LIVE_LINES.clear()
    return jsonify({"ok": True, "lines": lines, "running": streams._live_is_running(), "cycle": streams.LIVE_CYCLE})


@app.post("/api/fast-live/start")
def api_fast_live_start():
    payload = request.get_json(silent=True) or {}
    rate = str(payload.get("rate", "10")).strip()
    if rate not in {"2.5", "10"}:
        return jsonify({"ok": False, "error": "unsupported rate"})
    with session.SESSION_SWITCH_LOCK:
        if streams._live_is_running():
            streams._stop_stream(streams.LIVE_THREAD, streams.LIVE_STOP)
        if streams._fast_live_is_running():
            if streams.FAST_LIVE_RATE == rate and streams._fast_live_data_fresh():
                return jsonify({"ok": True, "running": True, "rate": streams.FAST_LIVE_RATE})
            streams._stop_stream(streams.FAST_LIVE_THREAD, streams.FAST_LIVE_STOP)
        streams._start_fast_live_stream(rate)
        return jsonify({"ok": True, "running": True, "rate": streams.FAST_LIVE_RATE})


@app.post("/api/fast-live/stop")
def api_fast_live_stop():
    with session.SESSION_SWITCH_LOCK:
        if streams._fast_live_is_running():
            streams._stop_stream(streams.FAST_LIVE_THREAD, streams.FAST_LIVE_STOP)
        stopping = streams._fast_live_is_running()
        return jsonify({"ok": True, "running": False, "stopping": stopping, "rate": streams.FAST_LIVE_RATE})


@app.get("/api/fast-live/poll")
def api_fast_live_poll():
    with streams.FAST_LIVE_LOCK:
        lines = list(streams.FAST_LIVE_LINES)
        streams.FAST_LIVE_LINES.clear()
    return jsonify({"ok": True, "lines": lines, "running": streams._fast_live_is_running(), "rate": streams.FAST_LIVE_RATE})


if __name__ == "__main__":
    if os.getenv("ALMEMO_STARTUP_CONNECT", "1") == "1":
        threading.Thread(target=session._startup_connect, daemon=True).start()
    app.run(host="0.0.0.0", port=3040)
