import csv
import io
import json
import time
from datetime import datetime, timezone
from typing import Any

_RETRIABLE_EXPORT_MARKERS = (
    "Failed to download data from the Node",
    "Failed to get the Datalog Session Info",
    "Failed to get the Datalogging Session Info",
    "EEPROM",
)


def _is_retriable_export_error(err_text: str) -> bool:
    message = str(err_text or "")
    return any(marker in message for marker in _RETRIABLE_EXPORT_MARKERS)


def execute_export_storage_connected(
    *,
    node_id: int,
    export_format: str,
    ingest_influx: bool,
    ui_from_raw: Any,
    ui_to_raw: Any,
    ui_window_from_ns: int | None,
    ui_window_to_ns: int | None,
    host_hours: float | None,
    state_module,
    mscl_mod,
    ensure_beacon_on_fn,
    pause_stream_reader_fn,
    send_idle_sensorconnect_style_fn,
    coerce_logged_sweeps_fn,
    logged_sweep_rows_fn,
    resolve_export_time_window_fn,
    filter_rows_by_host_window_fn,
    backfill_rows_to_influx_stream_fn,
    metric_inc_fn,
    log_func,
    source_node_export: str,
    jsonify_fn,
    response_cls,
    send_file_fn,
):
    pause_stream_reader_fn(4.0, f"export-storage node={node_id}")
    ensure_beacon_on_fn()
    base_station = state_module.BASE_STATION
    if base_station is None:
        return jsonify_fn(success=False, error="Base station not connected"), 503

    old_base_timeout = None
    old_base_retries = None
    try:
        old_base_timeout = int(base_station.timeout())
    except Exception:
        old_base_timeout = None
    try:
        old_base_retries = int(base_station.readWriteRetries())
    except Exception:
        old_base_retries = None

    try:
        base_station.timeout(max(int(old_base_timeout or 0), 4000))
    except Exception:
        pass
    try:
        base_station.readWriteRetries(max(int(old_base_retries or 0), 25))
    except Exception:
        pass

    rows: list[dict[str, Any]] = []
    sweep_count = 0
    session_count = 0
    last_download_err: Exception | None = None
    download_pct = -1.0

    try:
        for attempt in range(1, 6):
            pause_stream_reader_fn(6.0, f"export-storage attempt={attempt} node={node_id}")
            node = mscl_mod.WirelessNode(node_id, base_station)
            node.readWriteRetries(25)

            idle_status = send_idle_sensorconnect_style_fn(node, node_id, f"before-export-storage#{attempt}")
            idle_confirmed = bool(idle_status.get("state_confirmed"))
            if not idle_confirmed:
                last_download_err = RuntimeError(
                    f"Idle not confirmed before export attempt {attempt}: {idle_status.get('reason')}"
                )
                log_func(f"[mscl-web] [EXPORT-STORAGE] node_id={node_id}: {last_download_err}")
                if attempt < 5:
                    retry_sleep = min(8.0, 1.5 * float(attempt))
                    time.sleep(retry_sleep)
                    continue
                raise last_download_err

            settle_sec = 1.0 + (attempt * 0.5)
            time.sleep(settle_sec)

            try:
                node.ping()
            except Exception:
                pass

            session_count_read: int | None = None
            session_err = None
            for s_try in range(1, 4):
                try:
                    session_count_read = int(node.getNumDatalogSessions())
                    break
                except Exception as se:
                    session_err = str(se)
                    if "EEPROM" in session_err:
                        metric_inc_fn("eeprom_retries_read")
                    log_func(
                        f"[mscl-web] [EXPORT-STORAGE] getNumDatalogSessions failed "
                        f"node_id={node_id} attempt {attempt}/5 subtry {s_try}/3: {session_err}"
                    )
                    time.sleep(0.5 * s_try)

            if session_count_read is None:
                last_download_err = RuntimeError(
                    f"Failed to read datalog session count: {session_err or 'unknown error'}"
                )
                if attempt < 5:
                    continue
                raise last_download_err

            session_count = int(session_count_read)
            if session_count <= 0:
                return jsonify_fn(success=False, error="No datalog sessions on node storage"), 404

            try:
                downloader = mscl_mod.DatalogDownloader(node)
            except Exception as create_exc:
                last_download_err = create_exc
                err_txt = str(create_exc)
                log_func(f"[mscl-web] [EXPORT-STORAGE] attempt {attempt}/5 create failed node_id={node_id}: {err_txt}")
                retriable = _is_retriable_export_error(err_txt)
                if attempt < 5 and retriable:
                    retry_sleep = min(8.0, 1.5 * float(attempt))
                    time.sleep(retry_sleep)
                    continue
                raise

            rows = []
            sweep_count = 0
            safety_loops = 0
            transient_errors = 0
            consecutive_errors = 0
            retryable_download_break = None
            while not downloader.complete():
                safety_loops += 1
                if safety_loops > 20_000_000:
                    raise RuntimeError("Download aborted: safety loop limit reached")

                try:
                    batch = downloader.getNextData()
                    consecutive_errors = 0
                except Exception as dl_step_exc:
                    err_txt = str(dl_step_exc)
                    retriable = _is_retriable_export_error(err_txt)
                    if not retriable:
                        raise
                    transient_errors += 1
                    consecutive_errors += 1
                    if transient_errors % 10 == 0:
                        log_func(
                            f"[mscl-web] [EXPORT-STORAGE] transient errors node_id={node_id}: "
                            f"{transient_errors}, pct={downloader.percentComplete():.3f}, last={err_txt}"
                        )
                    if consecutive_errors >= 20 or transient_errors >= 400:
                        retryable_download_break = RuntimeError(
                            f"Too many transient download errors ({transient_errors}); last={err_txt}"
                        )
                        break
                    time.sleep(min(1.0, 0.08 * consecutive_errors))
                    continue

                sweeps = coerce_logged_sweeps_fn(batch)
                if not sweeps:
                    continue

                for sweep in sweeps:
                    sweep_count += 1
                    try:
                        session_index = int(downloader.sessionIndex())
                    except Exception:
                        session_index = None
                    try:
                        sample_rate_text = str(downloader.sampleRate())
                    except Exception:
                        sample_rate_text = ""
                    rows.extend(logged_sweep_rows_fn(node_id, session_index, sample_rate_text, sweep))

                if sweep_count % 500 == 0:
                    try:
                        pct = float(downloader.percentComplete())
                    except Exception:
                        pct = -1.0
                    download_pct = pct
                    log_func(
                        f"[mscl-web] [EXPORT-STORAGE] progress node_id={node_id} "
                        f"sweeps={sweep_count} points={len(rows)} pct={pct:.3f}"
                    )

            if retryable_download_break is not None:
                last_download_err = retryable_download_break
                if attempt < 5:
                    log_func(
                        f"[mscl-web] [EXPORT-STORAGE] retry node_id={node_id}: "
                        f"attempt {attempt}/5 failed ({last_download_err}); restarting downloader"
                    )
                    retry_sleep = min(8.0, 1.5 * float(attempt))
                    time.sleep(retry_sleep)
                    continue
                raise last_download_err

            if rows:
                try:
                    download_pct = float(downloader.percentComplete())
                except Exception:
                    pass
                break
            last_download_err = RuntimeError("No datapoints found in node datalog sessions")

        if last_download_err is not None and not rows:
            raise last_download_err
    finally:
        try:
            if old_base_timeout is not None:
                base_station.timeout(int(old_base_timeout))
        except Exception:
            pass
        try:
            if old_base_retries is not None:
                base_station.readWriteRetries(int(old_base_retries))
        except Exception:
            pass

    if not rows:
        return jsonify_fn(success=False, error="No datapoints found in node datalog sessions"), 404

    rows_before_window = int(len(rows))
    time_window_applied = False
    time_window_from_ns, time_window_to_ns, time_window_origin = resolve_export_time_window_fn(
        export_format=export_format,
        ui_window_from_ns=ui_window_from_ns,
        ui_window_to_ns=ui_window_to_ns,
        host_hours=host_hours,
        now_ns=time.time_ns(),
    )
    if time_window_from_ns is not None and time_window_to_ns is not None:
        rows = filter_rows_by_host_window_fn(
            rows=rows,
            window_from_ns=int(time_window_from_ns),
            window_to_ns=int(time_window_to_ns),
            time_offset_ns=0,
        )
        time_window_applied = True
        log_func(
            f"[mscl-web] [EXPORT-STORAGE] window-filter node_id={node_id} "
            f"origin={time_window_origin} points_before={rows_before_window} points_after={len(rows)} "
            f"offset_ns=0"
        )
        if not rows:
            return jsonify_fn(
                success=False,
                error="No datapoints in selected time window",
                window_origin=time_window_origin,
                ui_from=ui_from_raw,
                ui_to=ui_to_raw,
                host_hours=host_hours,
            ), 404

    backfill_written = 0
    backfill_skipped_existing = 0
    backfill_error = None
    if ingest_influx:
        try:
            bf_stats = backfill_rows_to_influx_stream_fn(
                node_id=node_id,
                rows=rows,
                source_tag=source_node_export,
            )
            backfill_written = int(bf_stats.get("written", 0))
            backfill_skipped_existing = int(bf_stats.get("skipped_existing", 0))
            metric_inc_fn("stream_write_calls")
            metric_inc_fn("stream_points_written", backfill_written)
            log_func(
                f"[mscl-web] [EXPORT-STORAGE] backfill node_id={node_id} "
                f"written={backfill_written} skipped_existing={backfill_skipped_existing} "
                f"offset_ns=0 skew_ns=0"
            )
        except Exception as bf_exc:
            backfill_error = str(bf_exc)
            log_func(f"[mscl-web] [EXPORT-STORAGE] backfill failed node_id={node_id}: {backfill_error}")

    exported_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"node_{node_id}_datalog_{exported_at}"
    log_func(
        f"[mscl-web] [EXPORT-STORAGE] success node_id={node_id} "
        f"sessions={session_count} sweeps={sweep_count} "
        f"download_pct={download_pct:.3f} points_before_window={rows_before_window} points={len(rows)} "
        f"format={export_format} ingest_influx={ingest_influx} "
        f"backfill_written={backfill_written} backfill_skipped_existing={backfill_skipped_existing} "
        f"time_window_applied={time_window_applied} time_window_origin={time_window_origin} "
        f"time_window_offset_ns=0 "
        f"host_hours={host_hours} ui_from={ui_from_raw} ui_to={ui_to_raw}"
    )

    def _attach_export_headers(resp):
        resp.headers["X-Influx-Backfill-Written"] = str(int(backfill_written))
        resp.headers["X-Influx-Backfill-Skipped-Existing"] = str(int(backfill_skipped_existing))
        if time_window_applied:
            if time_window_origin:
                resp.headers["X-Time-Window-Origin"] = str(time_window_origin)
            if time_window_from_ns is not None:
                resp.headers["X-Time-Window-From-Ns"] = str(int(time_window_from_ns))
            if time_window_to_ns is not None:
                resp.headers["X-Time-Window-To-Ns"] = str(int(time_window_to_ns))
            if ui_from_raw:
                resp.headers["X-UI-Window-From"] = str(ui_from_raw)
            if ui_to_raw:
                resp.headers["X-UI-Window-To"] = str(ui_to_raw)
            if host_hours is not None:
                resp.headers["X-Host-Window-Hours"] = str(host_hours)
        if backfill_error:
            resp.headers["X-Influx-Backfill-Error"] = str(backfill_error)[:180]
        return resp

    if export_format == "json":
        payload = {
            "node_id": int(node_id),
            "exported_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
            "session_count": int(session_count),
            "sweep_count": int(sweep_count),
            "point_count": int(len(rows)),
            "ingest_influx": bool(ingest_influx),
            "backfill_written": int(backfill_written),
            "backfill_skipped_existing": int(backfill_skipped_existing),
            "backfill_error": backfill_error,
            "time_window_applied": bool(time_window_applied),
            "time_window_origin": time_window_origin,
            "ui_from": ui_from_raw,
            "ui_to": ui_to_raw,
            "host_hours": host_hours,
            "time_window_offset_ns": 0,
            "rows": rows,
        }
        body = json.dumps(payload, ensure_ascii=False)
        resp = response_cls(
            body,
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={base_name}.json"},
        )
        return _attach_export_headers(resp)

    if export_format == "none":
        if ingest_influx and backfill_error:
            return jsonify_fn(
                success=False,
                error=f"Export to Influx failed: {backfill_error}",
                node_id=int(node_id),
                session_count=int(session_count),
                sweep_count=int(sweep_count),
                point_count=int(len(rows)),
                backfill_written=int(backfill_written),
                backfill_skipped_existing=int(backfill_skipped_existing),
            ), 502
        return jsonify_fn(
            success=True,
            node_id=int(node_id),
            session_count=int(session_count),
            sweep_count=int(sweep_count),
            point_count=int(len(rows)),
            ingest_influx=bool(ingest_influx),
            backfill_written=int(backfill_written),
            backfill_skipped_existing=int(backfill_skipped_existing),
            backfill_error=backfill_error,
            time_window_applied=bool(time_window_applied),
            time_window_origin=time_window_origin,
            ui_from=ui_from_raw,
            ui_to=ui_to_raw,
            host_hours=host_hours,
            time_window_offset_ns=0,
        )

    csv_columns = [
        "timestamp_utc",
        "timestamp_ns",
        "node_id",
        "session_index",
        "sample_rate",
        "channel",
        "channel_id",
        "value",
        "tick",
        "cal_applied",
    ]
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=csv_columns)
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = csv_buf.getvalue().encode("utf-8")
    resp = send_file_fn(
        io.BytesIO(csv_bytes),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=f"{base_name}.csv",
    )
    return _attach_export_headers(resp)


__all__ = ["execute_export_storage_connected"]
