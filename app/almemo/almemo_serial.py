"""Serial discovery, command sessions, and device health."""

import os
import time
import threading
import glob
import re
from pathlib import Path
from typing import Any

import serial

if __package__:
    from . import almemo_settings as settings, almemo_protocol as protocol
else:
    import almemo_settings as settings
    import almemo_protocol as protocol


class AlmemoSession:
    def __init__(self):
        # Bind the streaming service before serving requests.
        self.streams = None
        self.ALMEMO_PROTOCOL_VERSION = "V6"
        self.VERSION_LOCK = threading.Lock()
        self.HEALTH_CACHE_LOCK = threading.Lock()
        self._LAST_HEALTH_DATA: dict[str, Any] = {}
        self._LAST_HEALTH_AT = 0.0
        self.SERIAL_LOCK = threading.Lock()
        self.SESSION_SWITCH_LOCK = threading.Lock()
        self.HEALTH_PROBE_LOCK = threading.Lock()
        self._PSER: serial.Serial | None = None
        self._PSER_PORT: str = ""
        self.LAST_DEVICE_OK_AT = 0.0
        self.LAST_DEVICE_MISSING_AT = 0.0
        self.LAST_DEVICE_MISSING_REASON = ""

    def _get_protocol_version(self) -> str:
        with self.VERSION_LOCK:
            return self.ALMEMO_PROTOCOL_VERSION

    def _mark_device_ok(self) -> None:
        self.LAST_DEVICE_OK_AT = time.monotonic()
        self.LAST_DEVICE_MISSING_AT = 0.0
        self.LAST_DEVICE_MISSING_REASON = ""

    def _mark_device_missing(self, reason: str) -> None:
        self.LAST_DEVICE_MISSING_AT = time.monotonic()
        self.LAST_DEVICE_MISSING_REASON = reason.strip()

    def _device_recently_ok(self, max_age_sec: float) -> bool:
        if self.LAST_DEVICE_OK_AT <= 0.0:
            return False
        return (time.monotonic() - self.LAST_DEVICE_OK_AT) <= max_age_sec

    def _recent_missing_reason(self, max_age_sec: float) -> str | None:
        if self.LAST_DEVICE_MISSING_AT <= 0.0:
            return None
        if self.LAST_DEVICE_OK_AT > self.LAST_DEVICE_MISSING_AT:
            return None
        if (time.monotonic() - self.LAST_DEVICE_MISSING_AT) > max_age_sec:
            return None
        return self.LAST_DEVICE_MISSING_REASON or "ALMEMO 2490-2 not responding"

    def _log_serial_event(self, message: str) -> None:
        print(f">>> {message}", flush=True)

    def _serial_candidates(self) -> list[str]:
        candidates: list[str] = []
        by_id = "/dev/serial/by-id"
        if os.path.isdir(by_id):
            for name in sorted(os.listdir(by_id)):
                candidates.append(os.path.join(by_id, name))
        for pattern in ("/dev/ttyUSB", "/dev/ttyACM"):
            for idx in range(0, 8):
                path = f"{pattern}{idx}"
                if os.path.exists(path):
                    candidates.append(path)
        seen: set[str] = set()
        ordered: list[str] = []
        for item in candidates:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _excluded_serial_realpaths(self) -> set[str]:
        excluded: set[str] = set()
        for raw_pattern in settings.ALMEMO_EXCLUDE_PORTS.split(","):
            pattern = raw_pattern.strip()
            if not pattern:
                continue
            matches = glob.glob(pattern) if any(ch in pattern for ch in "*?[]") else [pattern]
            for path in matches:
                if os.path.exists(path):
                    excluded.add(os.path.realpath(path))
                else:
                    excluded.add(path)
        return excluded

    def _excluded_usb_ids(self) -> set[tuple[str, str]]:
        excluded: set[tuple[str, str]] = set()
        for raw_item in settings.ALMEMO_EXCLUDE_USB_IDS.split(","):
            item = raw_item.strip().lower()
            if not item or ":" not in item:
                continue
            vendor, product = (part.strip() for part in item.split(":", 1))
            if vendor and product:
                excluded.add((vendor, product))
        return excluded

    def _serial_usb_ids(self, path: str) -> tuple[str, str] | None:
        real_path = os.path.realpath(path) if os.path.exists(path) else path
        tty_name = os.path.basename(real_path)
        sys_path = Path("/sys/class/tty") / tty_name / "device"
        if not sys_path.exists():
            return None
        try:
            current = sys_path.resolve()
        except Exception:
            current = sys_path
        for current in (current, *current.parents):
            vendor_path = current / "idVendor"
            product_path = current / "idProduct"
            if vendor_path.exists() and product_path.exists():
                try:
                    return (
                        vendor_path.read_text(encoding="ascii").strip().lower(),
                        product_path.read_text(encoding="ascii").strip().lower(),
                    )
                except Exception:
                    return None
        return None

    def _is_excluded_serial_port(self, path: str, excluded_realpaths: set[str]) -> bool:
        if not path:
            return True
        basename = os.path.basename(path).lower()
        if basename.startswith(("ttymicroeps", "ttyoptris", "ttypyro")):
            return True
        key = os.path.realpath(path) if os.path.exists(path) else path
        if key in excluded_realpaths:
            return True
        usb_ids = self._serial_usb_ids(path)
        return usb_ids is not None and usb_ids in self._excluded_usb_ids()

    def _candidate_ports(self) -> list[str]:
        ordered: list[str] = []
        if settings.ALMEMO_PORT_ENV and os.path.exists(settings.ALMEMO_PORT_ENV):
            ordered.append(settings.ALMEMO_PORT_ENV)
        candidates = self._serial_candidates()
        excluded_realpaths = self._excluded_serial_realpaths()
        for path in candidates:
            name = os.path.basename(path).lower()
            if self._is_excluded_serial_port(path, excluded_realpaths):
                continue
            if "almemo" in name:
                ordered.append(path)
        if os.path.exists("/dev/ttyUSB0"):
            ordered.append("/dev/ttyUSB0")
        ordered.extend(path for path in candidates if not self._is_excluded_serial_port(path, excluded_realpaths))

        deduped: list[str] = []
        seen: set[str] = set()
        for path in ordered:
            if self._is_excluded_serial_port(path, excluded_realpaths):
                continue
            key = os.path.realpath(path) if os.path.exists(path) else path
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    def _resolve_port(self) -> str:
        ports = self._candidate_ports()
        if ports:
            return ports[0]
        if settings.ALMEMO_PORT_ENV:
            return settings.ALMEMO_PORT_ENV
        return "/dev/ttyUSB0"

    def _write_line(self, ser: serial.Serial, command: str, *, sleep_sec: float = 0.02) -> None:
        payload = (command + protocol._decode_eol(settings.ALMEMO_EOL)).encode("utf-8")
        ser.reset_output_buffer()
        time.sleep(sleep_sec)
        ser.write(payload)
        time.sleep(sleep_sec)

    def _discard_input_until_quiet(self,
        ser: serial.Serial,
        *,
        max_sec: float | None = None,
        quiet_sec: float | None = None,
    ) -> int:
        max_wait = max(0.2, max_sec if max_sec is not None else settings.ALMEMO_DRAIN_MAX_SEC)
        quiet_wait = max(0.05, quiet_sec if quiet_sec is not None else settings.ALMEMO_DRAIN_QUIET_SEC)
        previous_timeout = ser.timeout
        discarded = 0
        try:
            ser.timeout = min(0.05, quiet_wait / 2.0)
            deadline = time.monotonic() + max_wait
            last_rx = time.monotonic()
            while time.monotonic() < deadline:
                chunk = ser.read(256)
                if chunk:
                    discarded += len(chunk)
                    last_rx = time.monotonic()
                    continue
                if time.monotonic() - last_rx >= quiet_wait:
                    break
        finally:
            ser.timeout = previous_timeout
        return discarded

    def _prepare_serial_link(self, ser: serial.Serial, full_reset: bool = False) -> None:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        # Explicit XON: if device was paused by XOFF (OS buffer full), reset_input_buffer()
        # empties the buffer but does NOT send XON — device stays frozen until power cycle.
        ser.write(b"\x11")
        time.sleep(0.03)
        if full_reset:
            # Only do aggressive stop if we are about to start a real session
            for _ in range(2):
                self._write_line(ser, "C11", sleep_sec=0.1)
                self._discard_input_until_quiet(ser, max_sec=0.6, quiet_sec=0.15)

        # Select address 00 (broadcast/default)
        self._write_line(ser, "G00", sleep_sec=0.05)
        self._discard_input_until_quiet(ser, max_sec=0.3, quiet_sec=0.1)

    def _update_protocol_version(self, version_str: str | None) -> None:
        if not version_str:
            return
        # ALMEMO V6 versions are reported with a 6.xx software version.
        # Example version: "8590-9KL 6.52" or "2490-1 V7 7.20"
        match = re.search(r"(\d)\.\d{2}", version_str)
        with self.VERSION_LOCK:
            if match and match.group(1) == "6":
                self.ALMEMO_PROTOCOL_VERSION = "V6"
            else:
                self.ALMEMO_PROTOCOL_VERSION = "V7"
        self._log_serial_event(f"Protocol detected: {self.ALMEMO_PROTOCOL_VERSION} based on version '{version_str}'")

    def _probe_device(self, ser: serial.Serial, timeout_ms: int, full_reset: bool = False) -> tuple[str | None, list[str]]:
        self._prepare_serial_link(ser, full_reset=full_reset)
        self._write_line(ser, "t0", sleep_sec=0.05)
        lines = self._read_lines(ser, 3, timeout_ms)
        version = protocol._extract_device_version(lines)
        if version:
            self._update_protocol_version(version)
        return version, lines

    def _open_serial_on_port(self,
        port: str,
        timeout_ms: int,
        xonxoff: bool = True,
    ) -> serial.Serial:
        timeout_s = max(0.05, float(timeout_ms) / 1000.0)

        return serial.Serial(
            port,
            baudrate=settings.ALMEMO_BAUD,
            timeout=timeout_s,
            write_timeout=timeout_s,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=xonxoff,
            exclusive=True,
        )

    def _open_verified_serial(self,
        timeout_ms: int,
        xonxoff: bool = True,
    ) -> tuple[serial.Serial, str]:
        errors: list[str] = []
        probe_timeout_ms = max(timeout_ms, settings.ALMEMO_PROBE_TIMEOUT_MS)
        for port in self._candidate_ports():
            if not os.path.exists(port):
                continue
            ser: serial.Serial | None = None
            version: str | None = None
            try:
                ser = self._open_serial_on_port(port, timeout_ms, xonxoff=xonxoff)
                version, lines = self._probe_device(ser, probe_timeout_ms, full_reset=True)
                if version:
                    self._mark_device_ok()
                    self._log_serial_event(f"ALMEMO probe ok on {port}: {version}")
                    return ser, port
                details = ", ".join(protocol._normalize_line_text(line) for line in lines if protocol._normalize_line_text(line))
                errors.append(f"{port}: no ALMEMO version response ({details or 'empty'})")
            except Exception as exc:
                errors.append(f"{port}: {exc}")
            finally:
                if ser is not None and not version:
                    try:
                        ser.close()
                    except Exception:
                        pass
        if not errors:
            message = "No serial candidates available for ALMEMO"
            self._mark_device_missing(message)
            raise serial.SerialException(message)
        message = "ALMEMO 2490-2 not responding on serial candidates: " + "; ".join(errors)
        self._mark_device_missing(message)
        raise serial.SerialException(message)

    def _get_pser(self) -> serial.Serial:
        """Return persistent serial connection; open and drain if needed. Caller must hold SERIAL_LOCK."""
        recent_missing = self._recent_missing_reason(settings.ALMEMO_MISSING_CACHE_SEC)
        if recent_missing:
            raise serial.SerialException(recent_missing)
        port = self._resolve_port()
        if self._PSER is not None and self._PSER.is_open and self._PSER_PORT == port:
            return self._PSER
        if self._PSER is not None:
            try:
                self._PSER.close()
            except Exception:
                pass
            self._PSER = None
        ser, verified_port = self._open_verified_serial(settings.ALMEMO_TIMEOUT_MS, xonxoff=True)
        self._PSER = ser
        self._PSER_PORT = verified_port
        return self._PSER

    def _close_pser(self) -> None:
        """Close persistent serial connection. Caller must hold SERIAL_LOCK."""
        if self._PSER is not None:
            try:
                self._PSER.close()
            except Exception:
                pass
            self._PSER = None
            self._PSER_PORT = ""

    def _read_lines(self, ser: serial.Serial, read_lines: int, timeout_ms: int) -> list[str]:
        lines: list[str] = []
        deadline = time.monotonic() + max(0.05, float(timeout_ms) / 1000.0)

        while True:
            if read_lines > 0 and len(lines) >= read_lines:
                break
            if time.monotonic() >= deadline:
                break
            raw = ser.readline()
            if not raw:
                continue
            line = protocol._normalize_line_text(raw.decode("utf-8", errors="replace"))
            if line:
                lines.append(line)
        return lines

    def _send_command(self,
        command: str,
        *,
        timeout_ms: int,
        read_lines: int,
        raw: bool,
        eol: str | None = None,
    ) -> dict[str, Any]:
        result = self._send_command_sequence(
            [
                {
                    "command": command,
                    "timeout_ms": timeout_ms,
                    "read_lines": read_lines,
                    "raw": raw,
                    "eol": eol,
                }
            ]
        )
        sequence = result.get("sequence")
        if isinstance(sequence, list) and len(sequence) == 1:
            return sequence[0]
        return result

    def _send_command_sequence(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        if not steps:
            return {"ok": False, "error": "missing steps", "sequence": []}

        normalized_steps: list[dict[str, Any]] = []
        for step in steps:
            command = str(step.get("command", "")).strip()
            if not command:
                return {"ok": False, "error": "missing command", "sequence": []}
            normalized_steps.append(
                {
                    "command": command,
                    "timeout_ms": int(step.get("timeout_ms", settings.ALMEMO_TIMEOUT_MS)),
                    "read_lines": int(step.get("read_lines", 0)),
                    "raw": bool(step.get("raw", False)),
                    "eol": step.get("eol"),
                }
            )

        sequence: list[dict[str, Any]] = []

        with self.SESSION_SWITCH_LOCK:
            paused_streams = self.streams._pause_streaming_sessions()
            try:
                with self.SERIAL_LOCK:
                    if paused_streams:
                        try:
                            self._rearm_command_session_after_stream_pause(paused_streams)
                        except Exception as exc:
                            self._close_pser()
                            return {"ok": False, "error": f"ALMEMO session switch failed: {exc}", "sequence": sequence}
                    for idx, step in enumerate(normalized_steps):
                        is_write_only_step = step["read_lines"] <= 0
                        if idx == 0:
                            pre_drain_max_sec = 0.35
                            pre_drain_quiet_sec = 0.10
                        elif is_write_only_step:
                            pre_drain_max_sec = 0.06
                            pre_drain_quiet_sec = 0.03
                        else:
                            pre_drain_max_sec = 0.15
                            pre_drain_quiet_sec = 0.05
                        result = self._send_command_on_active_serial(
                            step["command"],
                            timeout_ms=step["timeout_ms"],
                            read_lines=step["read_lines"],
                            raw=step["raw"],
                            eol=step["eol"],
                            pre_drain_max_sec=pre_drain_max_sec,
                            pre_drain_quiet_sec=pre_drain_quiet_sec,
                        )
                        sequence.append(result)
                        if not result.get("ok"):
                            return {
                                "ok": False,
                                "error": result.get("error", "command failed"),
                                "failed_command": step["command"],
                                "sequence": sequence,
                            }
                    return {"ok": True, "sequence": sequence}
            finally:
                self.streams._resume_streaming_sessions(paused_streams)

    def _rearm_command_session_after_stream_pause(self, state: dict[str, str]) -> None:
        """Re-open a clean command session after stopping streaming readers. Caller must hold SERIAL_LOCK."""
        if not state:
            return
        active_modes = ",".join(sorted(state.keys()))
        self._log_serial_event(f"session switch: stopping {active_modes}, re-arming command channel")
        self._close_pser()
        ser = self._get_pser()
        self._discard_input_until_quiet(ser, max_sec=0.35, quiet_sec=0.10)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write(b"\x11")
        time.sleep(0.03)

    def _send_command_on_active_serial(self,
        command: str,
        *,
        timeout_ms: int,
        read_lines: int,
        raw: bool,
        eol: str | None = None,
        pre_drain_max_sec: float = 0.35,
        pre_drain_quiet_sec: float = 0.10,
    ) -> dict[str, Any]:
        payload = command
        if not raw:
            payload += protocol._decode_eol(eol if eol is not None else settings.ALMEMO_EOL)

        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                ser = self._get_pser()
                self._discard_input_until_quiet(ser, max_sec=pre_drain_max_sec, quiet_sec=pre_drain_quiet_sec)
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                # reset_input_buffer() clears the OS-side queue but does not
                # release a device paused by XOFF, so send XON before the next command.
                ser.write(b"\x11")
                time.sleep(0.03)
                time.sleep(0.02)
                ser.write(payload.encode("utf-8"))
                time.sleep(0.02)
                lines = self._read_lines(ser, read_lines, timeout_ms)
                if read_lines > 0 and not lines:
                    last_exc = serial.SerialException("No response from ALMEMO device")
                    self._mark_device_missing(str(last_exc))
                    self._log_serial_event("No response to command, forcing ALMEMO reconnect")
                    self._close_pser()
                    break
                if command == "t0" or lines:
                    self._mark_device_ok()
                return {
                    "ok": True,
                    "command": command,
                    "lines": lines,
                    "raw": "\n".join(lines),
                }
            except Exception as exc:
                last_exc = exc
                self._close_pser()
        return {"ok": False, "command": command, "error": str(last_exc), "lines": [], "raw": ""}

    def _startup_connect(self) -> None:
        time.sleep(2.0)
        with self.SESSION_SWITCH_LOCK:
            if self.streams._live_is_running() or self.streams._fast_live_is_running():
                return
            if self._PSER is not None and self._PSER.is_open:
                return
            with self.SERIAL_LOCK:
                try:
                    self._get_pser()
                except Exception:
                    pass
