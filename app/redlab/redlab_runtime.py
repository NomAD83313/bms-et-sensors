"""One USB-TC acquisition session, configuration, filtering, and health."""

import threading
import time
from datetime import datetime, timezone

from uldaq import DaqDevice, TcType
from uldaq.daq_device_config import DevVersionType

if __package__:
    from .redlab_acquisition import (
        BOOTSTRAP_BATCHES, BOOTSTRAP_WARMUP_SECONDS, CHANNEL_COUNT,
        channel_log_data, configure_tc_channels, filter_bootstrap_batches,
        load_active_channels, new_filter_state, read_batch,
    )
    from .redlab_inventory import canonical_redlab_device_id
    from .redlab_writer import RedLabWriter
else:
    from redlab_acquisition import (
        BOOTSTRAP_BATCHES, BOOTSTRAP_WARMUP_SECONDS, CHANNEL_COUNT,
        channel_log_data, configure_tc_channels, filter_bootstrap_batches,
        load_active_channels, new_filter_state, read_batch,
    )
    from redlab_inventory import canonical_redlab_device_id
    from redlab_writer import RedLabWriter


def _tc_type_labels() -> list[str]:
    return [t.name for t in TcType]


def _resolve_tc_type(raw: str) -> TcType | None:
    key = str(raw or "").strip().upper()
    if not key:
        return None
    try:
        return TcType[key]
    except Exception:
        return None


