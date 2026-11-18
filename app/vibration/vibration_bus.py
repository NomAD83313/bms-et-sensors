"""Serial Modbus RTU bus shared by all WTVB01-485 sensors on one RS485 line.

All transactions run under one lock, so polling pauses automatically while a
scan or configuration change owns the bus. The serial opener is injectable for
tests; production uses pyserial with 8N1 framing.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

try:
    from . import wtvb01_protocol as proto
except ImportError:  # Flat import inside the container.
    import wtvb01_protocol as proto

# Reply latency beyond the frame's own transmit time. A WTVB01 occasionally pauses
# mid-reply while computing statistics, so this is generous; scans use their own value.
BASE_TIMEOUT_SEC = 0.15
QUIET_GAP_SEC = 0.03
QUIET_MAX_SEC = 0.5
SCAN_TIMEOUT_SEC = 0.06
# A CH340 adapter with a WTVB01 dropped the first request right after the port opened.
OPEN_SETTLE_SEC = 0.2
BITS_PER_CHAR = 10
# FIFO capture: read in short slices so progress frames get timestamps close to arrival.
FIFO_READ_SLICE_SEC = 0.02
FIFO_READ_CHUNK = 8192
FIFO_MARGIN_SEC = 2.0


class BusError(Exception):
    """No usable answer: timeout, port problem or protocol error."""


def pyserial_opener(port: str, baud: int) -> Any:
    import serial  # Imported lazily so protocol tests do not need pyserial.

    return serial.Serial(port=port, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                         timeout=BASE_TIMEOUT_SEC, write_timeout=1.0)


def find_header(data: bytes, address: int, function: int) -> int:
    """Index of the first plausible response header, or -1.

    Auto-direction RS485 adapters occasionally emit a stray byte (seen as 0x00)
    when the line turns around; the real frame follows it.
    """
    for index in range(len(data) - 1):
        if data[index] == address and data[index + 1] in (function, function | 0x80):
            return index
    return -1


class ModbusBus:
    def __init__(self, opener: Callable[[str, int], Any] = pyserial_opener, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._serial: Any = None
        self._port = ""
        self._baud = proto.DEFAULT_BAUD
        self.lock = threading.RLock()

    # --- port lifecycle -------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._serial is not None

    @property
    def baud(self) -> int:
        return self._baud

    def open(self, port: str, baud: int) -> None:
        with self.lock:
            if self._serial is not None and port == self._port and baud == self._baud:
                return
            self.close()
            if not port:
                raise BusError("no serial port configured")
            try:
                self._serial = self._opener(port, baud)
                self._sleep(OPEN_SETTLE_SEC)
                self._serial.reset_input_buffer()
            except Exception as exc:
                self.close()
                raise BusError(f"cannot open {port}: {exc}") from exc
            self._port, self._baud = port, baud

    def close(self) -> None:
        with self.lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

    def switch_baud(self, baud: int) -> None:
        self._serial.baudrate = baud
        self._baud = baud

    # --- transactions ---------------------------------------------------------
    def _char_time(self) -> float:
        return BITS_PER_CHAR / float(self._baud)

    def _transact(self, request: bytes, expected: int, timeout: float | None) -> bytes:
        if self._serial is None:
            raise BusError("serial port is not open")
        ser = self._serial
        wait = (timeout if timeout is not None else BASE_TIMEOUT_SEC) + (len(request) + expected) * self._char_time()
        try:
            ser.reset_input_buffer()
            ser.write(request)
            ser.timeout = wait
            data = bytes(ser.read(expected))
            start = find_header(data, request[0], request[1])
            if start > 0:
                data = data[start:] + bytes(ser.read(start))
        except Exception as exc:
            raise BusError(f"serial I/O error: {exc}") from exc
        finally:
            # Modbus RTU silent interval (3.5 characters, at least 2 ms) before the next frame.
            self._sleep(max(0.002, 3.5 * self._char_time()))
        if not data:
            raise BusError(f"no response from 0x{request[0]:02X}")
        return data

    def _wait_quiet(self) -> None:
        """Discard a late reply tail so a retry does not collide with it on half-duplex RS485."""
        ser = self._serial
        try:
            ser.timeout = QUIET_GAP_SEC
            deadline = time.monotonic() + QUIET_MAX_SEC
            while ser.read(256) and time.monotonic() < deadline:
                pass
        except Exception:
            pass

    def _with_retry(self, attempt: Callable[[], Any], retries: int) -> Any:
        """Repeat a transaction after a lost or corrupted reply; I/O errors are final."""
        while True:
            try:
                return attempt()
            except BusError as exc:
                if retries <= 0 or str(exc).startswith("serial I/O error"):
                    raise
                retries -= 1
                self._wait_quiet()

    def read_registers(self, address: int, register: int, count: int, timeout: float | None = None,
                       retries: int = 1) -> list[int]:
        request = proto.read_request(address, register, count)

        def attempt() -> list[int]:
            frame = self._transact(request, proto.read_response_length(count), timeout)
            try:
                return proto.parse_read_response(frame, address, count)
            except proto.ProtocolError as exc:
                raise BusError(f"0x{address:02X}: {exc}") from exc

        with self.lock:
            return self._with_retry(attempt, retries)

    def write_register(self, address: int, register: int, value: int, timeout: float | None = None,
                       retries: int = 1) -> None:
        # Repeating a write is safe: every configuration write sets an absolute value.
        request = proto.write_request(address, register, value)

        def attempt() -> None:
            frame = self._transact(request, proto.WRITE_RESPONSE_LENGTH, timeout)
            try:
                proto.parse_write_response(frame, request)
            except proto.ProtocolError as exc:
                raise BusError(f"0x{address:02X}: {exc}") from exc

        with self.lock:
            self._with_retry(attempt, retries)

    def read_profile(self, address: int, profile: str, skip: Iterable[int] = (),
                     rejected: set[int] | None = None) -> dict[str, float]:
        """Read every block of the profile except those whose first register is in skip.

        Blocks carrying the sensor's invalid marker are left out and added to rejected.
        """
        skipped = set(skip)
        values: dict[str, float] = {}
        with self.lock:
            for first, count, fields in proto.READ_PROFILES[profile]:
                if first in skipped:
                    continue
                registers = self.read_registers(address, first, count)
                if not proto.block_valid(first, registers):
                    if rejected is not None:
                        rejected.add(first)
                    continue
                values.update(proto.decode_fields(first, registers, fields))
                if first == proto.BLOCK_ENERGY[0]:
                    values.update(proto.derived_fields(values))
        return values

    def capture_fifo(self, address: int) -> dict[str, Any]:
        """Capture one raw acceleration FIFO (1024 samples per axis, about 4.2 s).

        The sensor sends progress frames by itself while capturing, so the whole
        capture holds the bus lock: no other sensor may be addressed meanwhile.
        Returns samples in g, progress points (host time, samples) and the
        estimated sample rate (None when it could not be measured).
        """
        with self.lock:
            if self._serial is None:
                raise BusError("serial port is not open")
            ser = self._serial
            deadline_sec = proto.FIFO_CAPTURE_SEC + proto.FIFO_FRAME_LENGTH * self._char_time() + FIFO_MARGIN_SEC
            progress: list[tuple[float, int]] = []
            tail = b""
            try:
                ser.reset_input_buffer()
                ser.write(proto.fifo_request(address))
                started = self._clock()
                ser.timeout = FIFO_READ_SLICE_SEC
                while self._clock() - started < deadline_sec:
                    chunk = bytes(ser.read(FIFO_READ_CHUNK))
                    if not chunk:
                        continue
                    now = self._clock() - started
                    events, tail = proto.split_fifo_stream(tail + chunk, address)
                    for kind, payload in events:
                        if kind == "progress":
                            progress.append((now, payload))
                        else:
                            return {"samples": payload, "progress": progress,
                                    "sample_rate": proto.estimate_sample_rate(progress),
                                    "elapsed_s": now}
            except proto.ProtocolError as exc:
                self._wait_quiet()
                raise BusError(f"0x{address:02X}: FIFO {exc}") from exc
            except Exception as exc:
                raise BusError(f"serial I/O error: {exc}") from exc
            finally:
                self._sleep(max(0.002, 3.5 * self._char_time()))
            self._wait_quiet()
            state = f"{progress[-1][1]} of {proto.FIFO_SAMPLES} samples" if progress else "no progress frames"
            raise BusError(f"0x{address:02X}: FIFO capture timed out ({state})")

    def probe(self, address: int, timeout: float = SCAN_TIMEOUT_SEC) -> bool:
        try:
            self.read_registers(address, proto.REG_ADDRESS, 1, timeout=timeout, retries=0)
            return True
        except BusError:
            return False

    def scan(self, addresses: Iterable[int]) -> list[int]:
        with self.lock:
            return [address for address in addresses if self.probe(address)]

    # --- configuration (manual 10.2: unlock, write, save) ---------------------
    def set_address(self, old: int, new: int) -> None:
        """Change a sensor's Modbus address; the new address answers immediately."""
        if not proto.valid_address(new):
            raise ValueError("invalid new address")
        with self.lock:
            if new != old and self.probe(new):
                raise BusError(f"address 0x{new:02X} is already in use")
            self.write_register(old, proto.REG_UNLOCK, proto.UNLOCK_KEY)
            self.write_register(old, proto.REG_ADDRESS, new)
            self.write_register(new, proto.REG_UNLOCK, proto.UNLOCK_KEY)
            self.write_register(new, proto.REG_SAVE, proto.SAVE_VALUE)
            if not self.probe(new, timeout=BASE_TIMEOUT_SEC):
                raise BusError(f"sensor did not answer at new address 0x{new:02X}")

    def set_baud(self, addresses: list[int], new_baud: int) -> dict[int, str]:
        """Move every listed sensor and the port to new_baud; returns per-address results.

        The sensor switches immediately after the BAUD write (its echo may arrive at
        either rate, so it is not checked); the save is sent at the new rate, as in
        the manual's example.
        """
        code = proto.baud_code(new_baud)
        results: dict[int, str] = {}
        with self.lock:
            if self._serial is None:
                raise BusError("serial port is not open")
            old_baud = self._baud
            for address in addresses:
                try:
                    self.switch_baud(old_baud)
                    self.write_register(address, proto.REG_UNLOCK, proto.UNLOCK_KEY)
                    try:
                        self.write_register(address, proto.REG_BAUD, code, retries=0)
                    except BusError:
                        pass
                    self.switch_baud(new_baud)
                    self.write_register(address, proto.REG_UNLOCK, proto.UNLOCK_KEY)
                    self.write_register(address, proto.REG_SAVE, proto.SAVE_VALUE)
                    results[address] = "ok"
                except BusError as exc:
                    results[address] = str(exc)
            self.switch_baud(new_baud)
            for address, result in results.items():
                if result == "ok" and not self.probe(address, timeout=BASE_TIMEOUT_SEC):
                    results[address] = f"no answer at {new_baud} baud"
        return results
