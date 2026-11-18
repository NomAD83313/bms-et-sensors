"""Simulated WTVB01-485 on a virtual serial port, for test sessions without a machine.

Select the port `sim://roll?speed=600&diameter=600` (query optional) in the Bus form.
The object behaves like a pyserial port carrying one sensor at address 0x50:

- register reads (0x03) and writes (0x06) answer at once with plausible values derived
  from the machine model (the sensor's internal filtering is not modelled);
- a raw FIFO request (0x2C) produces progress frames every 250 ms and the 1024-sample
  data frame after 1024 / 246.3 s, delivered at the line rate of the selected baud, so the
  collector's real capture path, timing and sample-rate estimate are exercised.

Machine model: a directly driven roll whose real speed is 1 % below the nominal line
speed (slip) and drifts by +-0.3 % over 5 minutes. `scenario=faults` adds a 30 minute
sequence of faults (FAULT_STAGES). Vibration (peak velocity, mm/s) at
roll orders per axis, a foreign 24.5 Hz line on Z that does not follow the speed
(e.g. a neighbouring pump), gravity on Z and white noise.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

try:
    from . import wtvb01_protocol as proto
except ImportError:  # Flat import inside the container.
    import wtvb01_protocol as proto

SIM_ADDRESS = 0x50
SIM_SAMPLE_RATE = 246.3
PROGRESS_INTERVAL_SEC = 0.25
REPLY_LATENCY_SEC = 0.004
STANDARD_GRAVITY_MM_S2 = 9806.65
NOISE_G = 0.0003
SLIP = 0.99
DRIFT = 0.003
DRIFT_PERIOD_SEC = 300.0
# Peak velocity amplitude in mm/s per axis at multiples of the roll frequency.
ORDER_VELOCITY_MM_S = {
    "x": {0.5: 0.15, 1.0: 2.5, 2.0: 1.0, 3.0: 0.4},
    "y": {0.5: 0.10, 1.0: 1.8, 2.0: 1.4, 3.0: 0.5},
    "z": {0.5: 0.05, 1.0: 0.8, 2.0: 0.4, 3.0: 0.2},
}
FOREIGN_LINES = {"z": ((24.5, 0.3),)}  # (Hz, peak mm/s) independent of the roll speed.

# Fault scenario `scenario=faults` (30 min, counted from the first time the port is opened
# by this process). Each fault appears in its own window and disappears afterwards, so a
# trend change can be attributed to one stage. (start s, end s, name)
FAULT_STAGES = (
    (0, 240, "normal"),
    (240, 540, "unbalance"),        # 1x X/Y ramps 2.5 -> 7 / 1.8 -> 5 mm/s
    (540, 840, "misalignment"),     # 2x ramps to the 1x level, 3x x2.5, mostly Y
    (840, 1080, "speed 450 m/min"), # line slows to 75 %; machine data still says 600
    (1080, 1380, "looseness"),      # 0.5x, 1.5x, 2.5x and 4x-6x harmonics
    (1380, 1620, "bearing impacts"),  # impacts at 3.6x exciting a 90 Hz resonance
    (1620, 1740, "communication faults"),  # corrupted FIFO frames, lost register replies
    (1740, 1800, "normal"),
)
SCENARIO_LENGTH_SEC = FAULT_STAGES[-1][1]
BEARING_ORDER = 3.6
BEARING_RESONANCE_HZ = 90.0
BEARING_IMPACT_G = 0.06
BEARING_DECAY_SEC = 0.012
COMM_FIFO_CORRUPT_RATE = 0.5
COMM_REPLY_LOSS_RATE = 0.15
# Scenario start per port string: reopening the port (e.g. after an I/O error) continues the
# scenario; append e.g. `&run=2` to the port to start it again.
_SCENARIO_START: dict[str, float] = {}


def _crc(body: bytes) -> bytes:
    crc = proto.crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def _parse(port: str) -> tuple[float, float, str]:
    query = parse_qs(urlparse(port).query)

    def number(key: str, default: float) -> float:
        try:
            return float(query.get(key, [default])[0])
        except ValueError:
            return default
    return number("speed", 600.0), number("diameter", 600.0), query.get("scenario", ["steady"])[0]


def stage_at(scenario: str, t: float) -> tuple[str, float]:
    """(stage name, progress 0..1 within the stage) at scenario time t."""
    if scenario != "faults":
        return "steady", 0.0
    for start, end, name in FAULT_STAGES:
        if start <= t < end:
            return name, (t - start) / (end - start)
    return "normal (scenario finished)", 1.0


class SimulatedSerial:
    def __init__(self, port: str, baudrate: int, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, seed: int | None = None):
        self.port = port
        self.baudrate = baudrate
        self.timeout: float | None = None
        self.write_timeout = 1.0
        self.line_speed_m_min, self.roll_diameter_mm, self.scenario = _parse(port)
        self._clock = clock
        self._sleep = sleep
        self._random = random.Random(seed)
        self._started = _SCENARIO_START.setdefault(port, clock()) if self.scenario != "steady" else clock()
        self._pending: list[tuple[float, bytes]] = []  # (deliver at, bytes), sorted
        self._fifo_busy_until = 0.0
        self.closed = False

    # --- machine model ---------------------------------------------------------
    def stage(self, t: float) -> tuple[str, float]:
        return stage_at(self.scenario, t)

    def roll_hz(self, t: float) -> float:
        nominal = self.line_speed_m_min / (math.pi * self.roll_diameter_mm / 1000.0) / 60.0
        if self.stage(t)[0] == "speed 450 m/min":
            nominal *= 0.75
        return nominal * SLIP * (1 + DRIFT * math.sin(2 * math.pi * t / DRIFT_PERIOD_SEC))

    def components(self, t: float) -> dict[str, list[tuple[float, float]]]:
        """Per axis: (frequency Hz, peak velocity mm/s), including the active fault."""
        f1 = self.roll_hz(t)
        name, progress = self.stage(t)
        lines = {}
        for axis, orders in ORDER_VELOCITY_MM_S.items():
            orders = dict(orders)
            if name == "unbalance" and axis in "xy":
                orders[1.0] *= 1 + (7.0 / 2.5 - 1) * progress
            elif name == "misalignment":
                orders[2.0] += (orders[1.0] * 1.05 - orders[2.0]) * progress if axis != "z" else 0.8 * progress
                orders[3.0] *= 1 + 1.5 * progress
            elif name == "looseness":
                orders[0.5] *= 5
                orders.update({1.5: 0.5, 2.5: 0.5, 4.0: 0.4, 5.0: 0.3, 6.0: 0.25})
            lines[axis] = [(f1 * k, v) for k, v in orders.items()] + list(FOREIGN_LINES.get(axis, ()))
        return lines

    def _impacts(self, t0: float) -> list[float]:
        """Bearing impact signal in g per sample (decaying 90 Hz bursts at 3.6x)."""
        if self.stage(t0)[0] != "bearing impacts":
            return [0.0] * proto.FIFO_SAMPLES
        period = 1.0 / (BEARING_ORDER * self.roll_hz(t0))
        offset = self._random.uniform(0, period)
        out = []
        for i in range(proto.FIFO_SAMPLES):
            since = (i / SIM_SAMPLE_RATE - offset) % period
            out.append(BEARING_IMPACT_G * math.exp(-since / BEARING_DECAY_SEC) * math.sin(2 * math.pi * BEARING_RESONANCE_HZ * since))
        return out

    def _samples(self, t0: float) -> list[tuple[int, int, int]]:
        """1024 raw FIFO samples as the sensor sends them (sign inverted, 2048 LSB per g)."""
        lines = self.components(t0)
        impacts = self._impacts(t0)
        phases = {axis: [self._random.uniform(0, 2 * math.pi) for _ in comps] for axis, comps in lines.items()}
        raw = []
        for i in range(proto.FIFO_SAMPLES):
            t = i / SIM_SAMPLE_RATE
            sample = []
            for axis in ("x", "y", "z"):
                g = 1.0 if axis == "z" else 0.0
                for (frequency, velocity), phase in zip(lines[axis], phases[axis]):
                    g += velocity * 2 * math.pi * frequency / STANDARD_GRAVITY_MM_S2 * math.sin(2 * math.pi * frequency * t + phase)
                g += self._random.gauss(0.0, NOISE_G)
                if axis != "x":  # Radial impacts show on Y and Z.
                    g += impacts[i]
                sample.append(max(-32768, min(32767, round(-g / proto.ACC_SCALE_G))))
            raw.append(tuple(sample))
        return raw  # type: ignore[return-value]

    def registers(self, t: float) -> dict[int, int]:
        """Register image for the read blocks (approximate, unfiltered)."""
        lines = self.components(t)
        regs: dict[int, int] = {proto.REG_ADDRESS: SIM_ADDRESS, proto.REG_BAUD: proto.BAUD_CODES.get(self.baudrate, 2)}
        for index, axis in enumerate("xyz"):
            comps = lines[axis]
            vel_rms = math.sqrt(sum(v * v for _, v in comps) / 2)
            acc_rms = math.sqrt(sum((v * 2 * math.pi * f / STANDARD_GRAVITY_MM_S2) ** 2 for f, v in comps) / 2)
            disp_um = sum(v / (2 * math.pi * f) for f, v in comps) * 1000
            dominant = max(comps, key=lambda c: c[1])[0]
            gravity = 1.0 if axis == "z" else 0.0
            regs[0x34 + index] = round(gravity / proto.ACC_SCALE_G) & 0xFFFF
            regs[0x3A + index] = round(vel_rms * math.sqrt(2) * 100)
            regs[0x41 + index] = round(disp_um)
            regs[0x44 + index] = round(dominant * 10)
            base = (0x47, 0x53, 0x5F)[index]
            impacts = self.stage(t)[0] == "bearing impacts" and axis != "x"
            crest, kurtosis = (4.2, 6.5) if impacts else (1.414, 1.5)
            stats = (crest, kurtosis, acc_rms * 0.9, acc_rms ** 2, acc_rms, 1.11, 1.57, 1.75, 0.0, vel_rms, 1.5,
                     disp_um / 1000 / math.sqrt(2))
            for offset, value in enumerate(stats):
                regs[base + offset] = round(value * 1000) & 0xFFFF
            energy = (0x6E, 0x7D, 0x8C)[index]
            f1 = self.roll_hz(t)
            for point, multiple in enumerate((0.25, 0.5, 1, 2, 3, 4, 5, 6)):
                regs[energy + point] = round(sum(v * v for f, v in comps if abs(f - f1 * multiple) < 0.2) * 100)
            edges = ((0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 4), (4, 5))
            for band, (low, high) in enumerate(edges):
                regs[energy + 8 + band] = round(sum(v * v for f, v in comps if f1 * low < f <= f1 * high + 0.01) * 100)
            regs[energy + 14] = round(sum(v * v for _, v in comps) * 100)
        regs[0x40] = 2650
        return regs

    # --- serial port interface -----------------------------------------------
    def _char_time(self) -> float:
        return 10.0 / float(self.baudrate)

    def _schedule(self, at: float, data: bytes) -> None:
        self._pending.append((at, data))
        self._pending.sort(key=lambda item: item[0])

    def reset_input_buffer(self) -> None:
        now = self._clock()
        self._pending = [(at, data) for at, data in self._pending if at > now]

    def write(self, frame: bytes) -> int:
        frame = bytes(frame)
        now = self._clock()
        if len(frame) < 8 or frame[0] != SIM_ADDRESS or proto.crc16(frame[:-2]) != frame[-2] | (frame[-1] << 8):
            return len(frame)
        function, register, value = frame[1], (frame[2] << 8) | frame[3], (frame[4] << 8) | frame[5]
        done = now + len(frame) * self._char_time() + REPLY_LATENCY_SEC
        if function == proto.FUNC_READ and register == proto.REG_FIFO:
            if now >= self._fifo_busy_until:
                self._start_fifo(done)
        elif function == proto.FUNC_READ:
            if self._comm_fault(now) and self._random.random() < COMM_REPLY_LOSS_RATE:
                return len(frame)  # Reply lost.
            regs = self.registers(now - self._started)
            words = b"".join((regs.get(register + i, 0) & 0xFFFF).to_bytes(2, "big") for i in range(value))
            reply = _crc(bytes((SIM_ADDRESS, proto.FUNC_READ, 2 * value)) + words)
            self._schedule(done + len(reply) * self._char_time(), reply)
        elif function == proto.FUNC_WRITE:
            self._schedule(done + len(frame) * self._char_time(), frame)
        return len(frame)

    def _comm_fault(self, now: float) -> bool:
        return self.stage(now - self._started)[0] == "communication faults"

    def _start_fifo(self, start: float) -> None:
        duration = proto.FIFO_SAMPLES / SIM_SAMPLE_RATE
        tick = PROGRESS_INTERVAL_SEC
        while tick < duration:
            count = 3 + 6 * int(tick * SIM_SAMPLE_RATE)
            frame = _crc(bytes((SIM_ADDRESS, proto.FUNC_READ, proto.FIFO_PROGRESS_MARK, count >> 8, count & 0xFF)))
            self._schedule(start + tick, frame)
            tick += PROGRESS_INTERVAL_SEC
        payload = b"".join(v.to_bytes(2, "big", signed=True) for s in self._samples(start - self._started) for v in s)
        data = _crc(bytes((SIM_ADDRESS, proto.FUNC_READ, proto.FIFO_DATA_MARK)) + payload)
        if self._comm_fault(start) and self._random.random() < COMM_FIFO_CORRUPT_RATE:
            corrupted = bytearray(data)
            corrupted[len(data) // 2] ^= 0x5A
            data = bytes(corrupted)
        chunk = 256
        for offset in range(0, len(data), chunk):
            self._schedule(start + duration + (offset + chunk) * self._char_time(), data[offset:offset + chunk])
        self._fifo_busy_until = start + duration + len(data) * self._char_time()

    def read(self, size: int = 1) -> bytes:
        deadline = self._clock() + (self.timeout if self.timeout is not None else 1.0)
        out = bytearray()
        while True:
            now = self._clock()
            while self._pending and self._pending[0][0] <= now and len(out) < size:
                at, data = self._pending.pop(0)
                take = data[:size - len(out)]
                out += take
                if len(take) < len(data):
                    self._pending.insert(0, (at, data[len(take):]))
            # Like pyserial: return when `size` bytes arrived or the timeout expired.
            if len(out) >= size or now >= deadline:
                return bytes(out)
            wait_until = min(deadline, self._pending[0][0]) if self._pending else deadline
            self._sleep(max(0.0, min(wait_until - now, 0.05)))

    def close(self) -> None:
        self.closed = True


def opener(port: str, baud: int) -> Any:
    return SimulatedSerial(port, baud)


def scenario_status(port: str, now: float | None = None) -> dict[str, Any] | None:
    """Current scenario stage for the status API, or None for a steady simulation."""
    _speed, _diameter, scenario = _parse(port)
    if scenario == "steady":
        return {"scenario": "steady"}
    started = _SCENARIO_START.get(port)
    if started is None:
        return {"scenario": scenario, "stage": "not started"}
    elapsed = (time.monotonic() if now is None else now) - started
    name, progress = stage_at(scenario, elapsed)
    return {"scenario": scenario, "stage": name, "stage_progress": round(progress, 2),
            "elapsed_s": round(elapsed), "length_s": SCENARIO_LENGTH_SEC}
