from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


PAYLOAD_SIZE = 32
LEGACY_PACKET_WORDS = 16
HOST_COMMAND_WORDS = 8
DEFAULT_CLIP_ID = 1

TASK_IDLE = 0
TASK_DEEP_SLEEP = 10
TASK_LOGGING = 20
TASK_FILE_LIST = 30
TASK_FILE_LIST_DONE = 39
TASK_FILE_DOWNLOAD = 40
TASK_FILE_DOWNLOAD_DONE = 49
TASK_FILE_DELETE = 50
TASK_FILE_DELETE_ALL = 51
TASK_FILE_DELETED = 59
TASK_LIVE_DATA = 60
TASK_ACK = 99


@dataclass(frozen=True)
class IdTask:
    clip_id: int
    task: int

    @property
    def value(self) -> int:
        return make_id_task(self.clip_id, self.task)


@dataclass(frozen=True)
class PingPacket:
    clip_id: int
    task: int
    timestamp_ms: int
    ping_ms: int
    success_percent_x100: int
    file_count: int
    raw_words: tuple[int, ...]


@dataclass(frozen=True)
class FileDataPacket:
    clip_id: int
    task: int
    unix_time: int
    line_number: int
    values: tuple[int, ...]
    raw_words: tuple[int, ...]


@dataclass(frozen=True)
class LiveDataPacket:
    clip_id: int
    task: int
    timestamp_ms: int
    values: tuple[int, ...]
    raw_words: tuple[int, ...]


def _require_uint(value: int, bits: int, name: str) -> int:
    item = int(value)
    limit = 1 << bits
    if item < 0 or item >= limit:
        raise ValueError(f"{name} must fit in unsigned {bits}-bit range")
    return item


def make_id_task(clip_id: int, task: int) -> int:
    clip = _require_uint(clip_id, 16, "clip_id")
    task_value = _require_uint(task, 16, "task")
    if task_value >= 1000:
        raise ValueError("task must be less than 1000")
    return clip * 1000 + task_value


def split_id_task(value: int) -> IdTask:
    item = _require_uint(value, 16, "id_task")
    task = item % 1000
    return IdTask(clip_id=(item - task) // 1000, task=task)


def words_to_radio_bytes_32(words: Sequence[int]) -> bytes:
    if len(words) > HOST_COMMAND_WORDS:
        raise ValueError("host command can contain at most 8 unsigned 32-bit words")
    output = bytearray()
    for idx, word in enumerate(words):
        item = _require_uint(word, 32, f"word[{idx}]")
        output.extend(item.to_bytes(4, byteorder="big")[::-1])
    return bytes(output)


def radio_bytes_to_words_16(payload: bytes | bytearray | Sequence[int]) -> tuple[int, ...]:
    raw = bytes(payload)
    if len(raw) != PAYLOAD_SIZE:
        raise ValueError("radio payload must be exactly 32 bytes")
    return tuple(int.from_bytes((raw[idx + 1], raw[idx]), byteorder="big") for idx in range(0, PAYLOAD_SIZE, 2))


def radio_bytes_to_file_words(payload: bytes | bytearray | Sequence[int]) -> tuple[int, ...]:
    words = radio_bytes_to_words_16(payload)
    return (
        words[0],
        (words[1] << 16) + words[2],
        (words[3] << 16) + words[4],
        *words[5:16],
    )


def decode_ping_packet(payload: bytes | bytearray | Sequence[int]) -> PingPacket:
    words = radio_bytes_to_words_16(payload)
    id_task = split_id_task(words[0])
    return PingPacket(
        clip_id=id_task.clip_id,
        task=id_task.task,
        timestamp_ms=(words[1] << 16) + words[2],
        ping_ms=words[3],
        success_percent_x100=words[4],
        file_count=words[5],
        raw_words=words,
    )


def decode_file_data_packet(payload: bytes | bytearray | Sequence[int]) -> FileDataPacket:
    words = radio_bytes_to_file_words(payload)
    id_task = split_id_task(words[0])
    return FileDataPacket(
        clip_id=id_task.clip_id,
        task=id_task.task,
        unix_time=words[1],
        line_number=words[2],
        values=tuple(words[3:]),
        raw_words=words,
    )


def decode_live_data_packet(payload: bytes | bytearray | Sequence[int]) -> LiveDataPacket:
    words = radio_bytes_to_words_16(payload)
    id_task = split_id_task(words[0])
    return LiveDataPacket(
        clip_id=id_task.clip_id,
        task=id_task.task,
        timestamp_ms=(words[1] << 16) + words[2],
        values=tuple(words[5:14]),
        raw_words=words,
    )


def build_ping_command(clip_id: int, timestamp_ms: int = 0) -> bytes:
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_IDLE), timestamp_ms])


def build_logging_command(clip_id: int, timestamp_ms: int, unix_time: int, sample_rate: int, logging_time: int) -> bytes:
    return words_to_radio_bytes_32([
        make_id_task(clip_id, TASK_LOGGING),
        timestamp_ms,
        unix_time,
        sample_rate,
        logging_time,
    ])


def build_file_list_command(clip_id: int, timestamp_ms: int) -> bytes:
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_FILE_LIST), timestamp_ms, 0, 0, 0, 0, 0, 0])


def build_file_download_command(clip_id: int, timestamp_ms: int, filename_epoch: int, first_line: int, line_count: int) -> bytes:
    return words_to_radio_bytes_32([
        make_id_task(clip_id, TASK_FILE_DOWNLOAD),
        timestamp_ms,
        0,
        filename_epoch,
        first_line,
        int(line_count) >> 16,
        int(line_count) & 0xFFFF,
    ])


def build_delete_file_command(clip_id: int, timestamp_ms: int, filename_epoch: int) -> bytes:
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_FILE_DELETE), timestamp_ms, 0, filename_epoch])


def build_delete_all_command(clip_id: int, timestamp_ms: int) -> bytes:
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_FILE_DELETE_ALL), timestamp_ms, 0])


def build_ack_command(clip_id: int, timestamp_ms: int) -> bytes:
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_ACK), timestamp_ms])


def words16_to_legacy_payload(words: Iterable[int]) -> bytes:
    items = tuple(words)
    if len(items) != LEGACY_PACKET_WORDS:
        raise ValueError("legacy payload must contain exactly 16 unsigned 16-bit words")
    output = bytearray()
    for idx, word in enumerate(items):
        item = _require_uint(word, 16, f"word[{idx}]")
        output.extend((item & 0xFF, (item >> 8) & 0xFF))
    return bytes(output)
