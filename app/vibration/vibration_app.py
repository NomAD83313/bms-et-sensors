"""WitMotion WTVB01-485 vibration collector: RS485 polling, InfluxDB writes and UI/API."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Flask, Response, jsonify, request

try:
    from app.shared.csv_locale import resolve_csv_delimiter
    from . import vibration_config as cfg
    from . import vibration_fft as fft
    from . import vibration_sim as sim
    from . import vibration_records as records
    from . import vibration_burst as burst
    from . import wtvb01_protocol as proto
    from .vibration_bus import BusError, ModbusBus
except ImportError:  # Flat import inside the container.
    from csv_locale import resolve_csv_delimiter
    import vibration_config as cfg
    import vibration_fft as fft
    import vibration_sim as sim
    import vibration_records as records
    import vibration_burst as burst
    import wtvb01_protocol as proto
    from vibration_bus import BusError, ModbusBus

HTTP_PORT = int(os.getenv("VIBRATION_HTTP_PORT", "3090"))
DATA_DIR = Path(os.getenv("VIBRATION_DATA_DIR", "/data"))
CONFIG_PATH = DATA_DIR / "config.json"
RECORDS_DIR = DATA_DIR / "records"
MEASUREMENT = os.getenv("VIBRATION_INFLUX_MEASUREMENT", "wtvb01")
FFT_MEASUREMENT = os.getenv("VIBRATION_FFT_INFLUX_MEASUREMENT", "wtvb01_fft")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")
INFLUX_BATCH_SIZE = int(os.getenv("VIBRATION_INFLUX_BATCH_SIZE", "500"))
INFLUX_FLUSH_MS = int(os.getenv("VIBRATION_INFLUX_FLUSH_MS", "1000"))
OFFLINE_AFTER_FAILURES = 3
OFFLINE_RETRY_SEC = 5.0
PORT_RETRY_SEC = 2.0
SERIAL_DIRS = ("/dev/serial/by-id",)
SIM_PORT = "sim://frequency?hz=5.25"
SIM_FAULTS_PORT = "sim://frequency?hz=5.25&scenario=faults"
# The simulated ports are offered in the port list only for test sessions.
SIM_PORTS_LISTED = os.getenv("VIBRATION_SIM_PORTS", "0") == "1"
# Simulated sensors next to the real bus (vibration_sim.RollDriveSimulator): while a sensor
# with one of these addresses is registered, its frames go to the simulator instead of RS485.
# At most one runs at a time. Defaults are the two highest WTVB01 addresses.
SIM_SENSORS: dict[int, dict[str, str]] = {
    int(os.getenv("VIBRATION_SIM_SENSOR_ADDRESS", "127")): {
        "name": "sim-roll-600-defects", "profile": "defects", "label": "simulated roll: unbalance + bearing defect",
        "description": ("Simulated 600 mm roll on a direct drive: small unbalance, small outer-race defect in the "
                        "drive bearing, line speed 450/600 m/min with 5 s ramps every 30 s"),
    },
    int(os.getenv("VIBRATION_SIM_HEALTHY_ADDRESS", "126")): {
        "name": "sim-roll-600-healthy", "profile": "healthy", "label": "simulated roll: no defects",
        "description": ("Simulated 600 mm roll on a direct drive without defects: well balanced (residual 1x only), "
                        "healthy bearing, line speed 450/600 m/min with 5 s ramps every 30 s"),
    },
}
SIM_SENSOR_ADDRESS = next(iter(SIM_SENSORS))  # Default for POST /api/sim-sensor without an address.

app = Flask(__name__)

STATE_LOCK = threading.Lock()
WAKE = threading.Event()
BUS = ModbusBus()


def _load_initial() -> tuple[dict[str, Any], str | None]:
    return cfg.load(CONFIG_PATH)


CONFIG, CONFIG_ERROR = _load_initial()
SENSOR_STATE: dict[int, dict[str, Any]] = {}
BUS_STATE: dict[str, Any] = {"port_present": False, "open": False, "last_error": None, "cycle_ms": None, "cycles": 0}
# Last FIFO capture per sensor: full spectrum for the UI, summary for status and InfluxDB.
SPECTRA: dict[int, dict[str, Any]] = {}
# Monotonic time of the next periodic capture per sensor.
FFT_NEXT: dict[int, float] = {}
# Time waveforms and orbits of the last capture per sensor (in memory only).
WAVEFORMS: dict[int, dict[str, Any]] = {}
OPERATION: dict[str, Any] = {"name": None, "running": False, "started_at": None, "finished_at": None, "result": None, "error": None,
                             "progress": None}
# Set by POST /api/operation/stop; a capture series ends after the current capture.
STOP_REQUESTED = threading.Event()
# Safety limit for one continuous record (600 s at about 4.4 s per capture is about 136).
BURST_MAX_CAPTURES = 200
# Analyses of continuous records: (record id, file stamp, window) -> result.
BURST_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
BURST_CACHE_SIZE = 4
BURST_CACHE_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sensor_state(address: int) -> dict[str, Any]:
    state = SENSOR_STATE.get(address)
    if state is None:
        state = SENSOR_STATE[address] = {
            "online": False, "values": {}, "last_ok_at": None, "last_error": None,
            "reads": 0, "errors": 0, "rejected_blocks": 0, "invalid_shape_axes": 0, "consecutive_failures": 0, "next_retry": 0.0, "block_read_at": {},
        }
    return state


def _config_snapshot() -> dict[str, Any]:
    with STATE_LOCK:
        return cfg.validate(CONFIG)


def _sync_virtual(config: dict[str, Any]) -> None:
    """Attach each simulated sensor to the bus while it is registered, detach it otherwise."""
    registered = {s["address"] for s in config["sensors"]}
    with BUS.lock:
        for address, spec in SIM_SENSORS.items():
            if address in registered and address not in BUS.virtual:
                BUS.virtual[address] = sim.RollDriveSimulator(address, config["baud"], profile=spec["profile"])
            elif address not in registered:
                BUS.virtual.pop(address, None)


def _sim_sensors_status() -> list[dict[str, Any]]:
    result = []
    for address, spec in SIM_SENSORS.items():
        device = BUS.virtual.get(address)
        info = {"address": address, "name": spec["name"], "label": spec["label"], "description": spec["description"],
                "active": device is not None}
        if device is not None and hasattr(device, "status"):
            info.update(device.status())
        result.append(info)
    return result


def _sim_sensor_status(address: int) -> dict[str, Any]:
    return next(item for item in _sim_sensors_status() if item["address"] == address)


def _store_config(new_config: dict[str, Any]) -> dict[str, Any]:
    global CONFIG, CONFIG_ERROR
    saved = cfg.save(CONFIG_PATH, new_config)
    with STATE_LOCK:
        if (saved["fft_interval_sec"], saved["fft_periodic"]) != (CONFIG.get("fft_interval_sec"), CONFIG.get("fft_periodic")):
            FFT_NEXT.clear()  # Reschedule periodic captures with the new interval.
        CONFIG = saved
        CONFIG_ERROR = None
        known = {s["address"] for s in saved["sensors"]}
        for table in (SENSOR_STATE, SPECTRA, FFT_NEXT, WAVEFORMS):
            for address in list(table):
                if address not in known:
                    del table[address]
    _sync_virtual(saved)
    WAKE.set()
    return saved


# --- InfluxDB -------------------------------------------------------------------

def _influx_enabled() -> bool:
    return bool(INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET)


class InfluxSink:
    def __init__(self) -> None:
        self._client = None
        self._writer = None
        self.last_error: str | None = None

    def write(self, sensor: dict[str, Any], values: dict[str, float], observed: datetime,
              measurement: str = MEASUREMENT) -> None:
        if not _influx_enabled():
            return
        try:
            from influxdb_client import InfluxDBClient, Point, WritePrecision, WriteOptions

            if self._writer is None:
                self._client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
                self._writer = self._client.write_api(write_options=WriteOptions(
                    batch_size=max(1, INFLUX_BATCH_SIZE), flush_interval=max(100, INFLUX_FLUSH_MS),
                    jitter_interval=0, retry_interval=5_000, max_retries=3, max_retry_delay=30_000, exponential_base=2,
                ))
            point = Point(measurement).tag("source", "wtvb01").tag("sensor", sensor["name"]).tag(
                "address", f"0x{sensor['address']:02X}").time(observed, WritePrecision.NS)
            for key, value in values.items():
                point = point.field(key, float(value))
            self._writer.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
            self.last_error = None
        except Exception as exc:  # Keep polling even when InfluxDB is unavailable.
            self.last_error = str(exc)


INFLUX = InfluxSink()


# --- polling --------------------------------------------------------------------

def _block_names(first: int) -> set[str]:
    for block in (proto.BLOCK_BASIC, proto.BLOCK_STATS, proto.BLOCK_ENERGY):
        if block[0] == first:
            names = {f.name for f in block[2]}
            if first == proto.BLOCK_ENERGY[0]:
                names |= {f"energy_{a}_ratio_{r}" for a in "xyz" for r in ("2x_1x", "half_1x")}
            return names
    return set()


def _blocks_to_skip(address: int, profile: str, now: float) -> set[int]:
    """Blocks read recently enough that the sensor has not refreshed them yet."""
    with STATE_LOCK:
        read_at = _sensor_state(address)["block_read_at"]
        return {first for first, _count, _fields in proto.READ_PROFILES[profile]
                if first in read_at and now - read_at[first] < proto.BLOCK_REFRESH_SEC[first]}


def _record_success(address: int, profile: str, values: dict[str, float], skipped: set[int], now: float,
                    rejected: set[int] = frozenset(), invalid_axes: int = 0) -> None:
    """Merge fresh values; skipped and rejected blocks keep their last values for the UI.

    A rejected block is not marked as read, so the next cycle reads it again.
    """
    with STATE_LOCK:
        state = _sensor_state(address)
        stale = set(skipped) | set(rejected)
        kept = set().union(*(_block_names(first) for first in stale)) if stale else set()
        merged = {k: v for k, v in state["values"].items() if k in kept}
        merged.update(values)
        for first, _count, _fields in proto.READ_PROFILES[profile]:
            if first not in stale:
                state["block_read_at"][first] = now
        state.update(online=True, values=merged, last_ok_at=_now_iso(), last_error=None,
                     consecutive_failures=0, next_retry=0.0)
        state["reads"] += 1
        state["rejected_blocks"] += len(rejected)
        state["invalid_shape_axes"] += invalid_axes


def _record_failure(address: int, error: str, now: float) -> None:
    with STATE_LOCK:
        state = _sensor_state(address)
        state["errors"] += 1
        state["consecutive_failures"] += 1
        state["last_error"] = error
        if state["consecutive_failures"] >= OFFLINE_AFTER_FAILURES:
            state["online"] = False
            state["next_retry"] = now + OFFLINE_RETRY_SEC


def _due(address: int, now: float) -> bool:
    with STATE_LOCK:
        return _sensor_state(address)["next_retry"] <= now


def _set_bus_state(**kwargs: Any) -> None:
    with STATE_LOCK:
        BUS_STATE.update(kwargs)


def _port_exists(path: str) -> bool:
    return path.startswith(cfg.SIM_PORT_PREFIX) or os.path.exists(path)


def poll_cycle(config: dict[str, Any], sink: InfluxSink, clock: Callable[[], float] = time.monotonic,
               port_exists: Callable[[str], bool] | None = None) -> float:
    """Run one pass over the enabled sensors; returns the seconds to wait before the next pass."""
    port_exists = port_exists or _port_exists
    sensors = [s for s in config["sensors"] if s["enabled"]]
    if not config["port"] or not sensors:
        BUS.close()
        _set_bus_state(open=False, port_present=bool(config["port"]) and port_exists(config["port"]),
                       last_error=None if config["port"] else "no serial port selected")
        return 1.0
    if not port_exists(config["port"]):
        BUS.close()
        _set_bus_state(open=False, port_present=False, last_error=f"{config['port']} not present")
        return PORT_RETRY_SEC
    try:
        BUS.open(config["port"], config["baud"])
    except BusError as exc:
        _set_bus_state(open=False, port_present=True, last_error=str(exc))
        return PORT_RETRY_SEC
    _set_bus_state(open=True, port_present=True, last_error=None)
    started = clock()
    for sensor in sensors:
        now = clock()
        if not _due(sensor["address"], now):
            continue
        skipped = _blocks_to_skip(sensor["address"], config["read_profile"], now)
        rejected: set[int] = set()
        try:
            values = BUS.read_profile(sensor["address"], config["read_profile"], skip=skipped, rejected=rejected)
        except BusError as exc:
            _record_failure(sensor["address"], str(exc), now)
            if "serial I/O error" in str(exc):
                BUS.close()
                _set_bus_state(open=False, last_error=str(exc))
                return PORT_RETRY_SEC
            continue
        invalid_axes = proto.drop_invalid_shape_stats(values)
        _record_success(sensor["address"], config["read_profile"], values, skipped, now, rejected,
                        len(invalid_axes))
        # Only freshly read values are written; skipped blocks would repeat old samples.
        sink.write(sensor, values, datetime.now(timezone.utc))
    elapsed = clock() - started
    with STATE_LOCK:
        BUS_STATE["cycle_ms"] = round(elapsed * 1000, 1)
        BUS_STATE["cycles"] += 1
    return max(0.0, config["poll_interval_sec"] - elapsed)


def _fft_due(config: dict[str, Any], now: float) -> int | None:
    """Address of the first enabled, online sensor whose periodic capture is due."""
    interval = config["fft_interval_sec"]
    if not config["fft_periodic"] or not config["port"]:
        return None
    with STATE_LOCK:
        for sensor in config["sensors"]:
            address = sensor["address"]
            if not sensor["enabled"] or not SENSOR_STATE.get(address, {}).get("online"):
                continue
            due = FFT_NEXT.setdefault(address, now)
            if due <= now:
                FFT_NEXT[address] = now + interval
                return address
    return None


def _poll_loop() -> None:
    while True:
        try:
            config = _config_snapshot()
            due = None if OPERATION["running"] else _fft_due(config, time.monotonic())
            if due is not None and _start_operation(
                    "fft", lambda: _fft_work(due, periodic=True, save_record=config["fft_save_records"],
                                              note="Automatic timer capture")):
                delay = 1.0
            else:
                delay = 1.0 if OPERATION["running"] else poll_cycle(config, INFLUX)
        except Exception as exc:  # Never let the poller thread die.
            _set_bus_state(last_error=f"poller error: {exc}")
            delay = PORT_RETRY_SEC
        WAKE.wait(max(delay, 0.001))
        WAKE.clear()


# --- long operations (scan, address/baud changes) --------------------------------

def _start_operation(name: str, work: Callable[[], Any]) -> bool:
    with STATE_LOCK:
        if OPERATION["running"]:
            return False
        OPERATION.update(name=name, running=True, started_at=_now_iso(), finished_at=None, result=None, error=None,
                         progress=None)
        STOP_REQUESTED.clear()

    def runner() -> None:
        result, error = None, None
        try:
            result = work()
        except (BusError, ValueError) as exc:
            error = str(exc)
        except Exception as exc:
            error = f"unexpected error: {exc}"
        with STATE_LOCK:
            OPERATION.update(running=False, finished_at=_now_iso(), result=result, error=error)
        WAKE.set()

    threading.Thread(target=runner, name=f"vibration-{name}", daemon=True).start()
    return True


def _open_bus_for_operation(config: dict[str, Any]) -> None:
    if not config["port"]:
        raise BusError("select a serial port first")
    BUS.open(config["port"], config["baud"])


def _scan_work(config: dict[str, Any], first: int, last: int, bauds: list[int]) -> dict[str, Any]:
    with BUS.lock:
        _open_bus_for_operation(config)
        found = []
        try:
            for baud in bauds:
                BUS.switch_baud(baud)
                found += [{"address": a, "baud": baud} for a in BUS.scan(range(first, last + 1))]
        finally:
            BUS.switch_baud(config["baud"])
    return {"found": found, "range": [first, last], "bauds": bauds}


def _address_work(old: int, new: int) -> dict[str, Any]:
    config = _config_snapshot()
    with BUS.lock:
        _open_bus_for_operation(config)
        BUS.set_address(old, new)
    for sensor in config["sensors"]:
        if sensor["address"] == old:
            sensor["address"] = new
    _store_config(config)
    return {"old_address": old, "new_address": new}


def _baud_work(new_baud: int) -> dict[str, Any]:
    config = _config_snapshot()
    addresses = [s["address"] for s in config["sensors"]]
    if not addresses:
        raise ValueError("register sensors before changing the bus baud rate")
    with BUS.lock:
        _open_bus_for_operation(config)
        results = BUS.set_baud(addresses, new_baud)
        if not any(r == "ok" for r in results.values()):
            BUS.switch_baud(config["baud"])
            raise BusError(f"no sensor switched to {new_baud} baud: {results}")
    config["baud"] = new_baud
    _store_config(config)
    return {"baud": new_baud, "results": {f"0x{a:02X}": r for a, r in results.items()}}


def _round_list(values: list[float], digits: int) -> list[float]:
    return [round(v, digits) for v in values]


def _fft_work(address: int, periodic: bool = False, averages: int | None = None,
              save_record: bool = False, note: str = "") -> dict[str, Any]:
    """Capture the raw FIFO of one sensor `averages` times, compute the averaged
    spectrum and store the summary. Any failed capture fails the whole run.

    Every sensor on the bus is polled once between two captures of a series."""
    config = _config_snapshot()
    count = averages or config["fft_averages"]
    sensor = _registered(config, address)
    captures: list[dict[str, Any]] = []
    stopped = False
    try:
        with BUS.lock:
            _open_bus_for_operation(config)
            for index in range(count):
                if index and (STOP_REQUESTED.is_set() or (periodic and not _config_snapshot()["fft_periodic"])):
                    stopped = True  # Stop pressed during the series: keep what was captured.
                    break
                if index:
                    # One normal polling pass between captures keeps live values and the
                    # InfluxDB series going during a long series (the sensor is silent now).
                    poll_cycle(_config_snapshot(), INFLUX)
                    _open_bus_for_operation(config)  # The pass closes the port after an I/O error.
                with STATE_LOCK:
                    OPERATION["progress"] = f"capture {index + 1}/{count}"
                captures.append(_stamp_capture(BUS.capture_fifo(address)))
    except BusError as exc:
        with STATE_LOCK:
            SPECTRA.setdefault(address, {})["error"] = f"capture {len(captures) + 1}/{count}: {exc}"
        raise
    finally:
        # The next periodic capture counts from the end of this one, so polling runs in between.
        current = _config_snapshot()
        if current["fft_periodic"]:
            with STATE_LOCK:
                FFT_NEXT[address] = time.monotonic() + current["fft_interval_sec"]
    spectrum, waveform = _publish_series(sensor, captures, config, periodic)
    response = {"address": address, "sample_rate_hz": spectrum["sample_rate_hz"], "averages": len(captures), "stopped": stopped,
                "peaks": {axis: data["peaks"] for axis, data in spectrum["axes"].items()}}
    if save_record:
        response["record"] = _save_record(sensor, captures, spectrum, waveform, periodic, note)
    return response


def _registered(config: dict[str, Any], address: int) -> dict[str, Any]:
    sensor = next((s for s in config["sensors"] if s["address"] == address), None)
    if sensor is None:
        raise ValueError(f"sensor 0x{address:02X} is not registered")
    return sensor


def _series_rate(captures: list[dict[str, Any]]) -> tuple[float, float | None]:
    """Sample rate of a series: mean of the measured rates, else the default."""
    rates = [c["sample_rate"] for c in captures if c.get("sample_rate")]
    measured = sum(rates) / len(rates) if rates else None
    return measured or proto.FIFO_DEFAULT_SAMPLE_RATE, measured


def _spectrum_payload(result: dict[str, Any], captured_at: str, periodic: bool, measured: bool) -> dict[str, Any]:
    return {
        "captured_at": captured_at, "periodic": periodic, "error": None,
        "sample_rate_hz": round(result["sample_rate_hz"], 3), "sample_rate_measured": measured,
        "samples": result["samples"], "duration_s": round(result["duration_s"], 3), "averages": result["averages"],
        "df_hz": round(result["df_hz"], 5), "nyquist_hz": round(result["nyquist_hz"], 3),
        "velocity_min_hz": result["velocity_min_hz"],
        "axes": {axis: {"acc_g": _round_list(data["acc_g"], 6), "velocity_mm_s": _round_list(data["velocity_mm_s"], 4),
                        "peaks": [{"hz": round(f, 3), "g": round(a, 6)} for f, a in data["peaks"]],
                        "bands_g": {k: round(v, 6) for k, v in data["bands_g"].items()},
                        "velocity_rms_mm_s": round(data["velocity_rms_mm_s"], 4)}
                 for axis, data in result["axes"].items()},
        "summary": result["summary"],
    }


def _stamp_capture(capture: dict[str, Any]) -> dict[str, Any]:
    """Add the wall-clock time of the capture's first sample (`first_sample_at`, ISO UTC).

    The bus clock is time.monotonic; the request time plus the offset fitted from the
    progress frames is converted with the current monotonic/wall-clock pair."""
    first = float(capture.get("requested_at") or time.monotonic()) + float(capture.get("first_sample_s") or 0.0)
    wall = datetime.now(timezone.utc) - timedelta(seconds=time.monotonic() - first)
    capture["first_sample_at"] = wall.isoformat(timespec="milliseconds")
    return capture


def _capture_start(record: dict[str, Any], index: int) -> str | None:
    """Wall-clock first-sample time of capture `index` of a saved record.

    Records store it per capture; older continuous records only have the recording start
    (`started_at`, a few ms before the first sample) and the offsets `capture_starts_s`.
    Older ordinary records have no per-capture time (None)."""
    started = record.get("capture_started_at") or []
    if index < len(started) and started[index]:
        return started[index]
    offsets = record.get("capture_starts_s") or []
    if record.get("started_at") and index < len(offsets):
        try:
            base = datetime.fromisoformat(str(record["started_at"]))
            return (base + timedelta(seconds=float(offsets[index]))).isoformat(timespec="milliseconds")
        except (TypeError, ValueError):
            return None
    return None


def _waveform_payload(wave: dict[str, Any], captured_at: str, sample_rate: float,
                      started_at: str | None = None) -> dict[str, Any]:
    orbits = {pair: {kind: {k: round(v, 3) for k, v in values.items()} for kind, values in orbit.items()}
              for pair, orbit in wave["orbits"].items()}
    return {
        "captured_at": captured_at, "started_at": started_at, "sample_rate_hz": round(sample_rate, 3), "samples": wave["samples"],
        "integration_min_hz": round(wave["integration_min_hz"], 3),
        "axes": {axis: {key: _round_list(values, 5 if key == "acc_g" else 2) for key, values in data.items()}
                 for axis, data in wave["axes"].items()},
        "orbits": orbits,
    }


def _publish_series(sensor: dict[str, Any], captures: list[dict[str, Any]], config: dict[str, Any],
                    periodic: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """Averaged spectrum and last-capture waveform of a series: kept as the sensor's
    latest spectrum and written to InfluxDB as one summary point."""
    sample_rate, measured = _series_rate(captures)
    result = fft.analyze_average([c["samples"] for c in captures], sample_rate,
                                 velocity_min_hz=config["fft_velocity_min_hz"])
    # The time waveform and displacement paths come from the last capture.
    wave = fft.waveforms(captures[-1]["samples"], sample_rate)
    for pair, orbit in wave["orbits"].items():
        result["summary"][f"orbit_{pair}_ratio"] = round(orbit["overall"]["ratio"], 3)
    observed = datetime.now(timezone.utc)
    spectrum = _spectrum_payload(result, observed.isoformat(timespec="milliseconds"), periodic, measured is not None)
    # started_at: wall-clock time of the first sample of the shown (last) capture.
    waveform = _waveform_payload(wave, spectrum["captured_at"], sample_rate, captures[-1].get("first_sample_at"))
    spectrum["orbits"] = waveform["orbits"]
    with STATE_LOCK:
        SPECTRA[sensor["address"]] = spectrum
        WAVEFORMS[sensor["address"]] = waveform
    INFLUX.write(sensor, result["summary"], observed, measurement=FFT_MEASUREMENT)
    return spectrum, waveform


def _save_record(sensor: dict[str, Any], captures: list[dict[str, Any]], spectrum: dict[str, Any],
                 waveform: dict[str, Any], periodic: bool, note: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    saved = records.save(RECORDS_DIR, {
        "sensor": sensor["name"], "address": sensor["address"], "captured_at": spectrum["captured_at"],
        "note": note, "sample_rate_hz": spectrum["sample_rate_hz"], "samples": spectrum["samples"],
        "duration_s": spectrum["duration_s"], "averages": len(captures), "capture_count": len(captures),
        "periodic": periodic, "processing": {"velocity_min_hz": spectrum["velocity_min_hz"], "window": "hann"},
        "raw_captures_g": [capture["samples"] for capture in captures],
        "capture_started_at": [capture.get("first_sample_at") for capture in captures],
        "waveform": waveform, "spectrum": spectrum, **(extra or {}),
    })
    current = _config_snapshot()
    records.prune(RECORDS_DIR, current["record_max_count"], current["record_retention_days"])
    return saved


def _burst_work(address: int, duration_s: int, note: str = "",
                clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Continuous recording: FIFO captures of one sensor back to back for `duration_s`,
    saved as one record. Polling pauses for the whole record so the next request
    follows the data frame immediately; the last capture ends after `duration_s`.
    Stop or a failed capture ends the record and keeps the captures taken so far."""
    config = _config_snapshot()
    sensor = _registered(config, address)
    captures: list[dict[str, Any]] = []
    stopped, error = False, None
    started_wall = datetime.now(timezone.utc)
    with BUS.lock:
        _open_bus_for_operation(config)
        begin = clock()
        while len(captures) < BURST_MAX_CAPTURES:
            elapsed = clock() - begin
            if captures and elapsed >= duration_s:
                break
            if STOP_REQUESTED.is_set():
                stopped = True
                break
            with STATE_LOCK:
                OPERATION["progress"] = f"continuous capture {len(captures) + 1} · {elapsed:.0f}/{duration_s} s"
            try:
                captures.append(_stamp_capture(BUS.capture_fifo(address)))
            except BusError as exc:
                if not captures:
                    raise
                error = f"capture {len(captures) + 1}: {exc}"
                break
    if not captures:
        raise ValueError("stopped before the first capture")
    spectrum, waveform = _publish_series(sensor, captures, config, periodic=False)
    starts = burst.capture_starts(captures)
    extra = {
        "kind": "burst", "requested_duration_s": duration_s, "started_at": started_wall.isoformat(timespec="milliseconds"),
        "capture_starts_s": starts,
        "capture_rates_hz": [None if c.get("sample_rate") is None else round(c["sample_rate"], 3) for c in captures],
        "stopped": stopped, "error": error,
        **burst.summary(starts, spectrum["samples"], spectrum["sample_rate_hz"]),
    }
    saved = _save_record(sensor, captures, spectrum, waveform, False, note, extra)
    return {"address": address, "sample_rate_hz": spectrum["sample_rate_hz"], "averages": len(captures),
            "stopped": stopped, "capture_error": error, "record": saved,
            "peaks": {axis: data["peaks"] for axis, data in spectrum["axes"].items()}}


