from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from .messkluppe_protocol import FileDataPacket, LiveDataPacket, PingPacket
except ImportError:
    from messkluppe_protocol import FileDataPacket, LiveDataPacket, PingPacket


DEFAULT_MEASUREMENT = "messkluppe_sensor"
RAW_VALUE_NAMES = (
    "sensor_ms",
    "force_x_raw",
    "force_y_raw",
    "force_z_raw",
    "accel_x_raw",
    "accel_y_raw",
    "yaw_raw",
    "imu_temperature_raw",
    "clip_temperature_raw",
    "battery_raw",
    "reserved_1",
    "reserved_2",
)
LIVE_VALUE_NAMES = (
    "force_x_raw",
    "force_y_raw",
    "force_z_raw",
    "accel_x_raw",
    "accel_y_raw",
    "yaw_raw",
    "imu_temperature_raw",
    "clip_temperature_raw",
    "battery_raw",
)


@dataclass(frozen=True)
class MesskluppeInfluxRecord:
    measurement: str
    tags: dict[str, str]
    fields: dict[str, int | float | str]
    time_ns: int | None = None


def _signed16(value: int) -> int:
    item = int(value) & 0xFFFF
    return item - 0x10000 if item & 0x8000 else item


def _yaw_deg(value: int) -> float:
    return round((_signed16(value) / 10.0) % 360.0, 3)


def _packet_time_ns(packet: FileDataPacket) -> int | None:
    if packet.unix_time <= 0:
        return None
    sensor_ms = int(packet.values[0]) if packet.values else 0
    if sensor_ms < 0 or sensor_ms > 999:
        sensor_ms = 0
    return (int(packet.unix_time) * 1_000_000_000) + (sensor_ms * 1_000_000)


def file_packet_to_fields(packet: FileDataPacket) -> dict[str, int | float | str]:
    fields: dict[str, int | float | str] = {
        "line": int(packet.line_number),
        "unix_time": int(packet.unix_time),
    }

    for idx, value in enumerate(packet.values):
        fields[f"raw_{idx:02d}"] = int(value)
        if idx < len(RAW_VALUE_NAMES):
            fields[RAW_VALUE_NAMES[idx]] = _signed16(value)

    if len(packet.values) > 6:
        fields["yaw_deg"] = _yaw_deg(packet.values[6])
    if len(packet.values) > 7:
        fields["imu_temperature_c"] = round(_signed16(packet.values[7]) / 100.0, 3)

    return fields


def live_packet_to_fields(packet: LiveDataPacket) -> dict[str, int | float | str]:
    fields: dict[str, int | float | str] = {
        "node_millis": int(packet.timestamp_ms),
    }
    for idx, value in enumerate(packet.values):
        fields[f"raw_{idx:02d}"] = int(value)
        if idx < len(LIVE_VALUE_NAMES):
            fields[LIVE_VALUE_NAMES[idx]] = _signed16(value)

    if len(packet.values) > 5:
        fields["yaw_deg"] = _yaw_deg(packet.values[5])
    if len(packet.values) > 6:
        fields["imu_temperature_c"] = round(_signed16(packet.values[6]) / 100.0, 3)
    return fields


def ping_packet_to_fields(packet: PingPacket) -> dict[str, int | float | str]:
    return {
        "node_millis": int(packet.timestamp_ms),
        "ping_ms": int(packet.ping_ms),
        "success_percent_x100": int(packet.success_percent_x100),
        "success_ratio": round(float(packet.success_percent_x100) / 100.0, 3),
        "file_count": int(packet.file_count),
    }


def file_packet_to_influx_record(
    packet: FileDataPacket,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    source: str = "messkluppe",
    file_id: str | int | None = None,
) -> MesskluppeInfluxRecord:
    tags = {
        "source": source,
        "clip_id": str(packet.clip_id),
        "packet_task": str(packet.task),
    }
    if file_id is not None:
        tags["file_id"] = str(file_id)

    return MesskluppeInfluxRecord(
        measurement=measurement,
        tags=tags,
        fields=file_packet_to_fields(packet),
        time_ns=_packet_time_ns(packet),
    )


def live_packet_to_influx_record(
    packet: LiveDataPacket,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    source: str = "messkluppe",
    file_id: str | int | None = None,
) -> MesskluppeInfluxRecord:
    tags = {
        "source": source,
        "clip_id": str(packet.clip_id),
        "packet_task": str(packet.task),
    }
    if file_id is not None:
        tags["file_id"] = str(file_id)

    return MesskluppeInfluxRecord(
        measurement=measurement,
        tags=tags,
        fields=live_packet_to_fields(packet),
        time_ns=None,
    )


def ping_packet_to_influx_record(
    packet: PingPacket,
    *,
    measurement: str = DEFAULT_MEASUREMENT,
    source: str = "messkluppe",
    file_id: str | int | None = None,
) -> MesskluppeInfluxRecord:
    tags = {
        "source": source,
        "clip_id": str(packet.clip_id),
        "packet_task": str(packet.task),
    }
    if file_id is not None:
        tags["file_id"] = str(file_id)

    return MesskluppeInfluxRecord(
        measurement=measurement,
        tags=tags,
        fields=ping_packet_to_fields(packet),
        time_ns=None,
    )


def record_to_line_protocol(record: MesskluppeInfluxRecord) -> str:
    def esc_key(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")

    def esc_string(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    tags = ",".join(f"{esc_key(key)}={esc_key(value)}" for key, value in sorted(record.tags.items()) if value != "")
    fields: list[str] = []
    for key, value in sorted(record.fields.items()):
        if isinstance(value, bool):
            fields.append(f"{esc_key(key)}={'true' if value else 'false'}")
        elif isinstance(value, int):
            fields.append(f"{esc_key(key)}={value}i")
        elif isinstance(value, float):
            fields.append(f"{esc_key(key)}={value}")
        else:
            fields.append(f"{esc_key(key)}={esc_string(str(value))}")
    if not fields:
        raise ValueError("record must contain at least one field")
    head = esc_key(record.measurement)
    if tags:
        head = f"{head},{tags}"
    line = f"{head} {','.join(fields)}"
    if record.time_ns is not None:
        line = f"{line} {record.time_ns}"
    return line


def point_kwargs(record: MesskluppeInfluxRecord) -> dict[str, Any]:
    return {
        "measurement": record.measurement,
        "tags": dict(record.tags),
        "fields": dict(record.fields),
        "time_ns": record.time_ns,
    }
