def run_write_retry_loop(
    *,
    node_id,
    max_attempts,
    log_func,
    sleep_fn,
    internal_connect_fn,
    base_connected_fn,
    connected_attempt_fn,
    metric_inc_fn,
    mark_base_disconnected_fn,
):
    last_err = None
    last_was_eeprom = False

    for attempt in range(1, int(max_attempts) + 1):
        if attempt > 1:
            log_func(f"[mscl-web] Write retry {attempt}/{max_attempts} node_id={node_id}")

        # If last attempt failed with EEPROM read error, pause before retry.
        if last_was_eeprom:
            backoff = min(4.0, 0.5 * (2 ** (attempt - 1)))
            sleep_fn(backoff)

        ok, msg = internal_connect_fn()
        if not ok or not base_connected_fn():
            last_err = f"Base station not connected: {msg}"
            log_func(f"[mscl-web] Write failed: {last_err}")
            sleep_fn(0.5)
            continue

        try:
            response = connected_attempt_fn()
            return {"response": response, "error": None}
        except Exception as e:
            last_err = str(e)
            log_func(f"[mscl-web] Write error node_id={node_id}: {e}")

            # If the node returned an EEPROM read error, keep the base connection and retry.
            last_was_eeprom = "EEPROM" in last_err
            if last_was_eeprom:
                metric_inc_fn("eeprom_retries_write")
            else:
                mark_base_disconnected_fn()

            sleep_fn(0.5)
            continue

    return {"response": None, "error": last_err or "Write failed"}
