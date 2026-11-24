"""Persistent runtime storage and exports for WTVB01 FIFO capture records."""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from typing import Any

try:
    from app.shared.csv_locale import resolve_csv_decimal_separator
except ImportError:  # Flat import inside the container.
    from csv_locale import resolve_csv_decimal_separator


RECORD_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def record_id(captured_at: str, sensor_name: str) -> str:
    timestamp = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(captured_at)).strip("-") or "capture"
    sensor = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(sensor_name)).strip("-") or "sensor"
    return f"{timestamp}_{sensor}"


def _path(root: Path, item_id: str) -> Path:
    if not RECORD_ID_RE.fullmatch(str(item_id or "")):
        raise ValueError("invalid record id")
    return root / f"{item_id}.json.gz"


def save(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    item = dict(record)
    item.setdefault("version", 1)
    item.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    item.setdefault("id", record_id(item["captured_at"], item["sensor"]))
    target = _path(root, item["id"])
    temporary = target.with_suffix(target.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(item, handle, separators=(",", ":"), allow_nan=False)
    os.replace(temporary, target)
    return metadata(item)


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in (
        "id", "version", "sensor", "address", "captured_at", "created_at", "note", "sample_rate_hz",
        "samples", "duration_s", "averages", "capture_count", "periodic",
    )}


def load(root: Path, item_id: str) -> dict[str, Any]:
    with gzip.open(_path(root, item_id), "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("record is not a JSON object")
    return value


def list_records(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    records = []
    for path in root.glob("*.json.gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                records.append(metadata(json.load(handle)))
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            continue
    return sorted(records, key=lambda item: str(item.get("captured_at") or ""), reverse=True)


def delete(root: Path, item_id: str) -> bool:
    path = _path(root, item_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def delete_many(root: Path, item_ids: list[str]) -> tuple[list[str], list[str]]:
    """Delete validated record IDs and return (deleted, missing)."""
    unique_ids = list(dict.fromkeys(item_ids))
    paths = [(item_id, _path(root, item_id)) for item_id in unique_ids]
    deleted: list[str] = []
    missing: list[str] = []
    for item_id, path in paths:
        if path.exists():
            path.unlink()
            deleted.append(item_id)
        else:
            missing.append(item_id)
    return deleted, missing


def prune(root: Path, max_count: int, retention_days: int, now: datetime | None = None) -> list[str]:
    """Remove oldest records outside the age or count retention limits."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=retention_days)
    items = list_records(root)
    removed: list[str] = []
    for index, item in enumerate(items):
        raw_captured = str(item.get("captured_at") or "")
        try:
            captured = datetime.fromisoformat(raw_captured.replace("Z", "+00:00"))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
        except ValueError:
            captured = datetime.min.replace(tzinfo=timezone.utc)
        if index >= max_count or captured < cutoff:
            item_id = str(item.get("id") or "")
            if item_id and delete(root, item_id):
                removed.append(item_id)
    return removed


def csv_content(
    record: dict[str, Any], *, mode: str = "view", export_hz: str | None = None, delimiter: str = ",",
) -> str:
    wave = record.get("waveform") or {}
    axes = wave.get("axes") or {}
    sample_rate = float(record.get("sample_rate_hz") or wave.get("sample_rate_hz") or 1.0)
    samples = int(record.get("samples") or wave.get("samples") or 0)
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\r\n")
    decimal = resolve_csv_decimal_separator(delimiter)

    def number(value: float | str | None) -> str:
        return "" if value is None else str(value).replace(".", decimal)

    if mode == "raw_period":
        writer.writerow(["capture", "sample", "time_s", "raw_acc_g_x", "raw_acc_g_y", "raw_acc_g_z"])
        for capture, points in enumerate(record.get("raw_captures_g") or [], start=1):
            for index, values in enumerate(points):
                writer.writerow([capture, index, number(f"{index / sample_rate:.9f}"), *map(number, values)])
        return output.getvalue()

    if mode not in ("view", "custom_rate"):
        raise ValueError("unknown record export mode")
    fields = ("acc_g", "velocity_mm_s", "displacement_um")
    writer.writerow(["sample", "time_s", *[f"{field}_{axis}" for field in fields for axis in "xyz"]])
    channels = [axes.get(axis, {}).get(field, []) for field in fields for axis in "xyz"]
    if mode == "view":
        for index in range(samples):
            values = [channel[index] if index < len(channel) else None for channel in channels]
            writer.writerow([index, number(f"{index / sample_rate:.9f}"), *map(number, values)])
        return output.getvalue()

    try:
        hz = float(export_hz or "")
    except ValueError:
        hz = math.nan
    if not 0.01 <= hz <= sample_rate:
        raise ValueError(f"export_hz must be between 0.01 and {sample_rate:g} Hz")

    # Average populated time bins relative to the start of the saved waveform.
    rate_ratio = hz / sample_rate
    for bucket, group in groupby(range(samples), key=lambda index: int(index * rate_ratio)):
        indices = list(group)
        averages = []
        for channel in channels:
            values = [channel[index] for index in indices if index < len(channel)]
            averages.append(f"{sum(values) / len(values):.9g}" if values else None)
        writer.writerow([bucket, number(f"{bucket / hz:.9f}"), *map(number, averages)])
    return output.getvalue()
