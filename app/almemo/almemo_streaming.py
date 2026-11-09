"""Live streams and InfluxDB record adaptation."""

import time
import threading
from collections import deque
from datetime import datetime, timezone

import serial
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

if __package__:
    from . import almemo_settings as settings, almemo_protocol as protocol
else:
    import almemo_settings as settings
    import almemo_protocol as protocol


class AlmemoStreaming:
    def __init__(self, session):
        self.session = session
        self.LIVE_LOCK = threading.Lock()
        self.LIVE_THREAD: threading.Thread | None = None
        self.LIVE_STOP = threading.Event()
        self.LIVE_LINES: deque[str] = deque(maxlen=400)
        self.LIVE_CYCLE = "000001"
        self.LIVE_LAST_DATA_AT = 0.0
        self.FAST_LIVE_LOCK = threading.Lock()
        self.FAST_LIVE_THREAD: threading.Thread | None = None
        self.FAST_LIVE_STOP = threading.Event()
        self.FAST_LIVE_LINES: deque[str] = deque(maxlen=800)
        self.FAST_LIVE_RATE = "10"
        self.FAST_LIVE_LAST_DATA_AT = 0.0

    def _live_is_running(self) -> bool:
        return self.LIVE_THREAD is not None and self.LIVE_THREAD.is_alive()

    def _join_thread(self, thread: threading.Thread | None, timeout_sec: float = settings.THREAD_JOIN_TIMEOUT) -> None:
        if thread is None:
            return
        deadline = time.monotonic() + max(0.2, timeout_sec)
        while thread.is_alive() and time.monotonic() < deadline:
            thread.join(timeout=0.2)

    def _stop_stream(self, thread: threading.Thread | None, stop_event: threading.Event) -> None:
        stop_event.set()
        self._join_thread(thread)

    def _mark_live_data(self, kind: str) -> None:
        now = time.monotonic()
        if kind == "live":
            self.LIVE_LAST_DATA_AT = now
        else:
            self.FAST_LIVE_LAST_DATA_AT = now
        self.session._mark_device_ok()

    def _live_data_fresh(self) -> bool:
        return self.LIVE_LAST_DATA_AT > 0.0 and (time.monotonic() - self.LIVE_LAST_DATA_AT) <= max(1.0, settings.ALMEMO_LIVE_STALL_SEC)

    def _fast_live_data_fresh(self) -> bool:
        return self.FAST_LIVE_LAST_DATA_AT > 0.0 and (time.monotonic() - self.FAST_LIVE_LAST_DATA_AT) <= max(
            1.0, settings.ALMEMO_FAST_LIVE_STALL_SEC
        )

    def _live_push(self, line: str) -> None:
        if not line:
            return
        if not line.startswith("ERROR"):
            self._mark_live_data("live")
        with self.LIVE_LOCK:
            self.LIVE_LINES.append(line)

    def _fast_live_is_running(self) -> bool:
        return self.FAST_LIVE_THREAD is not None and self.FAST_LIVE_THREAD.is_alive()

    def _fast_live_push(self, line: str) -> None:
        if not line:
            return
        if not line.startswith("ERROR"):
            self._mark_live_data("fast")
        with self.FAST_LIVE_LOCK:
            self.FAST_LIVE_LINES.append(line)

    def _is_fast_live_data_line(self, line: str) -> bool:
        text = line.strip()
        if not text:
            return False
        if text in {"G00", "f5", "ERROR"}:
            return False
        if text.startswith("\x03"):
            text = text.lstrip("\x03").strip()
        if text.startswith("k") and len(text) <= 3:
            return False

        version = self.session._get_protocol_version()

        if version == "V7":
            # V7 fast live lines look like "0.0;23.5;C"
            return ";" in text and text[0:1].isdigit()
        else:
            # V6 fast live lines look like "01: 23.5 C"
            if text.startswith(("10:", "01:", "00:", "02:", "03:", "04:", "05:", "06:", "07:", "08:", "09:")):
                return True
            return bool(text and text[0:2].isdigit() and ":" in text)

    def _is_print_live_data_line(self, line: str) -> bool:
        text = protocol._normalize_line_text(line).lstrip("\x03").strip()
        if not text:
            return False
        if text == "ERROR":
            return False
        if text in {"C11", "G00", "S2"}:
            return False
        if text.startswith(("f5", "Z")):
            return False
        if text.startswith(("DATE:", "TIME:")):
            return True
        if protocol._record_start(text):
            return True
        if self.session._get_protocol_version() == "V7" and ";" in text:
            return True
        return protocol.CHANNEL_VALUE_RE.search(text) is not None

    def _influx_enabled(self) -> bool:
        return bool(settings.INFLUX_TOKEN and settings.INFLUX_ORG and settings.INFLUX_BUCKET)

    def _record_timestamp(self) -> datetime:
        return datetime.now(timezone.utc)

    def _parse_record_points(self, record: str, mode: str) -> list[Point]:
        measurements = protocol.parse_measurements(record, self.session._get_protocol_version())
        if not measurements:
            return []
        ts = self._record_timestamp()
        points = []
        for measurement in measurements:
            point = (
                Point(settings.ALMEMO_MEASUREMENT)
                .tag("source", "almemo")
                .tag("device", settings.ALMEMO_DEVICE_NAME)
                .tag("mode", mode)
                .tag("channel", measurement.channel)
                .field("value", measurement.value)
                .time(ts, WritePrecision.MS)
            )
            if measurement.unit:
                point.tag("unit", measurement.unit)
            if measurement.sensor:
                point.tag("sensor", measurement.sensor)
            points.append(point)
        return points

    def _write_record_to_influx(self, write_api, record: str, mode: str) -> None:
        if write_api is None:
            return
        points = self._parse_record_points(record, mode)
        if not points:
            return
        write_api.write(
            bucket=settings.INFLUX_BUCKET,
            org=settings.INFLUX_ORG,
            record=points,
            write_precision=WritePrecision.MS,
        )

    def _reader_loop(self,
        stop_event: threading.Event,
        push_fn,
        setup_cmds: tuple,
        setup_sleep: float,
        cleanup_cmds: tuple,
        cleanup_sleep: float,
        mode: str,
        *,
        filter_fn=None,
        push_clean: bool = False,
        channel_only_write: bool = False,
        stall_timeout_sec: float = 0.0,
        stall_fallback_cmds: tuple = (),
    ) -> None:
        influx_client = None
        write_api = None
        current_record = ""
        try:
            if self._influx_enabled():
                influx_client = InfluxDBClient(url=settings.INFLUX_URL, token=settings.INFLUX_TOKEN, org=settings.INFLUX_ORG)
                write_api = influx_client.write_api(write_options=SYNCHRONOUS)
            while not stop_event.is_set():
                try:
                    ser, active_port = self.session._open_verified_serial(settings.ALMEMO_TIMEOUT_MS, xonxoff=True)
                    with ser:
                        self.session._log_serial_event(f"{mode}: reader attached to {active_port}")
                        for cmd in setup_cmds:
                            self.session._write_line(ser, cmd, sleep_sec=0.03)
                            time.sleep(setup_sleep)
                        current_record = ""
                        last_data_at = time.monotonic()
                        fallback_sent = False
                        while not stop_event.is_set():
                            try:
                                raw = ser.readline()
                            except serial.SerialException as exc:
                                push_fn(f"ERROR: {exc}")
                                break
                            if not raw:
                                if stall_timeout_sec > 0 and (time.monotonic() - last_data_at) >= stall_timeout_sec:
                                    if stall_fallback_cmds and not fallback_sent:
                                        for cmd in stall_fallback_cmds:
                                            self.session._write_line(ser, cmd, sleep_sec=0.03)
                                            time.sleep(setup_sleep)
                                        fallback_sent = True
                                        last_data_at = time.monotonic()
                                        self.session._log_serial_event(f"{mode}: fallback command(s) sent after data stall")
                                        continue
                                    message = (
                                        f"ERROR: no ALMEMO data for {stall_timeout_sec:.1f}s on {active_port}; "
                                        "reconnecting"
                                    )
                                    push_fn(message)
                                    self.session._log_serial_event(message)
                                    break
                                continue
                            last_data_at = time.monotonic()
                            line = raw.decode("latin-1", errors="replace").strip()
                            if not line:
                                continue
                            if filter_fn is not None and not filter_fn(line):
                                continue
                            fallback_sent = False
                            clean_line = line.replace("\r", "").lstrip("\x03").strip()
                            push_fn(clean_line if push_clean else line)
                            if protocol._record_start(clean_line):
                                if current_record:
                                    self._write_record_to_influx(write_api, current_record, mode)
                                current_record = clean_line
                            elif current_record:
                                current_record = f"{current_record} {clean_line}".strip()
                            elif channel_only_write:
                                self._write_record_to_influx(write_api, clean_line, mode)
                        try:
                            for cmd in cleanup_cmds:
                                self.session._write_line(ser, cmd, sleep_sec=0.03)
                                if cleanup_sleep:
                                    time.sleep(cleanup_sleep)
                            # drain until device goes quiet (ensures S2 stopped before port closes)
                            self.session._discard_input_until_quiet(ser, max_sec=1.5, quiet_sec=0.15)
                        except Exception:
                            pass
                except serial.SerialException as exc:
                    push_fn(f"ERROR: {exc}")
                    self.session._log_serial_event(f"{mode}: reconnect failed: {exc}")
                    time.sleep(0.5)
        finally:
            if current_record:
                try:
                    self._write_record_to_influx(write_api, current_record, mode)
                except Exception:
                    pass
            if influx_client is not None:
                influx_client.close()
            stop_event.clear()

    def _live_reader(self, cycle: str) -> None:
        version = self.session._get_protocol_version()
        setup = protocol._live_setup_commands(version, cycle)
        # V6 ALMEMO 2490: M00 alone is not enough to recover a frozen print cycle.
        # Full stop (C11) + channel select (G00) + restart (S2) + measurement
        # select (M00) mirrors the original setup and reliably restarts the cycle
        # without needing a full serial reconnect.
        fallback = ("C11", "G00", f"Z{cycle}", "S2", "M00") if version == "V6" else ()

        with self.session.SERIAL_LOCK:
            self.session._close_pser()
            self._reader_loop(
                self.LIVE_STOP,
                self._live_push,
                setup_cmds=setup,
                setup_sleep=0.15,
                cleanup_cmds=("C11",),
                cleanup_sleep=0.0,
                mode="print_cycle",
                filter_fn=self._is_print_live_data_line,
                push_clean=True,
                stall_timeout_sec=max(1.0, settings.ALMEMO_LIVE_STALL_SEC),
                stall_fallback_cmds=fallback,
            )

    def _fast_live_reader(self, rate: str) -> None:
        with self.session.SERIAL_LOCK:
            self.session._close_pser()
            self._reader_loop(
                self.FAST_LIVE_STOP,
                self._fast_live_push,
                setup_cmds=("G00", protocol._fast_live_rate_command(rate), "f5 k2", "f5 k5"),
                setup_sleep=0.12,
                cleanup_cmds=("f5 k-5", "f5 k-2"),
                cleanup_sleep=0.05,
                mode="continuous_query",
                filter_fn=self._is_fast_live_data_line,
                push_clean=True,
                channel_only_write=True,
                stall_timeout_sec=max(1.0, settings.ALMEMO_FAST_LIVE_STALL_SEC),
            )

    def _start_live_stream(self, cycle: str) -> None:
        with self.LIVE_LOCK:
            self.LIVE_LINES.clear()
        self.LIVE_CYCLE = cycle
        self.LIVE_LAST_DATA_AT = 0.0
        self.LIVE_STOP.clear()
        self.LIVE_THREAD = threading.Thread(target=self._live_reader, args=(self.LIVE_CYCLE,), daemon=True)
        self.LIVE_THREAD.start()

    def _start_fast_live_stream(self, rate: str) -> None:
        with self.FAST_LIVE_LOCK:
            self.FAST_LIVE_LINES.clear()
        self.FAST_LIVE_RATE = rate
        self.FAST_LIVE_LAST_DATA_AT = 0.0
        self.FAST_LIVE_STOP.clear()
        self.FAST_LIVE_THREAD = threading.Thread(target=self._fast_live_reader, args=(rate,), daemon=True)
        self.FAST_LIVE_THREAD.start()

    def _pause_streaming_sessions(self) -> dict[str, str]:
        state: dict[str, str] = {}
        if self._live_is_running():
            state["live"] = self.LIVE_CYCLE
            self._stop_stream(self.LIVE_THREAD, self.LIVE_STOP)
        if self._fast_live_is_running():
            state["fast_live"] = self.FAST_LIVE_RATE
            self._stop_stream(self.FAST_LIVE_THREAD, self.FAST_LIVE_STOP)
        return state

    def _resume_streaming_sessions(self, state: dict[str, str]) -> None:
        if "live" in state and not self._live_is_running():
            self._start_live_stream(state["live"])
        if "fast_live" in state and not self._fast_live_is_running():
            self._start_fast_live_stream(state["fast_live"])
