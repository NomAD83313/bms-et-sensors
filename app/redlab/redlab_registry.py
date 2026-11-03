"""USB discovery, runtime registry, and process-level re-enumeration recovery."""

import json
import os
import subprocess
import sys
import threading
import time

from uldaq import get_daq_device_inventory, InterfaceType

if __package__:
    from .redlab_inventory import canonical_redlab_device_id, descriptor_has_valid_unique_id, is_valid_redlab_unique_id, normalize_redlab_inventory
    from .redlab_runtime import RedLabRuntime
else:
    from redlab_inventory import canonical_redlab_device_id, descriptor_has_valid_unique_id, is_valid_redlab_unique_id, normalize_redlab_inventory
    from redlab_runtime import RedLabRuntime

_REDLAB_REENUMERATION_LAST_RESTART_ENV = "_REDLAB_REENUMERATION_LAST_RESTART_TS"


class RedLabRegistry:
    def __init__(self, *, settings, read_settings, write_api=None, before_restart=lambda: None, runtime_factory=RedLabRuntime):
        self.settings = settings
        self.read_settings = read_settings
        self.write_api = write_api
        self.before_restart = before_restart
        self.runtime_factory = runtime_factory
        self.lock = threading.Lock()
        self.runtimes = {}
        self.health_lock = threading.Lock()
        self.health = {
            "status": "starting",
            "connected": False,
            "influx_ok": False,
            "last_ok_ts": None,
            "last_channels": [],
            "message": "initializing",
            "device_name": None,
            "device_unique_id": None,
            "device_fw_main": None,
            "read_mode": read_settings.get_read_mode(),
            "loop_sleep_seconds": read_settings.get_loop_sleep(),
            "loop_hz": None,
            "cal_date": None,
            "resolution": None,
            "has_pacer": None,
        }

    def set_health(self, **kwargs):
        with self.health_lock:
            self.health.update(kwargs)

    def get_health(self):
        with self.health_lock:
            return dict(self.health)

    def _runtime_snapshot(self) -> dict[str, dict]:
        with self.lock:
            return {device_id: runtime.health_snapshot() for device_id, runtime in self.runtimes.items()}

    def running_runtime_ids(self) -> set[str]:
        snapshot = self._runtime_snapshot()
        return {
            device_id
            for device_id, state in snapshot.items()
            if state.get("status") in {"starting", "running", "degraded"}
        }

    def runtime_for_device(self, device_id: str):
        with self.lock:
            return self.runtimes.get(str(device_id or "").strip())

    def active_device_id(self) -> str | None:
        configured = self.settings.active_unique_id.strip()
        if configured:
            return configured if configured.startswith("redlab_") else canonical_redlab_device_id(configured)
        running = sorted(self.running_runtime_ids())
        if running:
            return running[0]
        unique_id = str(self.get_health().get("device_unique_id") or "").strip()
        return canonical_redlab_device_id(unique_id) if unique_id else None

    def device_is_active(self, device_id: str) -> bool:
        runtime = self.runtime_for_device(device_id)
        if runtime is None:
            return False
        return runtime.health_snapshot().get("status") in {"starting", "running", "degraded"}

    def device_inventory(self) -> list[dict]:
        active_unique_id = str(self.get_health().get("device_unique_id") or "")
        descriptors = self._valid_inventory_descriptors()
        return normalize_redlab_inventory(
            descriptors,
            active_unique_id=active_unique_id,
            active_device_ids=self.running_runtime_ids(),
        )

    def _valid_inventory_descriptors(self) -> list:
        return [
            descriptor for descriptor in get_daq_device_inventory(InterfaceType.USB)
            if descriptor_has_valid_unique_id(descriptor)
        ]

    def _descriptor_device_ids(self, descriptors: list) -> set[str]:
        return {
            canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
            for descriptor in descriptors
        }

    def _fresh_inventory_device_ids(self) -> set[str]:
        code = (
            "import json;"
            "from uldaq import get_daq_device_inventory, InterfaceType;"
            "print(json.dumps([getattr(d, 'unique_id', '') for d in get_daq_device_inventory(InterfaceType.USB)]))"
        )
        try:
            raw = subprocess.check_output([sys.executable, "-c", code], text=True, timeout=5)
            unique_ids = json.loads(raw)
        except Exception as exc:
            print(f"!!! RedLab fresh inventory check failed: {exc}")
            return set()
        return {
            canonical_redlab_device_id(unique_id)
            for unique_id in unique_ids
            if is_valid_redlab_unique_id(unique_id)
        }

    def _last_reenumeration_restart_ts(self) -> float:
        try:
            return float(os.getenv(_REDLAB_REENUMERATION_LAST_RESTART_ENV, "0"))
        except ValueError:
            return 0.0

    def _restart_cooldown_remaining(self) -> float:
        if self.settings.reenumeration_cooldown <= 0:
            return 0.0
        return max(0.0, self.settings.reenumeration_cooldown - (time.time() - self._last_reenumeration_restart_ts()))

    def _restart_process_for_usb_reenumeration(self, missing_device_ids: set[str]) -> None:
        if not missing_device_ids:
            return
        os.environ[_REDLAB_REENUMERATION_LAST_RESTART_ENV] = str(time.time())
        print(
            "!!! RedLab registry detected USB re-enumeration stale state; "
            f"fresh inventory sees {sorted(missing_device_ids)} only from a new process. Restarting redlab-collector process."
        )
        sys.stdout.flush()
        sys.stderr.flush()
        self.before_restart()
        time.sleep(0.2)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def active_runtime(self):
        active_id = self.active_device_id()
        if active_id:
            runtime = self.runtime_for_device(active_id)
            if runtime is not None:
                return runtime
        with self.lock:
            for runtime in self.runtimes.values():
                if runtime.health_snapshot().get("status") in {"starting", "running", "degraded"}:
                    return runtime
        return None

    def _registry_overall_health(self) -> dict:
        snapshot = self._runtime_snapshot()
        running_states = [
            state for state in snapshot.values()
            if state.get("status") in {"starting", "running", "degraded"}
        ]
        preferred_id = self.active_device_id()
        selected = snapshot.get(preferred_id) if preferred_id else None
        if selected is None and running_states:
            selected = sorted(running_states, key=lambda item: item.get("device_id") or "")[0]
        if selected is None:
            return {
                "status": "offline",
                "connected": False,
                "influx_ok": bool(self.settings.token and self.settings.org and self.settings.bucket),
                "last_ok_ts": None,
                "last_channels": [],
                "message": "no RedLab runtimes running",
                "device_name": None,
                "device_unique_id": None,
                "device_fw_main": None,
                "read_mode": self.read_settings.get_read_mode(),
                "loop_sleep_seconds": self.read_settings.get_loop_sleep(),
                "loop_hz": None,
                "devices": snapshot,
                "device_count": len(snapshot),
                "running_device_count": 0,
            }
        payload = dict(selected)
        payload.update(
            devices=snapshot,
            device_count=len(snapshot),
            running_device_count=len(running_states),
            connected=bool(running_states),
            status="running" if any(state.get("status") == "running" for state in running_states) else payload.get("status"),
            read_mode=self.read_settings.get_read_mode(),
            loop_sleep_seconds=self.read_settings.get_loop_sleep(),
        )
        return payload

    def run(self) -> None:
        self.set_health(status="starting", influx_ok=True, message="registry starting")
        reenumeration_hits: dict[str, int] = {}
        while True:
            try:
                descriptors = self._valid_inventory_descriptors()
                seen_device_ids = self._descriptor_device_ids(descriptors)
                fresh_device_ids: set[str] = set()
                if self.settings.reenumeration_recovery:
                    fresh_device_ids = self._fresh_inventory_device_ids()
                    runtime_ids = set(self._runtime_snapshot().keys())
                    stale_missing = fresh_device_ids - seen_device_ids - runtime_ids
                    if stale_missing:
                        for device_id in stale_missing:
                            reenumeration_hits[device_id] = reenumeration_hits.get(device_id, 0) + 1
                        for device_id in list(reenumeration_hits):
                            if device_id not in stale_missing:
                                reenumeration_hits.pop(device_id, None)
                        confirmed_missing = {
                            device_id for device_id, count in reenumeration_hits.items()
                            if count >= self.settings.reenumeration_confirmations
                        }
                        if confirmed_missing:
                            cooldown_remaining = self._restart_cooldown_remaining()
                            visible_missing = confirmed_missing - seen_device_ids
                            if not visible_missing:
                                print(
                                    ">>> RedLab USB re-enumeration stale state already visible in current process; "
                                    f"skipping restart: {sorted(confirmed_missing)}"
                                )
                                reenumeration_hits.clear()
                            elif cooldown_remaining > 0:
                                print(
                                    "!!! RedLab registry sees stale USB re-enumeration, "
                                    f"but restart cooldown has {cooldown_remaining:.1f}s remaining: "
                                    f"{sorted(confirmed_missing)}"
                                )
                                self.set_health(
                                    status="degraded",
                                    connected=bool(self.running_runtime_ids()),
                                    message=f"USB re-enumeration restart cooldown {cooldown_remaining:.1f}s",
                                )
                            else:
                                if self.settings.reenumeration_debounce:
                                    print(
                                        "!!! RedLab registry sees possible USB re-enumeration stale state; "
                                        f"waiting {self.settings.reenumeration_debounce:.1f}s before restart check: "
                                        f"{sorted(confirmed_missing)}"
                                    )
                                    time.sleep(self.settings.reenumeration_debounce)
                                descriptors = self._valid_inventory_descriptors()
                                seen_device_ids = self._descriptor_device_ids(descriptors)
                                fresh_device_ids = self._fresh_inventory_device_ids()
                                runtime_ids = set(self._runtime_snapshot().keys())
                                still_missing = fresh_device_ids - seen_device_ids - runtime_ids
                                restart_needed = confirmed_missing & still_missing
                                if restart_needed:
                                    self._restart_process_for_usb_reenumeration(restart_needed)
                                else:
                                    print(">>> RedLab USB re-enumeration settled without process restart")
                                    reenumeration_hits.clear()
                    else:
                        reenumeration_hits.clear()
                with self.lock:
                    for device_id, runtime in list(self.runtimes.items()):
                        if device_id not in seen_device_ids and device_id not in fresh_device_ids:
                            runtime.stop("device disconnected")
                            self.runtimes.pop(device_id, None)
                    for descriptor in descriptors:
                        device_id = canonical_redlab_device_id(getattr(descriptor, "unique_id", ""))
                        runtime = self.runtimes.get(device_id)
                        if runtime is None or (runtime.thread and not runtime.thread.is_alive() and runtime.health_snapshot().get("status") == "error"):
                            runtime = self.runtime_factory(descriptor, self.write_api, settings=self.settings, read_settings=self.read_settings)
                            self.runtimes[device_id] = runtime
                            runtime.start()
                self.set_health(**self._registry_overall_health())
            except Exception as exc:
                print(f"!!! RedLab registry error: {exc}")
                self.set_health(status="degraded", connected=False, message=f"registry error: {exc}")
            time.sleep(max(1.0, self.settings.discovery_interval))

