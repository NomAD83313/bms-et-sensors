from typing import Any


def load_dashboard_panels(
    *,
    view_mode: str,
    start_expr: str,
    stop_expr: str | None,
    window: str,
    load_mscl_series_fn,
    load_redlab_series_fn,
    load_almemo_series_fn,
    load_pyrometers_series_fn,
    load_messkluppe_series_fn,
    load_messkluppe_orientation_series_fn,
    load_messkluppe_battery_series_fn,
    load_messkluppe_temperature_series_fn,
    load_matter_series_fn,
    load_matter_humidity_series_fn,
    load_matter_pressure_series_fn,
    load_matter_pm_series_fn,
    load_matter_battery_series_fn,
    panel_raw_cadence_ms_fn,
):
    raw_mode = (window == "__raw__")
    panels = {
        "mscl_temperature": [],
        "redlab_temperature": [],
        "almemo_live": [],
        "pyrometers_temperature": [],
        "messkluppe_force": [],
        "messkluppe_orientation": [],
        "messkluppe_battery": [],
        "messkluppe_temperatures": [],
        "matter_temperature": [],
        "matter_humidity": [],
        "matter_pressure": [],
        "matter_pm": [],
        "matter_battery": [],
    }
    panel_meta: dict[str, dict[str, Any]] = {
        key: {"raw_cadence_ms": None} for key in panels.keys()
    }

    if view_mode in {"all", "mscl"}:
        panels["mscl_temperature"] = load_mscl_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["mscl_temperature"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("mscl_temperature", start_expr, stop_expr)
    if view_mode in {"all", "redlab"}:
        panels["redlab_temperature"] = load_redlab_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["redlab_temperature"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("redlab_temperature", start_expr, stop_expr)
    if view_mode in {"all", "almemo"}:
        panels["almemo_live"] = load_almemo_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["almemo_live"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("almemo_live", start_expr, stop_expr)
    if view_mode in {"all", "pyrometers"}:
        panels["pyrometers_temperature"] = load_pyrometers_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["pyrometers_temperature"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("pyrometers_temperature", start_expr, stop_expr)
    if view_mode in {"all", "messkluppe"}:
        panels["messkluppe_force"] = load_messkluppe_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["messkluppe_force"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("messkluppe_force", start_expr, stop_expr)
        panels["messkluppe_orientation"] = load_messkluppe_orientation_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["messkluppe_orientation"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("messkluppe_orientation", start_expr, stop_expr)
        panels["messkluppe_battery"] = load_messkluppe_battery_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["messkluppe_battery"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("messkluppe_battery", start_expr, stop_expr)
        panels["messkluppe_temperatures"] = load_messkluppe_temperature_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["messkluppe_temperatures"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("messkluppe_temperatures", start_expr, stop_expr)
    if view_mode in {"all", "matter"}:
        panels["matter_temperature"] = load_matter_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["matter_temperature"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("matter_temperature", start_expr, stop_expr)
        panels["matter_humidity"] = load_matter_humidity_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["matter_humidity"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("matter_humidity", start_expr, stop_expr)
        panels["matter_pressure"] = load_matter_pressure_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["matter_pressure"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("matter_pressure", start_expr, stop_expr)
        panels["matter_pm"] = load_matter_pm_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["matter_pm"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("matter_pm", start_expr, stop_expr)
        panels["matter_battery"] = load_matter_battery_series_fn(start_expr, stop_expr, window, raw_mode)
        panel_meta["matter_battery"]["raw_cadence_ms"] = panel_raw_cadence_ms_fn("matter_battery", start_expr, stop_expr)
    return panels, panel_meta


def build_single_export_response(
    *,
    resolved: dict[str, Any],
    load_series_fn,
    naming_fn,
    append_series_rows_fn,
    csv_export_response_fn,
    precision: int,
    filename_prefix: str,
    column_prefix: str = "",
):
    range_key = str(resolved["range_key"])
    sample_key = str(resolved["sample_key"])
    start_expr = str(resolved["start_expr"])
    stop_expr = resolved["stop_expr"]
    window = str(resolved["window"])
    raw_mode = bool(resolved.get("raw_mode"))
    csv_delimiter = str(resolved.get("csv_delimiter") or ",")
    csv_decimal_separator = str(resolved.get("csv_decimal_separator") or ".")

    series_list = load_series_fn(start_expr, stop_expr, window, raw_mode)
    by_ts: dict[str, dict[str, Any]] = {}
    cols: set[str] = set()
    append_series_rows_fn(by_ts, cols, series_list, naming_fn)
    return csv_export_response_fn(
        by_ts=by_ts,
        cols=cols,
        precision=precision,
        filename_prefix=filename_prefix,
        sample_key=sample_key,
        range_key=range_key,
        column_prefix=column_prefix,
        delimiter=csv_delimiter,
        decimal_separator=csv_decimal_separator,
    )


def build_all_export_response(
    *,
    resolved: dict[str, Any],
    load_mscl_series_fn,
    load_redlab_series_fn,
    load_pyrometers_series_fn,
    load_messkluppe_series_fn,
    load_matter_series_fn,
    load_matter_environment_series_fn,
    append_series_rows_fn,
    csv_export_response_fn,
    mscl_csv_column_name_fn,
    redlab_csv_column_name_fn,
    pyrometers_csv_column_name_fn,
    messkluppe_csv_column_name_fn,
    matter_csv_column_name_fn,
):
    range_key = str(resolved["range_key"])
    sample_key = str(resolved["sample_key"])
    start_expr = str(resolved["start_expr"])
    stop_expr = resolved["stop_expr"]
    window = str(resolved["window"])
    raw_mode = bool(resolved.get("raw_mode"))
    csv_delimiter = str(resolved.get("csv_delimiter") or ",")
    csv_decimal_separator = str(resolved.get("csv_decimal_separator") or ".")

    mscl_series = load_mscl_series_fn(start_expr, stop_expr, window, raw_mode)
    redlab_series = load_redlab_series_fn(start_expr, stop_expr, window, raw_mode)
    pyrometers_series = load_pyrometers_series_fn(start_expr, stop_expr, window, raw_mode)
    messkluppe_series = load_messkluppe_series_fn(start_expr, stop_expr, window, raw_mode)
    matter_series = load_matter_environment_series_fn(start_expr, stop_expr, window, raw_mode)

    by_ts: dict[str, dict[str, Any]] = {}
    cols: set[str] = set()
    append_series_rows_fn(by_ts, cols, mscl_series, mscl_csv_column_name_fn)
    append_series_rows_fn(by_ts, cols, redlab_series, lambda name: f"redlab_{redlab_csv_column_name_fn(name)}")
    append_series_rows_fn(by_ts, cols, pyrometers_series, pyrometers_csv_column_name_fn)
    append_series_rows_fn(by_ts, cols, messkluppe_series, messkluppe_csv_column_name_fn)
    append_series_rows_fn(by_ts, cols, matter_series, matter_csv_column_name_fn)
    return csv_export_response_fn(
        by_ts=by_ts,
        cols=cols,
        precision=3,
        filename_prefix="all_graphs",
        sample_key=sample_key,
        range_key=range_key,
        delimiter=csv_delimiter,
        decimal_separator=csv_decimal_separator,
    )
