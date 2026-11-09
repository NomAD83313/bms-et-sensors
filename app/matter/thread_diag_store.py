from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from typing import Any

try:
    from .thread_diag import (
        ThreadDiagNeighborRecord,
        ThreadDiagNodeRecord,
        ThreadDiagParentRecord,
        ThreadDiagRecord,
    )
except ImportError:
    from thread_diag import (
        ThreadDiagNeighborRecord,
        ThreadDiagNodeRecord,
        ThreadDiagParentRecord,
        ThreadDiagRecord,
    )


class ThreadDiagStore:
    def __init__(self, state_file: str | None = None) -> None:
        self._lock = threading.Lock()
        self._nodes_by_serial: dict[str, dict[str, Any]] = {}
        self._parent_links_by_child_serial: dict[str, dict[str, Any]] = {}
        self._neighbor_links_by_key: dict[str, dict[str, Any]] = {}
        self._updated_at: float | None = None
        self._state_file = Path(state_file) if state_file else None
        self._load_state()

    def ingest_records(self, records: list[ThreadDiagRecord], observed_at: float | None = None) -> dict[str, int]:
        ts = observed_at if observed_at is not None else time.time()
        accepted = 0
        with self._lock:
            for record in records:
                if isinstance(record, ThreadDiagNodeRecord):
                    self._nodes_by_serial[record.serial] = self._wrap_record(record, ts)
                    accepted += 1
                    continue
                if isinstance(record, ThreadDiagParentRecord):
                    self._parent_links_by_child_serial[record.serial] = self._wrap_record(record, ts)
                    accepted += 1
                    continue
                if isinstance(record, ThreadDiagNeighborRecord):
                    key = self._neighbor_key(record)
                    self._neighbor_links_by_key[key] = self._wrap_record(record, ts)
                    accepted += 1
            if accepted:
                self._updated_at = ts
                self._save_state_locked()
        return {"accepted": accepted}

    def snapshot(self, now: float | None = None, stale_after_sec: float = 300.0) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self._lock:
            nodes = [self._snapshot_entry(entry, current, stale_after_sec) for entry in self._nodes_by_serial.values()]
            parents = [
                self._snapshot_entry(entry, current, stale_after_sec)
                for entry in self._parent_links_by_child_serial.values()
            ]
            neighbors = [
                self._snapshot_entry(entry, current, stale_after_sec)
                for entry in self._neighbor_links_by_key.values()
            ]
            updated_at = self._updated_at
        return {
            "updated_at": updated_at,
            "age_sec": None if updated_at is None else round(current - updated_at, 1),
            "node_count": len(nodes),
            "parent_count": len(parents),
            "neighbor_count": len(neighbors),
            "nodes": sorted(nodes, key=lambda item: item["serial"]),
            "parents": sorted(parents, key=lambda item: item["serial"]),
            "neighbors": sorted(
                neighbors,
                key=lambda item: (item["serial"], item.get("ext_address", ""), item.get("parent_ext_address", "")),
            ),
        }

    def _load_state(self) -> None:
        if self._state_file is None or not self._state_file.exists():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        nodes = payload.get("nodes_by_serial")
        parents = payload.get("parent_links_by_child_serial")
        neighbors = payload.get("neighbor_links_by_key")
        updated_at = payload.get("updated_at")
        if not isinstance(nodes, dict) or not isinstance(parents, dict) or not isinstance(neighbors, dict):
            return
        with self._lock:
            self._nodes_by_serial = {str(key): value for key, value in nodes.items() if isinstance(value, dict)}
            self._parent_links_by_child_serial = {
                str(key): value for key, value in parents.items() if isinstance(value, dict)
            }
            self._neighbor_links_by_key = {
                str(key): value for key, value in neighbors.items() if isinstance(value, dict)
            }
            self._updated_at = updated_at if isinstance(updated_at, (int, float)) else None

    def _save_state_locked(self) -> None:
        if self._state_file is None:
            return
        payload = {
            "updated_at": self._updated_at,
            "nodes_by_serial": self._nodes_by_serial,
            "parent_links_by_child_serial": self._parent_links_by_child_serial,
            "neighbor_links_by_key": self._neighbor_links_by_key,
        }
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp_path.replace(self._state_file)
        except Exception:
            return

    @staticmethod
    def _wrap_record(record: ThreadDiagRecord, observed_at: float) -> dict[str, Any]:
        data = asdict(record)
        data["observed_at"] = observed_at
        return data

    @staticmethod
    def _neighbor_key(record: ThreadDiagNeighborRecord) -> str:
        return "|".join([record.serial, record.ext_address, record.rloc16, record.role])

    @staticmethod
    def _snapshot_entry(entry: dict[str, Any], now: float, stale_after_sec: float) -> dict[str, Any]:
        data = dict(entry)
        observed_at = data.get("observed_at")
        age_sec = round(now - observed_at, 1) if isinstance(observed_at, (int, float)) else None
        data["age_sec"] = age_sec
        data["stale"] = bool(age_sec is not None and age_sec > stale_after_sec)
        return data
