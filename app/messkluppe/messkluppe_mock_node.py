from __future__ import annotations

import math
import time
from dataclasses import dataclass

try:
    from .messkluppe_protocol import TASK_FILE_DOWNLOAD, make_id_task, words16_to_legacy_payload
except ImportError:
    from messkluppe_protocol import TASK_FILE_DOWNLOAD, make_id_task, words16_to_legacy_payload


DEFAULT_MOCK_CLIP_ID = 1
DEFAULT_MOCK_FILE_ID = "mock-node"


@dataclass(frozen=True)
class MockNodeSample:
    payload: bytes
    file_id: str
    seq: int
    unix_time: int


def _uint16(value: int) -> int:
    return int(value) & 0xFFFF


def _signed_wave(seq: int, *, base: int, amplitude: int, phase: float = 0.0) -> int:
    radians = (int(seq) / 12.0) + phase
    return _uint16(round(base + math.sin(radians) * amplitude))


def build_mock_node_payload(*, seq: int, unix_time: int | None = None, clip_id: int = DEFAULT_MOCK_CLIP_ID) -> bytes:
    timestamp = int(time.time()) if unix_time is None else int(unix_time)
    sequence = max(0, int(seq))
    sensor_ms = (sequence * 137) % 1000
    line_number = sequence

    words = [
        make_id_task(clip_id, TASK_FILE_DOWNLOAD),
        (timestamp >> 16) & 0xFFFF,
        timestamp & 0xFFFF,
        (line_number >> 16) & 0xFFFF,
        line_number & 0xFFFF,
        sensor_ms,
        _signed_wave(sequence, base=2000, amplitude=180),
        _signed_wave(sequence, base=2010, amplitude=140, phase=1.7),
        _signed_wave(sequence, base=2020, amplitude=110, phase=3.4),
        _uint16((sequence * 3) - 90),
        _uint16((sequence * 2) - 50),
        _uint16((sequence * 7) % 3600),
        _uint16(2450 + ((sequence % 8) * 3)),
        _uint16(2600 + ((sequence % 10) * 2)),
        _uint16(3700 - (sequence % 60)),
        0,
    ]
    return words16_to_legacy_payload(words)


def build_mock_node_sample(*, seq: int, unix_time: int | None = None, clip_id: int = DEFAULT_MOCK_CLIP_ID) -> MockNodeSample:
    timestamp = int(time.time()) if unix_time is None else int(unix_time)
    return MockNodeSample(
        payload=build_mock_node_payload(seq=seq, unix_time=timestamp, clip_id=clip_id),
        file_id=DEFAULT_MOCK_FILE_ID,
        seq=max(0, int(seq)),
        unix_time=timestamp,
    )
