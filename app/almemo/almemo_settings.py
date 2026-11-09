"""Environment configuration shared by the ALMEMO services."""

import os


ALMEMO_PORT_ENV = os.getenv("ALMEMO_PORT", "").strip()
ALMEMO_BAUD = int(os.getenv("ALMEMO_BAUD", "9600"))
ALMEMO_TIMEOUT_MS = int(os.getenv("ALMEMO_TIMEOUT_MS", "1500"))
ALMEMO_EOL = os.getenv("ALMEMO_EOL", "\\r\\n")
ALMEMO_PROBE_TIMEOUT_MS = int(os.getenv("ALMEMO_PROBE_TIMEOUT_MS", "2000"))
ALMEMO_HEALTH_PROBE_TIMEOUT_MS = int(os.getenv("ALMEMO_HEALTH_PROBE_TIMEOUT_MS", "1000"))
ALMEMO_DRAIN_MAX_SEC = float(os.getenv("ALMEMO_DRAIN_MAX_SEC", "1.2"))
ALMEMO_DRAIN_QUIET_SEC = float(os.getenv("ALMEMO_DRAIN_QUIET_SEC", "0.18"))
ALMEMO_LIVE_STALL_SEC = float(os.getenv("ALMEMO_LIVE_STALL_SEC", "12.0"))
ALMEMO_FAST_LIVE_STALL_SEC = float(os.getenv("ALMEMO_FAST_LIVE_STALL_SEC", "4.0"))
ALMEMO_MISSING_CACHE_SEC = float(os.getenv("ALMEMO_MISSING_CACHE_SEC", "2.0"))
ALMEMO_EXCLUDE_PORTS = os.getenv("ALMEMO_EXCLUDE_PORTS", "/dev/ttyMICROEPS*,/dev/ttyOPTRIS*,/dev/ttyPYRO*")
ALMEMO_EXCLUDE_USB_IDS = os.getenv("ALMEMO_EXCLUDE_USB_IDS", "10c4:834b")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086").strip()
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "").strip()
INFLUX_ORG = os.getenv("INFLUX_ORG", "").strip()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "").strip()
ALMEMO_MEASUREMENT = "almemo_live"
ALMEMO_DEVICE_NAME = os.getenv("ALMEMO_DEVICE_NAME", "almemo_logger").strip()
THREAD_JOIN_TIMEOUT = 4.0
HEALTH_CACHE_TTL = 10.0
