from flask import jsonify, request


def register_routes(app, ctx: dict):
    def register_page(path: str, view_name: str, endpoint: str):
        @app.route(path, endpoint=endpoint)
        def page():
            return ctx["render_index"](view_name, ctx["default_range"], ctx["default_refresh_sec"], ctx["allowed_ranges"])

    register_page("/", "all", "index")
    register_page("/redlab", "redlab", "index_redlab")
    register_page("/mscl", "mscl", "index_mscl")
    register_page("/matter", "matter", "index_matter")
    register_page("/almemo", "almemo", "index_almemo")
    register_page("/pyrometers", "pyrometers", "index_pyrometers")
    register_page("/messkluppe", "messkluppe", "index_messkluppe")

    @app.route("/api/health")
    def health():
        ok = all([ctx["influx_token"], ctx["influx_org"], ctx["influx_bucket"]])
        status = "ok" if ok else "degraded"
        return jsonify(
            success=True,
            status=status,
            influx_url=ctx["influx_url"],
            influx_org=ctx["influx_org"],
            influx_bucket=ctx["influx_bucket"],
            message="Influx credentials are set" if ok else "Missing Influx credentials",
        )

    @app.route("/api/redlab/channels", methods=["GET", "POST"])
    def redlab_channels():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify(success=False, error="Expected JSON object"), 400
            channels = ctx["save_redlab_channels"](
                ctx["redlab_channel_state_path"],
                ctx["redlab_channel_keys"],
                payload,
            )
            return jsonify(success=True, channels=channels)
        channels = ctx["load_redlab_channels"](
            ctx["redlab_channel_state_path"],
            ctx["redlab_channel_keys"],
        )
        return jsonify(success=True, channels=channels)

    @app.route("/api/dashboard")
    # Keep /api/data as a compatibility alias until external usage is verified.
    @app.route("/api/data")
    def dashboard_data():
        resolved, error = ctx["resolve_dashboard_request"](
            args=request.args,
            default_range=ctx["default_range"],
            allowed_ranges=ctx["allowed_ranges"],
            sample_presets=ctx["sample_presets"],
            max_points_per_series=ctx["max_points_per_series"],
            safe_range_fn=ctx["safe_range_fn"],
            safe_sample_key_fn=ctx["safe_sample_key_fn"],
            safe_target_points_fn=ctx["safe_target_points_fn"],
            parse_iso_ts_fn=ctx["parse_iso_ts_fn"],
            window_for_target_points_fn=ctx["window_for_target_points_fn"],
            estimated_points_for_window_fn=ctx["estimated_points_for_window_fn"],
        )
        if error is not None:
            body, status_code = error
            return jsonify(**body), status_code

        view_mode = str(resolved["view_mode"])
        if view_mode not in ctx["view_configs"]:
            view_mode = "all"
        range_key = str(resolved["range_key"])
        sample_key = str(resolved["sample_key"])
        target_points = int(resolved["target_points"])
        custom_from_raw = resolved["custom_from_raw"]
        custom_to_raw = resolved["custom_to_raw"]
        start_expr = str(resolved["start_expr"])
        stop_expr = resolved["stop_expr"]
        window_from_dt = resolved["window_from_dt"]
        window_to_dt = resolved["window_to_dt"]
        window = str(resolved["window"])
        sample_label = str(resolved["sample_label"])
        panels, panel_meta = ctx["load_dashboard_panels"](
            view_mode=view_mode,
            start_expr=start_expr,
            stop_expr=stop_expr,
            window=window,
            load_mscl_series_fn=ctx["load_mscl_series_fn"],
            load_redlab_series_fn=ctx["load_redlab_series_fn"],
            load_almemo_series_fn=ctx["load_almemo_series_fn"],
            load_pyrometers_series_fn=ctx["load_pyrometers_series_fn"],
            load_messkluppe_series_fn=ctx["load_messkluppe_series_fn"],
            load_messkluppe_orientation_series_fn=ctx["load_messkluppe_orientation_series_fn"],
            load_messkluppe_battery_series_fn=ctx["load_messkluppe_battery_series_fn"],
            load_messkluppe_temperature_series_fn=ctx["load_messkluppe_temperature_series_fn"],
            load_matter_series_fn=ctx["load_matter_series_fn"],
            load_matter_humidity_series_fn=ctx["load_matter_humidity_series_fn"],
            load_matter_pressure_series_fn=ctx["load_matter_pressure_series_fn"],
            load_matter_pm_series_fn=ctx["load_matter_pm_series_fn"],
            load_matter_battery_series_fn=ctx["load_matter_battery_series_fn"],
            panel_raw_cadence_ms_fn=ctx["panel_raw_cadence_ms_fn"],
        )
        return jsonify(
            success=True,
            range=range_key,
            window=window,
            window_from_utc=ctx["to_iso_z_fn"](window_from_dt) if window_from_dt is not None else None,
            window_to_utc=ctx["to_iso_z_fn"](window_to_dt) if window_to_dt is not None else None,
            sample=sample_key,
            sample_label=sample_label,
            target_points=target_points,
            sample_options=[{"value": k, "label": str(v.get("label") or k)} for k, v in ctx["sample_presets"].items()],
            custom_from=custom_from_raw if range_key == "custom" else None,
            custom_to=custom_to_raw if range_key == "custom" else None,
            view=view_mode,
            panels=panels,
            panel_meta=panel_meta,
        )

    def resolve_export_or_error():
        resolved, error = ctx["resolve_export_request"](
            args=request.args,
            default_range=ctx["default_range"],
            allowed_ranges=ctx["allowed_ranges"],
            sample_presets=ctx["sample_presets"],
            max_points_per_series=ctx["max_points_per_series"],
            safe_range_fn=ctx["safe_range_fn"],
            safe_sample_key_fn=ctx["safe_sample_key_fn"],
            safe_target_points_fn=ctx["safe_target_points_fn"],
            parse_iso_ts_fn=ctx["parse_iso_ts_fn"],
            window_for_target_points_fn=ctx["window_for_target_points_fn"],
        )
        if error is not None:
            body, status_code = error
            return None, jsonify(**body), status_code
        return resolved, None, None

    @app.route("/api/export/mscl.csv")
    def export_mscl_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_mscl_series_fn"],
            naming_fn=lambda name: ctx["mscl_csv_column_name_fn"](name, ctx["mscl_source"], ctx["mscl_source_extra"]),
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=3,
            filename_prefix="mscl_temperature",
            column_prefix="value_",
        )

    @app.route("/api/export/redlab.csv")
    def export_redlab_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_redlab_series_fn"],
            naming_fn=ctx["redlab_csv_column_name_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=2,
            filename_prefix="redlab_temperature",
            column_prefix="value_",
        )

    @app.route("/api/export/almemo.csv")
    def export_almemo_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_almemo_series_fn"],
            naming_fn=ctx["almemo_csv_column_name_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=3,
            filename_prefix="almemo_live",
        )

    @app.route("/api/export/pyrometers.csv")
    def export_pyrometers_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_pyrometers_series_fn"],
            naming_fn=ctx["pyrometers_csv_column_name_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=3,
            filename_prefix="pyrometers_tobj",
        )

    @app.route("/api/export/matter.csv")
    def export_matter_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_matter_environment_series_fn"],
            naming_fn=ctx["matter_csv_column_name_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=2,
            filename_prefix="matter_environment",
        )

    @app.route("/api/export/messkluppe.csv")
    def export_messkluppe_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_single_export_response"](
            resolved=resolved,
            load_series_fn=ctx["load_messkluppe_series_fn"],
            naming_fn=ctx["messkluppe_csv_column_name_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            precision=3,
            filename_prefix="messkluppe_force",
        )

    @app.route("/api/export/all.csv")
    @app.route("/api/export/temperatures.csv")
    def export_temperatures_csv():
        resolved, error_response, status_code = resolve_export_or_error()
        if error_response is not None:
            return error_response, status_code
        return ctx["build_all_export_response"](
            resolved=resolved,
            load_mscl_series_fn=ctx["load_mscl_series_fn"],
            load_redlab_series_fn=ctx["load_redlab_series_fn"],
            load_pyrometers_series_fn=ctx["load_pyrometers_series_fn"],
            load_messkluppe_series_fn=ctx["load_messkluppe_series_fn"],
            load_matter_series_fn=ctx["load_matter_series_fn"],
            load_matter_environment_series_fn=ctx["load_matter_environment_series_fn"],
            append_series_rows_fn=ctx["append_series_rows_fn"],
            csv_export_response_fn=ctx["csv_export_response_fn"],
            mscl_csv_column_name_fn=lambda name: ctx["mscl_csv_column_name_fn"](name, ctx["mscl_source"], ctx["mscl_source_extra"]),
            redlab_csv_column_name_fn=ctx["redlab_csv_column_name_fn"],
            pyrometers_csv_column_name_fn=ctx["pyrometers_csv_column_name_fn"],
            messkluppe_csv_column_name_fn=ctx["messkluppe_csv_column_name_fn"],
            matter_csv_column_name_fn=ctx["matter_csv_column_name_fn"],
        )