class RedLabRuntime:
    def __init__(self, descriptor, write_api, *, settings, read_settings):
        self.settings = settings
        self.read_settings = read_settings
        self.descriptor = descriptor
        self.device_id = canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
        self.unique_id = str(getattr(descriptor, "unique_id", "") or "")
        self.product_name = str(getattr(descriptor, "product_name", "") or "USB-TC")
        self.lock = threading.Lock()
        self.health_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.daq_device = None
        self.ai_device = None
        self.ai_config = None
        self.filter_state = new_filter_state()
        self.cadence_signature: tuple[str, float] | None = None
        self.loop_timestamps: list[float] = []
        self.health = {
            "ok": True,
            "device_id": self.device_id,
            "device_name": self.product_name,
            "device_unique_id": self.unique_id,
            "status": "starting",
            "connected": False,
            "influx_ok": False,
            "last_ok_ts": None,
            "last_channels": [],
            "message": "runtime starting",
            "device_fw_main": None,
            "read_mode": self.read_settings.get_read_mode(),
            "loop_sleep_seconds": self.read_settings.get_loop_sleep(),
            "loop_hz": None,
            "cal_date": None,
            "resolution": None,
            "has_pacer": None,
            "write_queue_depth": 0,
        }

        self.writer = RedLabWriter(
            self.device_id, self.unique_id, write_api,
            settings=settings, read_settings=read_settings, set_health=self.set_health,
        )

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.writer.start()
        self.thread = threading.Thread(target=self.run, name=f"redlab-{self.unique_id}", daemon=True)
        self.thread.start()

    def stop(self, message: str) -> None:
        self.stop_event.set()
        self.writer.stop()
        self.set_health(status="offline", connected=False, message=message)

    def set_health(self, **kwargs) -> None:
        with self.health_lock:
            self.health.update(kwargs)

    def health_snapshot(self) -> dict:
        with self.health_lock:
            return dict(self.health)

    def connect(self) -> None:
        self.set_health(status="starting", connected=False, message="connecting")
        print(f"--- Connecting RedLab runtime {self.device_id} ({self.product_name})...")
        dev = DaqDevice(self.descriptor)
        dev.connect()
        ai_device = dev.get_ai_device()
        ai_info = ai_device.get_info()
        ai_config = ai_device.get_config()
        configure_tc_channels(ai_config)

        fw_main = None
        cal_date = None
        resolution = None
        has_pacer = None
        temp_unit = None
        try:
            fw_main = dev.get_config().get_version(DevVersionType.FW_MAIN)
        except Exception:
            pass
        try:
            temp_unit = ai_config.get_temp_unit().name
        except Exception:
            pass
        try:
            raw_cal_date = ai_config.get_cal_date()
            if raw_cal_date is not None:
                try:
                    cal_date = datetime.fromtimestamp(int(raw_cal_date), tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    cal_date = str(raw_cal_date)
        except Exception:
            pass
        try:
            resolution = ai_info.get_resolution()
        except Exception:
            pass
        try:
            has_pacer = bool(ai_info.has_pacer())
        except Exception:
            pass

        with self.lock:
            self.daq_device = dev
            self.ai_device = ai_device
            self.ai_config = ai_config
        self.set_health(
            status="running",
            connected=True,
            influx_ok=True,
            message="connected",
            device_fw_main=fw_main,
            temp_unit=temp_unit,
            cal_date=cal_date,
            resolution=resolution,
            has_pacer=has_pacer,
            read_mode=self.read_settings.get_read_mode(),
            loop_sleep_seconds=self.read_settings.get_loop_sleep(),
        )
        print(f">>> RedLab runtime connected: {self.device_id}")

    def tc_config_payload(self) -> dict:
        with self.lock:
            ai_config = self.ai_config
        if ai_config is None:
            return {"ok": False, "error": "device not connected", "device_id": self.device_id}
        channels = []
        with self.lock:
            for ch in range(CHANNEL_COUNT):
                try:
                    tc = ai_config.get_chan_tc_type(ch)
                    channels.append({"channel": ch, "tc_type": tc.name})
                except Exception as exc:
                    channels.append({"channel": ch, "tc_type": "UNKNOWN", "error": str(exc)})
        return {"ok": True, "device_id": self.device_id, "channels": channels, "types": _tc_type_labels()}

    def apply_tc_updates(self, updates: list) -> dict:
        with self.lock:
            ai_config = self.ai_config
            if ai_config is None:
                return {"ok": False, "error": "device not connected", "device_id": self.device_id}
            applied = []
            for item in updates:
                try:
                    ch = int(item.get("channel"))
                    tc = _resolve_tc_type(item.get("tc_type"))
                    if tc is None:
                        continue
                    ai_config.set_chan_tc_type(ch, tc)
                    applied.append({"channel": ch, "tc_type": tc.name})
                except Exception as exc:
                    applied.append({"channel": item.get("channel"), "error": str(exc)})
            self.filter_state["last_valid_by_channel"].clear()
        return {"ok": True, "device_id": self.device_id, "applied": applied}

    def write_runtime_points(self, batches: list[dict], message: str, *, skipped: int = 0, loop_hz: float | None = None) -> None:
        if not batches:
            return
        last_log_data = channel_log_data(batches[-1].get("values", {}))
        depth = self.writer.enqueue(batches, message, skipped, loop_hz)
        self.set_health(
            status="running",
            connected=True,
            last_channels=last_log_data,
            message=message,
            skipped_channels=skipped,
            read_mode=self.read_settings.get_read_mode(),
            loop_sleep_seconds=self.read_settings.get_loop_sleep(),
            loop_hz=loop_hz,
            write_queue_depth=depth,
        )

    def observe_loop_hz(self) -> float | None:
        signature = (self.read_settings.get_read_mode(), self.read_settings.get_loop_sleep())
        if signature != self.cadence_signature:
            self.cadence_signature = signature
            self.loop_timestamps.clear()
        now = time.monotonic()
        self.loop_timestamps.append(now)
        if len(self.loop_timestamps) > 12:
            self.loop_timestamps.pop(0)
        if len(self.loop_timestamps) < 2:
            return None
        span = self.loop_timestamps[-1] - self.loop_timestamps[0]
        if span <= 0:
            return None
        return round((len(self.loop_timestamps) - 1) / span, 2)

    def run(self) -> None:
        try:
            self.connect()
            warmup_deadline = time.monotonic() + BOOTSTRAP_WARMUP_SECONDS
            bootstrap_batches: list[dict] = []
            bootstrap_done = False

            while not self.stop_event.is_set():
                warmup_remaining = warmup_deadline - time.monotonic()
                active_channels = load_active_channels(self.settings.channel_state_path)
                if not active_channels:
                    self.set_health(
                        status="running",
                        connected=True,
                        influx_ok=True,
                        last_channels=[],
                        message="no active RedLab channels selected",
                    )
                    time.sleep(self.read_settings.get_loop_sleep())
                    continue

                with self.lock:
                    ai_device = self.ai_device
                    ai_config = self.ai_config
                    if ai_device is None or ai_config is None:
                        raise RuntimeError("device not connected")
                    batch_values, skipped = read_batch(
                        ai_device,
                        ai_config,
                        active_channels,
                        self.filter_state,
                        self.read_settings.get_read_mode(),
                    )

                if not batch_values:
                    time.sleep(self.read_settings.get_loop_sleep())
                    continue

                batch = {"ts_ns": time.time_ns(), "values": batch_values}
                loop_hz = self.observe_loop_hz()

                if not bootstrap_done and warmup_remaining > 0:
                    self.set_health(
                        status="starting",
                        connected=True,
                        influx_ok=True,
                        last_channels=channel_log_data(batch_values),
                        message=f"warmup {warmup_remaining:.1f}s",
                        skipped_channels=skipped,
                        read_mode=self.read_settings.get_read_mode(),
                        loop_sleep_seconds=self.read_settings.get_loop_sleep(),
                        loop_hz=loop_hz,
                    )
                elif not bootstrap_done:
                    bootstrap_batches.append(batch)
                    if len(bootstrap_batches) < BOOTSTRAP_BATCHES:
                        self.set_health(
                            status="starting",
                            connected=True,
                            influx_ok=True,
                            last_channels=channel_log_data(batch_values),
                            message=f"bootstrap review {len(bootstrap_batches)}/{BOOTSTRAP_BATCHES}",
                            skipped_channels=skipped,
                            read_mode=self.read_settings.get_read_mode(),
                            loop_sleep_seconds=self.read_settings.get_loop_sleep(),
                            loop_hz=loop_hz,
                        )
                        time.sleep(self.read_settings.get_loop_sleep())
                        continue
                    filtered_batches, dropped = filter_bootstrap_batches(bootstrap_batches)
                    bootstrap_done = True
                    print(
                        f">>> Bootstrap review complete for {self.device_id}: "
                        f"kept {len(filtered_batches)}/{len(bootstrap_batches)} batches, "
                        f"dropped {dropped} bootstrap outlier points"
                    )
                    self.write_runtime_points(
                        filtered_batches,
                        f"bootstrap kept {len(filtered_batches)} batches",
                        skipped=skipped,
                        loop_hz=loop_hz,
                    )
                else:
                    self.write_runtime_points([batch], f"logged {len(batch_values)} points", skipped=skipped, loop_hz=loop_hz)
                time.sleep(self.read_settings.get_loop_sleep())
        except Exception as exc:
            print(f"!!! RedLab runtime error for {self.device_id}: {exc}")
            self.set_health(status="error", connected=False, influx_ok=False, message=f"runtime error: {exc}")
        finally:
            self.writer.stop()
            with self.lock:
                daq_device = self.daq_device
                self.daq_device = None
                self.ai_device = None
                self.ai_config = None
                self.filter_state["last_valid_by_channel"].clear()
            if daq_device:
                try:
                    daq_device.disconnect()
                    daq_device.release()
                except Exception:
                    pass
            if self.stop_event.is_set():
                self.set_health(status="offline", connected=False, message="runtime stopped")