def _burst_analysis(record_id: str, window: int) -> dict[str, Any]:
    stamp = records.file_stamp(RECORDS_DIR, record_id)
    key = (record_id, stamp, window)
    with BURST_CACHE_LOCK:
        if key in BURST_CACHE:
            return BURST_CACHE[key]
    record = records.load(RECORDS_DIR, record_id)
    if record.get("kind") != "burst":
        raise ValueError("not a continuous record")
    analysis = burst.analyze(record["raw_captures_g"], record["capture_starts_s"], float(record["sample_rate_hz"]),
                             window=window,
                             velocity_min_hz=float((record.get("processing") or {}).get("velocity_min_hz")
                                                   or fft.VELOCITY_RMS_MIN_HZ))
    with BURST_CACHE_LOCK:
        BURST_CACHE[key] = analysis
        while len(BURST_CACHE) > BURST_CACHE_SIZE:
            BURST_CACHE.pop(next(iter(BURST_CACHE)))
    return analysis


def _fft_next_in(config: dict[str, Any], sensor: dict[str, Any]) -> float | None:
    """Seconds until the next periodic capture; None when periodic capture does not apply.

    Call with STATE_LOCK held. A sensor that is online but not scheduled yet is due now.
    """
    if not config["fft_periodic"] or not config["port"] or not sensor["enabled"]:
        return None
    if not SENSOR_STATE.get(sensor["address"], {}).get("online"):
        return None
    due = FFT_NEXT.get(sensor["address"])
    return 0.0 if due is None else round(max(0.0, due - time.monotonic()), 1)


