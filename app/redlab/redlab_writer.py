"""Bounded RedLab write queue and ordered InfluxDB retries."""

import threading
import time

if __package__:
    from .redlab_acquisition import build_points
else:
    from redlab_acquisition import build_points


class RedLabWriter:
    def __init__(self, device_id, unique_id, write_api, *, settings, read_settings, set_health):
        self.device_id = device_id
        self.unique_id = unique_id
        self.write_api = write_api
        self.settings = settings
        self.read_settings = read_settings
        self.set_health = set_health
        self.write_queue_lock = threading.Condition()
        self.writer_stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.write_queue: list[tuple[list[dict], str, int, float | None]] = []

    def start(self) -> None:
        self.writer_stop_event.clear()
        self.thread = threading.Thread(target=self.run, name=f"redlab-writer-{self.unique_id}", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.writer_stop_event.set()
        with self.write_queue_lock:
            self.write_queue_lock.notify_all()

    def _queued_batch_count(self) -> int:
        return sum(len(entry[0]) for entry in self.write_queue)


    def enqueue(self, batches: list[dict], message: str, skipped: int, loop_hz: float | None) -> int:
        dropped = 0
        with self.write_queue_lock:
            self.write_queue.append((list(batches), message, skipped, loop_hz))
            while self._queued_batch_count() > self.settings.write_queue_max_batches and self.write_queue:
                dropped += len(self.write_queue.pop(0)[0])
            depth = self._queued_batch_count()
            self.write_queue_lock.notify()
        if dropped:
            print(f"!!! RedLab write queue overflow for {self.device_id}: dropped {dropped} oldest batches")
        return depth


    def _write_batches(self, batches: list[dict]) -> list[str]:
        points = []
        last_log_data = []
        for batch in batches:
            pts, log_data = build_points(batch, self.device_id, state_path=self.settings.channel_state_path)
            points.extend(pts)
            last_log_data = log_data
        if points:
            self.write_api.write(self.settings.bucket, self.settings.org, points)
        return last_log_data


    def run(self) -> None:
        while True:
            with self.write_queue_lock:
                while not self.write_queue and not self.writer_stop_event.is_set():
                    self.write_queue_lock.wait(timeout=1.0)
                if not self.write_queue and self.writer_stop_event.is_set():
                    return
                batches, message, skipped, loop_hz = self.write_queue[0]
            try:
                last_log_data = self._write_batches(batches)
                with self.write_queue_lock:
                    if self.write_queue and self.write_queue[0][0] is batches:
                        self.write_queue.pop(0)
                    depth = self._queued_batch_count()
                if last_log_data:
                    print(f"Logged {self.device_id}: " + " | ".join(last_log_data))
                self.set_health(
                    status="running",
                    connected=True,
                    influx_ok=True,
                    last_ok_ts=int(time.time()),
                    last_channels=last_log_data,
                    message=message,
                    skipped_channels=skipped,
                    read_mode=self.read_settings.get_read_mode(),
                    loop_sleep_seconds=self.read_settings.get_loop_sleep(),
                    loop_hz=loop_hz,
                    write_queue_depth=depth,
                )
            except Exception as exc:
                with self.write_queue_lock:
                    depth = self._queued_batch_count()
                print(f"!!! InfluxDB Write Error for {self.device_id}: {exc}")
                self.set_health(
                    status="degraded",
                    connected=True,
                    influx_ok=False,
                    message=f"influx write retry pending: {exc}",
                    write_queue_depth=depth,
                )
                time.sleep(self.settings.write_retry_seconds)

