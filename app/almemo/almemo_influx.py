"""Bounded InfluxDB write queue shared by both ALMEMO streaming modes."""

from collections import deque
import logging
import threading
import time

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.rest import ApiException

if __package__:
    from . import almemo_settings as settings
else:
    import almemo_settings as settings


LOG = logging.getLogger(__name__)
MAX_PENDING_RECORDS = 256
IDLE_SECONDS = 30
RETRY_SECONDS = 1
WRITE_TIMEOUT_MS = 3000


class AlmemoInfluxWriter:
    def __init__(self):
        self._ready = threading.Condition()
        self._pending: deque[list[Point]] = deque(maxlen=MAX_PENDING_RECORDS)
        self._thread: threading.Thread | None = None
        self._writing = False
        self._dropped = 0
        self._errors = 0
        self._retrying = False

    def enqueue(self, points: list[Point]) -> None:
        with self._ready:
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append(points)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="almemo-influx", daemon=True)
                self._thread.start()
            self._ready.notify()

    def status(self) -> dict[str, int | bool]:
        with self._ready:
            return {
                "pending_records": len(self._pending) + int(self._writing),
                "dropped_records": self._dropped,
                "write_errors": self._errors,
                "retrying": self._retrying,
            }

    def _run(self) -> None:
        client = None
        write_api = None
        points = None
        try:
            while True:
                if points is None:
                    with self._ready:
                        if not self._ready.wait_for(lambda: self._pending, timeout=IDLE_SECONDS):
                            self._thread = None
                            return
                        points = self._pending.popleft()
                        self._writing = True

                try:
                    if client is None:
                        client = InfluxDBClient(
                            url=settings.INFLUX_URL, token=settings.INFLUX_TOKEN,
                            org=settings.INFLUX_ORG, timeout=WRITE_TIMEOUT_MS,
                        )
                    if write_api is None:
                        write_api = client.write_api(write_options=SYNCHRONOUS)
                    write_api.write(
                        bucket=settings.INFLUX_BUCKET, org=settings.INFLUX_ORG,
                        record=points, write_precision=WritePrecision.MS,
                    )
                except Exception as error:
                    rejected = isinstance(error, ApiException) and error.status in (400, 413, 422)
                    with self._ready:
                        first_error = not self._retrying
                        self._errors += 1
                        self._retrying = not rejected
                        if rejected:
                            self._dropped += 1
                            self._writing = False
                    if rejected:
                        LOG.warning("ALMEMO InfluxDB rejected a record (HTTP %s)", error.status)
                        points = None
                        continue
                    if first_error:
                        LOG.warning("ALMEMO InfluxDB write failed (%s); retrying", type(error).__name__)
                    time.sleep(RETRY_SECONDS)
                    continue

                with self._ready:
                    self._writing = False
                    recovered = self._retrying
                    self._retrying = False
                points = None
                if recovered:
                    LOG.info("ALMEMO InfluxDB writes resumed")
        finally:
            if client is not None:
                client.close()