def _spectrum_brief(address: int) -> dict[str, Any] | None:
    spectrum = SPECTRA.get(address)
    if not spectrum:
        return None
    brief = {k: spectrum.get(k) for k in ("captured_at", "periodic", "error", "sample_rate_hz",
                                          "sample_rate_measured", "duration_s", "averages")}
    axes = spectrum.get("axes") or {}
    brief["peaks"] = {axis: data["peaks"] for axis, data in axes.items()}
    brief["velocity_rms_mm_s"] = {axis: data["velocity_rms_mm_s"] for axis, data in axes.items()}
    brief["velocity_min_hz"] = spectrum.get("velocity_min_hz")
    return brief


# --- HTTP ------------------------------------------------------------------------

def _no_cache(body: str, content_type: str):
    return body, 200, {"Content-Type": content_type, "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


def list_ports(dirs: tuple[str, ...] = SERIAL_DIRS) -> list[dict[str, str]]:
    ports = []
    for directory in dirs:
        path = Path(directory)
        if not path.is_dir():
            continue
        for entry in sorted(path.iterdir()):
            try:
                target = os.path.realpath(entry)
            except OSError:
                target = ""
            ports.append({"path": str(entry), "device": target})
    for pattern in ("ttyUSB*", "ttyACM*"):
        for entry in sorted(Path("/dev").glob(pattern)):
            if not any(p["device"] == str(entry) for p in ports):
                ports.append({"path": str(entry), "device": str(entry)})
    if SIM_PORTS_LISTED:
        ports.append({"path": SIM_PORT, "device": "simulated WTVB01 (5.25 Hz reference)"})
        ports.append({"path": SIM_FAULTS_PORT, "device": "simulated WTVB01, 30 min fault scenario"})
    return ports


def health_payload() -> dict[str, Any]:
    with STATE_LOCK:
        config = CONFIG
        enabled = [s for s in config["sensors"] if s["enabled"]]
        online = [s for s in enabled if SENSOR_STATE.get(s["address"], {}).get("online")]
        bus = dict(BUS_STATE)
        busy = OPERATION["name"] if OPERATION["running"] else None
    if not config["port"] or not enabled:
        status = "idle"
    elif bus["open"] and len(online) == len(enabled):
        status = "ok"
    else:
        status = "degraded"
    return {
        "status": status, "service": "vibration-collector", "port": config["port"], "port_present": bus["port_present"],
        "baud": config["baud"], "sensors_enabled": len(enabled), "sensors_online": len(online),
        "cycle_ms": bus["cycle_ms"], "last_error": bus["last_error"] or CONFIG_ERROR, "operation": busy,
        "influx_configured": _influx_enabled(), "influx_error": INFLUX.last_error,
        "simulation": sim.scenario_status(config["port"]) if config["port"].startswith(cfg.SIM_PORT_PREFIX) else None,
    }


@app.get("/")
def index():
    return _no_cache(Path(__file__).with_name("ui.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")


@app.get("/help")
def help_page():
    return _no_cache(Path(__file__).with_name("help.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")


@app.get("/ui.js")
def ui_js():
    return _no_cache(Path(__file__).with_name("ui.js").read_text(encoding="utf-8"), "application/javascript")


@app.get("/device-common.css")
def device_common_css():
    return Path(__file__).with_name("device-common.css").read_text(encoding="utf-8"), 200, {"Content-Type": "text/css"}


@app.get("/health")
@app.get("/api/health")
def health():
    return jsonify(health_payload())


@app.get("/api/status")
def status():
    with STATE_LOCK:
        config = cfg.validate(CONFIG)
        sensors = []
        for sensor in config["sensors"]:
            state = dict(_sensor_state(sensor["address"]))
            state.pop("next_retry", None)
            state.pop("block_read_at", None)
            state["fft"] = _spectrum_brief(sensor["address"])
            state["fft_next_in_sec"] = _fft_next_in(config, sensor)
            sensors.append({**sensor, **state})
        operation = dict(OPERATION)
    return jsonify(
        health=health_payload(), config={k: v for k, v in config.items() if k != "sensors"}, sensors=sensors,
        operation=operation, bauds=list(proto.SUPPORTED_BAUDS), read_profiles=list(proto.READ_PROFILES),
        fields={name: [{"name": f.name, "unit": f.unit} for f in block[2]] for name, block in
                (("basic", proto.BLOCK_BASIC), ("stats", proto.BLOCK_STATS), ("energy", proto.BLOCK_ENERGY))},
        harmonics={"points": list(proto.HARMONIC_POINTS), "bands": list(proto.HARMONIC_BANDS)},
        sim_sensors=_sim_sensors_status(),
    )


@app.get("/api/ports")
def ports():
    return jsonify(ports=list_ports())


def _json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("JSON object body required")
    return body


def _busy_response():
    return jsonify(error="another bus operation is running"), 409


# Settings that never touch the bus; they may change while a capture or scan runs
# (Stop must work during a long averaged series).
FFT_SETTING_KEYS = ("fft_periodic", "fft_interval_sec", "fft_averages", "fft_velocity_min_hz",
                    "fft_save_records", "record_retention_days", "record_max_count")


def _update_config(mutator: Callable[[dict[str, Any]], None], allow_while_busy: bool = False):
    if OPERATION["running"] and not allow_while_busy:
        return _busy_response()
    try:
        config = _config_snapshot()
        mutator(config)
        return jsonify(config=_store_config(config))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/config")
def update_config():
    keys = ("port", "baud", "poll_interval_sec", "read_profile") + FFT_SETTING_KEYS
    body = request.get_json(silent=True)
    fft_only = isinstance(body, dict) and bool(body) and all(key in FFT_SETTING_KEYS for key in body if key in keys)

    def apply(config: dict[str, Any]) -> None:
        body = _json_body()
        for key in keys:
            if key in body:
                config[key] = body[key]
    return _update_config(apply, allow_while_busy=fft_only)


@app.post("/api/sensors")
def add_sensor():
    def apply(config: dict[str, Any]) -> None:
        config["sensors"].append(cfg.parse_sensor(_json_body()))
    return _update_config(apply)


def _find(config: dict[str, Any], address: int) -> dict[str, Any]:
    for sensor in config["sensors"]:
        if sensor["address"] == address:
            return sensor
    raise LookupError


@app.patch("/api/sensors/<int:address>")
def update_sensor(address: int):
    def apply(config: dict[str, Any]) -> None:
        body = _json_body()
        sensor = _find(config, address)
        if "name" in body:
            sensor["name"] = cfg.parse_name(body["name"])
        if "enabled" in body:
            sensor["enabled"] = bool(body["enabled"])
    try:
        return _update_config(apply)
    except LookupError:
        return jsonify(error="unknown sensor"), 404


@app.delete("/api/sensors/<int:address>")
def delete_sensor(address: int):
    def apply(config: dict[str, Any]) -> None:
        _find(config, address)
        config["sensors"] = [s for s in config["sensors"] if s["address"] != address]
    try:
        return _update_config(apply)
    except LookupError:
        return jsonify(error="unknown sensor"), 404


@app.post("/api/scan")
def scan():
    try:
        body = request.get_json(silent=True) or {}
        first = cfg.parse_address(body.get("first", proto.MIN_ADDRESS))
        last = cfg.parse_address(body.get("last", proto.MAX_ADDRESS))
        if first > last:
            raise ValueError("first address must not exceed last")
        config = _config_snapshot()
        bauds = [cfg.parse_baud(b) for b in body.get("bauds") or [config["baud"]]]
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("scan", lambda: _scan_work(config, first, last, bauds)):
        return _busy_response()
    return jsonify(started=True), 202


@app.post("/api/sim-sensor")
def sim_sensor():
    """Switch a simulated sensor on or off: {"address": 126|127, "active": true|false}.

    On means registered and polled; switching one on switches the others off."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("active"), bool):
        return jsonify(error='JSON body {"address": n, "active": true|false} required'), 400
    active = body["active"]
    try:
        address = cfg.parse_address(body.get("address", SIM_SENSOR_ADDRESS))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if address not in SIM_SENSORS:
        return jsonify(error=f"no simulated sensor at address {address}"), 404
    config = _config_snapshot()
    names = {a: spec["name"] for a, spec in SIM_SENSORS.items()}
    # "sim-roll-600" is the name of the first version of the defects simulator.
    other = next((s for s in config["sensors"] if s["address"] in names
                  and s["name"] not in (names[s["address"]], "sim-roll-600")), None)
    if other is not None:
        return jsonify(error=f"address {other['address']} is used by sensor {other['name']}"), 409

    def apply(config: dict[str, Any]) -> None:
        if active:  # One simulated sensor at a time.
            config["sensors"] = [s for s in config["sensors"] if s["address"] not in SIM_SENSORS]
            config["sensors"].append({"address": address, "name": names[address], "enabled": True})
        else:
            config["sensors"] = [s for s in config["sensors"] if s["address"] != address]
    response = _update_config(apply)
    if isinstance(response, tuple):
        return response
    return jsonify(sim_sensor=_sim_sensor_status(address), sim_sensors=_sim_sensors_status(),
                   config=response.get_json()["config"])


@app.post("/api/sensors/<int:address>/address")
def change_address(address: int):
    try:
        new = cfg.parse_address(_json_body().get("new_address"))
        if address in SIM_SENSORS or new in SIM_SENSORS:
            raise ValueError("simulated sensor addresses cannot be changed or reused")
        config = _config_snapshot()
        _find(config, address)
        if any(s["address"] == new for s in config["sensors"]):
            raise ValueError(f"address 0x{new:02X} is already registered")
    except LookupError:
        return jsonify(error="unknown sensor"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("set_address", lambda: _address_work(address, new)):
        return _busy_response()
    return jsonify(started=True), 202


@app.post("/api/sensors/<int:address>/spectrum")
def capture_spectrum(address: int):
    try:
        _find(_config_snapshot(), address)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValueError("JSON object body required")
        averages = cfg.parse_fft_averages(body["averages"]) if "averages" in body else None
    except LookupError:
        return jsonify(error="unknown sensor"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("fft", lambda: _fft_work(address, averages=averages)):
        return _busy_response()
    return jsonify(started=True), 202


@app.get("/api/sensors/<int:address>/spectrum")
def get_spectrum(address: int):
    with STATE_LOCK:
        spectrum = SPECTRA.get(address)
    if not spectrum or "axes" not in spectrum:
        return jsonify(error="no spectrum captured yet"), 404
    return jsonify(spectrum)


@app.get("/api/sensors/<int:address>/waveform")
def get_waveform(address: int):
    with STATE_LOCK:
        waveform = WAVEFORMS.get(address)
    if not waveform:
        return jsonify(error="no capture yet"), 404
    return jsonify(waveform)


@app.post("/api/sensors/<int:address>/records")
def capture_record(address: int):
    try:
        _find(_config_snapshot(), address)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValueError("JSON object body required")
        averages = cfg.parse_fft_averages(body.get("averages", 1))
        note = str(body.get("note") or "").strip()
        if len(note) > 200:
            raise ValueError("note must not exceed 200 characters")
    except LookupError:
        return jsonify(error="unknown sensor"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("record", lambda: _fft_work(address, averages=averages, save_record=True, note=note)):
        return _busy_response()
    return jsonify(started=True), 202


@app.post("/api/sensors/<int:address>/burst")
def capture_burst(address: int):
    try:
        _find(_config_snapshot(), address)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValueError("JSON object body required")
        duration = cfg.parse_burst_duration(body.get("duration_s", cfg.DEFAULT_BURST_DURATION_SEC))
        note = str(body.get("note") or "").strip()
        if len(note) > 200:
            raise ValueError("note must not exceed 200 characters")
    except LookupError:
        return jsonify(error="unknown sensor"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("burst", lambda: _burst_work(address, duration, note=note)):
        return _busy_response()
    return jsonify(started=True), 202


@app.post("/api/operation/stop")
def stop_operation():
    """End a running capture series or continuous record after the current capture."""
    if not OPERATION["running"] or OPERATION["name"] not in ("fft", "record", "burst"):
        return jsonify(error="no capture series is running"), 409
    STOP_REQUESTED.set()
    return jsonify(stopping=True)


@app.get("/api/records/<record_id>/burst")
def get_burst_analysis(record_id: str):
    try:
        window = cfg.parse_spectrogram_window(request.args.get("window", cfg.DEFAULT_SPECTROGRAM_WINDOW))
        return jsonify(_burst_analysis(record_id, window))
    except FileNotFoundError:
        return jsonify(error="record not found"), 404
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/records/<record_id>/captures/<int:index>")
def get_record_capture(record_id: str, index: int):
    """Waveform, orbits and spectrum of one capture of a saved record."""
    try:
        record = records.load(RECORDS_DIR, record_id)
        raw = record.get("raw_captures_g") or []
        if not 0 <= index < len(raw):
            return jsonify(error="capture not found"), 404
        sample_rate = float(record["sample_rate_hz"])
        starts = record.get("capture_starts_s") or []
        velocity_min = float((record.get("processing") or {}).get("velocity_min_hz") or fft.VELOCITY_RMS_MIN_HZ)
        result = fft.analyze(raw[index], sample_rate, velocity_min_hz=velocity_min)
        label = str(record.get("started_at") or record.get("captured_at") or "")
        spectrum = _spectrum_payload(result, label, bool(record.get("periodic")), True)
        waveform = _waveform_payload(fft.waveforms(raw[index], sample_rate), label, sample_rate,
                                     _capture_start(record, index))
    except FileNotFoundError:
        return jsonify(error="record not found"), 404
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(index=index, count=len(raw), start_s=starts[index] if index < len(starts) else None,
                   waveform=waveform, spectrum=spectrum)


@app.get("/api/records")
def list_capture_records():
    return jsonify(records=records.list_records(RECORDS_DIR))


@app.delete("/api/records")
def delete_capture_records():
    body = request.get_json(silent=True)
    item_ids = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(item_ids, list) or not item_ids or len(item_ids) > 10000:
        return jsonify(error="ids must be a non-empty array of at most 10000 record IDs"), 400
    if not all(isinstance(item_id, str) for item_id in item_ids):
        return jsonify(error="every record ID must be a string"), 400
    try:
        deleted, missing = records.delete_many(RECORDS_DIR, item_ids)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(deleted=deleted, missing=missing, deleted_count=len(deleted))


@app.get("/api/records/<record_id>")
def get_capture_record(record_id: str):
    try:
        return jsonify(records.load(RECORDS_DIR, record_id))
    except FileNotFoundError:
        return jsonify(error="record not found"), 404
    except (OSError, ValueError) as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/records/<record_id>/download.json")
def download_capture_json(record_id: str):
    try:
        body = records.load(RECORDS_DIR, record_id)
    except FileNotFoundError:
        return jsonify(error="record not found"), 404
    except (OSError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    response = jsonify(body)
    response.headers["Content-Disposition"] = f'attachment; filename="{record_id}.json"'
    return response


@app.get("/api/records/<record_id>/download.csv")
def download_capture_csv(record_id: str):
    try:
        body = records.load(RECORDS_DIR, record_id)
        content = records.csv_content(
            body, mode=request.args.get("export_mode", "view"),
            export_hz=request.args.get("export_hz"), delimiter=resolve_csv_delimiter(request.args),
        )
    except FileNotFoundError:
        return jsonify(error="record not found"), 404
    except (OSError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    return Response(content, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{record_id}.csv"'})


@app.delete("/api/records/<record_id>")
def delete_capture_record(record_id: str):
    try:
        if not records.delete(RECORDS_DIR, record_id):
            return jsonify(error="record not found"), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(deleted=True)


@app.post("/api/bus/baud")
def change_baud():
    try:
        new_baud = cfg.parse_baud(_json_body().get("baud"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not _start_operation("set_baud", lambda: _baud_work(new_baud)):
        return _busy_response()
    return jsonify(started=True), 202


def main() -> None:
    _sync_virtual(_config_snapshot())
    threading.Thread(target=_poll_loop, name="vibration-poller", daemon=True).start()
    app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True)


if __name__ == "__main__":
    main()
