"""RedLab service composition and executable entry point."""

import sys
import threading

from flask import Flask
from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

if __package__:
    from .redlab_registry import RedLabRegistry
    from .redlab_routes import register_routes
    from .redlab_settings import ReadSettings, RedLabSettings
else:
    from redlab_registry import RedLabRegistry
    from redlab_routes import register_routes
    from redlab_settings import ReadSettings, RedLabSettings

settings = RedLabSettings.from_env()
read_settings = ReadSettings(settings)
app = Flask(__name__)
HEALTH_PORT = settings.health_port
HEALTH_SERVER_LOCK = threading.Lock()
HEALTH_SERVER = None
HEALTH_SERVER_THREAD: threading.Thread | None = None


def start_health_server():
    global HEALTH_SERVER, HEALTH_SERVER_THREAD
    with HEALTH_SERVER_LOCK:
        if HEALTH_SERVER is not None:
            return
        from werkzeug.serving import make_server

        HEALTH_SERVER = make_server("0.0.0.0", HEALTH_PORT, app, threaded=True)
        HEALTH_SERVER_THREAD = threading.Thread(target=HEALTH_SERVER.serve_forever, daemon=True)
        HEALTH_SERVER_THREAD.start()
    print(f">>> RedLab server listening on :{HEALTH_PORT}/health")


def stop_health_server():
    global HEALTH_SERVER, HEALTH_SERVER_THREAD
    with HEALTH_SERVER_LOCK:
        server = HEALTH_SERVER
        thread = HEALTH_SERVER_THREAD
        HEALTH_SERVER = None
        HEALTH_SERVER_THREAD = None
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:
        print(f"!!! RedLab server shutdown warning: {exc}")
    if thread and thread.is_alive():
        thread.join(timeout=1.0)

registry = RedLabRegistry(settings=settings, read_settings=read_settings, before_restart=stop_health_server)
register_routes(app, registry, read_settings, settings)


def main():
    start_health_server()
    if not all([settings.token, settings.org, settings.bucket]):
        print("!!! Missing INFLUX_TOKEN / INFLUX_ORG / INFLUX_BUCKET")
        registry.set_health(status="degraded", connected=False, influx_ok=False, message="missing Influx credentials")
        sys.exit(1)

    client = InfluxDBClient(url=settings.url, token=settings.token, org=settings.org)
    registry.write_api = client.write_api(write_options=SYNCHRONOUS)
    registry.set_health(status="starting", influx_ok=True, message="influx configured")
    registry.run()


if __name__ == "__main__":
    main()
