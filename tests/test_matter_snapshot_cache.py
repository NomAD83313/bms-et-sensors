import unittest
from unittest.mock import Mock, patch

from app.matter.matter_snapshot_cache import MatterNodeSnapshotCache


class MatterNodeSnapshotCacheTests(unittest.TestCase):
    def test_fresh_snapshot_is_copied_without_fetching(self) -> None:
        fetch_snapshot = Mock()
        cache = MatterNodeSnapshotCache(fetch_snapshot, ttl_sec=lambda: 5.0, time_fn=lambda: 10.0)
        source = [{"node_id": 7}]
        cache.seed(source, fetched_at=9.0)
        source[0]["node_id"] = 99

        result = cache.get()
        result[0]["node_id"] = 42

        self.assertEqual(cache.get(), [{"node_id": 7}])
        fetch_snapshot.assert_not_called()

    def test_cold_nonblocking_get_starts_refresh_and_returns_empty(self) -> None:
        cache = MatterNodeSnapshotCache(Mock(), ttl_sec=lambda: 2.0, time_fn=lambda: 10.0)

        with patch.object(cache, "trigger_refresh", return_value=True) as trigger_refresh:
            result = cache.get(blocking=False)

        self.assertEqual(result, [])
        trigger_refresh.assert_called_once_with(force=True, blocking=False, on_error=None)

    def test_stale_snapshot_is_returned_while_refresh_is_triggered(self) -> None:
        cache = MatterNodeSnapshotCache(Mock(), ttl_sec=lambda: 2.0, time_fn=lambda: 20.0)
        cache.seed([{"node_id": 8}], fetched_at=10.0)

        with patch.object(cache, "trigger_refresh", return_value=True) as trigger_refresh:
            result = cache.get()

        self.assertEqual(result, [{"node_id": 8}])
        trigger_refresh.assert_called_once_with(on_error=None)

    def test_refresh_failure_returns_existing_snapshot(self) -> None:
        cache = MatterNodeSnapshotCache(
            Mock(side_effect=OSError("offline")),
            ttl_sec=lambda: 0.0,
            time_fn=lambda: 20.0,
        )
        cache.seed([{"node_id": 8}], fetched_at=10.0)

        self.assertEqual(cache.get(), [{"node_id": 8}])


if __name__ == "__main__":
    unittest.main()
