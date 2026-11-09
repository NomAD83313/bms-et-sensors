"""Environment configuration and shared, thread-safe RedLab read settings."""

import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RedLabSettings:
    url: str = "http://influxdb:8086"
    token: str | None = None
    org: str | None = None
    bucket: str | None = None
    health_port: int = 8090
    active_unique_id: str = ""
    loop_sleep: float = 0.5
    read_mode: str = "list"
    discovery_interval: float = 5.0
    reenumeration_recovery: bool = True
    reenumeration_confirmations: int = 2
    reenumeration_debounce: float = 5.0
    reenumeration_cooldown: float = 60.0
    write_queue_max_batches: int = 600
    write_retry_seconds: float = 1.0
    channel_state_path: Path = Path("/runtime/redlab_channels.json")

    @classmethod
    def from_env(cls):
        return cls(
            token=os.getenv("INFLUX_TOKEN"), org=os.getenv("INFLUX_ORG"), bucket=os.getenv("INFLUX_BUCKET"),
            active_unique_id=os.getenv("REDLAB_ACTIVE_UNIQUE_ID", "").strip(),
            loop_sleep=float(os.getenv("REDLAB_LOOP_SLEEP", "0.5")),
            read_mode=os.getenv("REDLAB_READ_MODE", "list").strip().lower(),
            discovery_interval=float(os.getenv("REDLAB_DISCOVERY_INTERVAL", "5.0")),
            reenumeration_recovery=os.getenv("REDLAB_REENUMERATION_RECOVERY", "1").strip().lower() not in {"0", "false", "no", "off"},
            reenumeration_confirmations=max(1, int(os.getenv("REDLAB_REENUMERATION_CONFIRMATIONS", "2"))),
            reenumeration_debounce=max(0.0, float(os.getenv("REDLAB_REENUMERATION_DEBOUNCE_SECONDS", "5.0"))),
            reenumeration_cooldown=max(0.0, float(os.getenv("REDLAB_REENUMERATION_RESTART_COOLDOWN_SECONDS", "60.0"))),
            write_queue_max_batches=max(1, int(os.getenv("REDLAB_WRITE_QUEUE_MAX_BATCHES", "600"))),
            write_retry_seconds=max(0.1, float(os.getenv("REDLAB_WRITE_RETRY_SECONDS", "1.0"))),
        )


class ReadSettings:
    """Read cadence is shared by all USB-TC runtimes, including device routes."""

    def __init__(self, settings: RedLabSettings):
        self.lock = threading.Lock()
        self.read_mode = settings.read_mode if settings.read_mode in {"single", "list"} else "list"
        self.loop_sleep = settings.loop_sleep

    def get_read_mode(self) -> str:
        with self.lock:
            return self.read_mode

    def set_read_mode(self, mode: str) -> None:
        value = (mode or "").strip().lower()
        if value in {"single", "list"}:
            with self.lock:
                self.read_mode = value

    def get_loop_sleep(self) -> float:
        with self.lock:
            return float(self.loop_sleep)

    def set_loop_sleep(self, seconds: float) -> None:
        with self.lock:
            self.loop_sleep = seconds
