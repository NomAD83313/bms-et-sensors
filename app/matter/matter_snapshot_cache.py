import threading
import time
from copy import deepcopy
from typing import Any, Callable


class MatterNodeSnapshotCache:
    def __init__(
        self,
        fetch_snapshot: Callable[[], list[dict[str, Any]]],
        ttl_sec: Callable[[], float],
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self._fetch_snapshot = fetch_snapshot
        self._ttl_sec = ttl_sec
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._snapshot: list[dict[str, Any]] | None = None
        self._fetched_at: float | None = None
        self._refresh_inflight = False
        self._refresh_thread: threading.Thread | None = None

    def reset(self) -> None:
        with self._lock:
            self._snapshot = None
            self._fetched_at = None
            self._refresh_inflight = False
            self._refresh_thread = None

    def seed(self, snapshot: list[dict[str, Any]], fetched_at: float | None = None) -> None:
        with self._lock:
            self._snapshot = deepcopy(snapshot)
            self._fetched_at = self._time_fn() if fetched_at is None else fetched_at

    def refresh(self) -> list[dict[str, Any]]:
        try:
            snapshot = self._fetch_snapshot()
            self.seed(snapshot)
            return deepcopy(snapshot)
        finally:
            with self._lock:
                self._refresh_inflight = False

    def trigger_refresh(
        self,
        force: bool = False,
        blocking: bool = False,
        on_error: Callable[[Exception], None] | None = None,
    ) -> bool:
        with self._lock:
            ttl_sec = max(0.0, self._ttl_sec())
            now = self._time_fn()
            is_fresh = (
                not force
                and ttl_sec > 0
                and self._snapshot is not None
                and self._fetched_at is not None
                and (now - self._fetched_at) < ttl_sec
            )
            if is_fresh or self._refresh_inflight:
                return False
            self._refresh_inflight = True

        if blocking:
            self.refresh()
            return True

        def runner() -> None:
            try:
                self.refresh()
            except Exception as exc:
                if on_error is not None:
                    on_error(exc)

        thread = threading.Thread(target=runner, name="matter-node-snapshot-refresh", daemon=True)
        with self._lock:
            self._refresh_thread = thread
        thread.start()
        return True

    def get(
        self,
        force: bool = False,
        blocking: bool = True,
        on_error: Callable[[Exception], None] | None = None,
    ) -> list[dict[str, Any]]:
        ttl_sec = max(0.0, self._ttl_sec())
        now = self._time_fn()
        with self._lock:
            cached = deepcopy(self._snapshot) if self._snapshot is not None else None
            fetched_at = self._fetched_at
        if cached is None and not blocking and not force:
            self.trigger_refresh(force=True, blocking=False, on_error=on_error)
            return []
        if cached is None or force or ttl_sec <= 0:
            try:
                return self.refresh()
            except Exception:
                if cached is not None:
                    return cached
                raise
        if fetched_at is not None and (now - fetched_at) >= ttl_sec:
            self.trigger_refresh(on_error=on_error)
        return cached

    def pending(self) -> bool:
        with self._lock:
            return self._snapshot is None and self._refresh_inflight
