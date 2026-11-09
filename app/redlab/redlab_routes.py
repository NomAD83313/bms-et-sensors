"""HTTP routes shared by legacy active-device and device-specific clients."""

from flask import jsonify, render_template, request

if __package__:
    from ..shared.redlab_channels import ChannelState, load_redlab_channels, save_redlab_channels
    from .redlab_acquisition import CHANNEL_COUNT
    from .redlab_runtime import _tc_type_labels
else:
    try:
        from redlab_channels import ChannelState, load_redlab_channels, save_redlab_channels
    except ImportError:
        from app.shared.redlab_channels import ChannelState, load_redlab_channels, save_redlab_channels
    from redlab_acquisition import CHANNEL_COUNT
    from redlab_runtime import _tc_type_labels


def register_routes(app, registry, read_settings, settings):
    channel_keys = [f"ch{i}" for i in range(CHANNEL_COUNT)]

    def _device_not_configurable(device_id: str):
        return jsonify(
            ok=False,
            configurable=False,
            device_id=device_id,
            error="device is detected but acquisition runtime is not attached yet",
        ), 409

    def _load_channel_state() -> ChannelState:
        return load_redlab_channels(settings.channel_state_path, channel_keys)

    def _save_channel_state(payload: dict) -> ChannelState:
        return save_redlab_channels(settings.channel_state_path, channel_keys, payload)

    @app.get("/")
    def index():
        return render_template("index.html", tc_types=_tc_type_labels())

    @app.get("/health")
    @app.get("/healthz")
    @app.get("/api/health")
    def health():
        return jsonify(registry.get_health())

    @app.get("/api/devices")
    def api_devices():
        try:
            devices = registry.device_inventory()
            active_device = next((device for device in devices if device.get("active")), None)
            return jsonify(
                ok=True,
                devices=devices,
                count=len(devices),
                active_device_id=registry.active_device_id() or (active_device.get("device_id") if active_device else None),
                active_device_ids=sorted(registry.running_runtime_ids()),
            )
        except Exception as exc:
            return jsonify(ok=False, devices=[], count=0, error=str(exc)), 500

    @app.get("/api/redlab/channels")
    def get_redlab_channels_state():
        return jsonify(ok=True, channels=_load_channel_state())

    @app.post("/api/redlab/channels")
    def set_redlab_channels_state():
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(ok=False, error="Expected JSON object"), 400
        return jsonify(ok=True, channels=_save_channel_state(payload))

    @app.get("/api/device/<device_id>/health")
    def device_health(device_id: str):
        runtime = registry.runtime_for_device(device_id)
        if runtime is not None:
            payload = runtime.health_snapshot()
            payload.update(ok=True, active=registry.device_is_active(device_id), configurable=True)
            return jsonify(payload)
        try:
            device = next((item for item in registry.device_inventory() if item.get("device_id") == device_id), None)
        except Exception as exc:
            return jsonify(ok=False, device_id=device_id, error=str(exc)), 500
        if device is None:
            return jsonify(ok=False, device_id=device_id, error="device not found"), 404
        return jsonify(
            ok=True,
            device_id=device_id,
            active=False,
            configurable=False,
            connected=True,
            status="standby",
            message="detected; acquisition runtime is not attached yet",
            device_name=device.get("product_name"),
            device_unique_id=device.get("unique_id"),
            device_fw_main=None,
        )

    @app.get("/api/config/tc")
    def get_tc_config():
        runtime = registry.active_runtime()
        if runtime is None:
            return jsonify(ok=False, error="device not connected")
        return jsonify(runtime.tc_config_payload())

    @app.post("/api/config/tc")
    def set_tc_config():
        payload = request.get_json(silent=True) or {}
        updates = payload.get("updates") or []
        runtime = registry.active_runtime()
        if runtime is None:
            return jsonify(ok=False, error="device not connected")
        return jsonify(runtime.apply_tc_updates(updates))

    @app.get("/api/device/<device_id>/config/tc")
    def get_device_tc_config(device_id: str):
        runtime = registry.runtime_for_device(device_id)
        if runtime is None:
            return _device_not_configurable(device_id)
        return jsonify(runtime.tc_config_payload())

    @app.post("/api/device/<device_id>/config/tc")
    def set_device_tc_config(device_id: str):
        runtime = registry.runtime_for_device(device_id)
        if runtime is None:
            return _device_not_configurable(device_id)
        payload = request.get_json(silent=True) or {}
        return jsonify(runtime.apply_tc_updates(payload.get("updates") or []))

    @app.get("/api/config/read_mode")
    def get_read_mode():
        return jsonify(ok=True, read_mode=read_settings.get_read_mode())

    @app.post("/api/config/read_mode")
    def set_read_mode():
        payload = request.get_json(silent=True) or {}
        mode = payload.get("read_mode")
        if not mode:
            return jsonify(ok=False, error="missing read_mode")
        val = str(mode).strip().lower()
        if val not in ("single", "list"):
            return jsonify(ok=False, error="invalid read_mode")
        read_settings.set_read_mode(val)
        registry.set_health(read_mode=val)
        return jsonify(ok=True, read_mode=read_settings.get_read_mode())

    @app.get("/api/device/<device_id>/config/read_mode")
    def get_device_read_mode(device_id: str):
        if registry.runtime_for_device(device_id) is None:
            return _device_not_configurable(device_id)
        return get_read_mode()

    @app.post("/api/device/<device_id>/config/read_mode")
    def set_device_read_mode(device_id: str):
        if registry.runtime_for_device(device_id) is None:
            return _device_not_configurable(device_id)
        return set_read_mode()

    @app.get("/api/config/loop_sleep")
    def get_loop_sleep():
        return jsonify(ok=True, loop_sleep_seconds=read_settings.get_loop_sleep())

    @app.post("/api/config/loop_sleep")
    def set_loop_sleep():
        payload = request.get_json(silent=True) or {}
        value = payload.get("loop_sleep_seconds")
        try:
            seconds = float(str(value).strip().replace(",", "."))
        except Exception:
            return jsonify(ok=False, error="invalid loop_sleep_seconds")
        if seconds < 0.1 or seconds > 60:
            return jsonify(ok=False, error="loop_sleep_seconds out of range (0.1-60)")
        read_settings.set_loop_sleep(seconds)
        registry.set_health(loop_sleep_seconds=seconds)
        return jsonify(ok=True, loop_sleep_seconds=read_settings.get_loop_sleep())

    @app.get("/api/device/<device_id>/config/loop_sleep")
    def get_device_loop_sleep(device_id: str):
        if registry.runtime_for_device(device_id) is None:
            return _device_not_configurable(device_id)
        return get_loop_sleep()

    @app.post("/api/device/<device_id>/config/loop_sleep")
    def set_device_loop_sleep(device_id: str):
        if registry.runtime_for_device(device_id) is None:
            return _device_not_configurable(device_id)
        return set_loop_sleep()

