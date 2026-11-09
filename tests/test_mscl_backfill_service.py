import unittest
from unittest.mock import patch

from app.mscl import mscl_backfill_service
from app.mscl.mscl_backfill_service import backfill_rows_to_influx_stream


def _build_fake_influx_types():
    class FakePoint:
        def __init__(self, measurement):
            self.measurement = measurement
            self.tags = {}
            self.fields = {}
            self.ts = None

        def tag(self, key, value):
            self.tags[key] = value
            return self

        def field(self, key, value):
            self.fields[key] = value
            return self

        def time(self, value, _precision):
            self.ts = value
            return self

    class FakeQueryApi:
        def query_stream(self, query, org):
            _ = (query, org)
            return []

    class FakeWriteApi:
        writes = []

        def write(self, bucket, org, points):
            self.__class__.writes.append((bucket, org, list(points)))

    class FakeInfluxDBClient:
        def __init__(self, url, token, org, **_kwargs):
            _ = (url, token, org)
            self._query = FakeQueryApi()
            self._write = FakeWriteApi()

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def query_api(self):
            return self._query

        def write_api(self, write_options=None):
            _ = write_options
            return self._write

    return FakeWriteApi, FakeInfluxDBClient, FakePoint


FakeWriteApi, FakeInfluxDBClient, FakePoint = _build_fake_influx_types()

class BackfillServiceTests(unittest.TestCase):
    def setUp(self):
        FakeWriteApi.writes = []
        for name, value in (("InfluxDBClient", FakeInfluxDBClient), ("Point", FakePoint)):
            patcher = patch.object(mscl_backfill_service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_backfill_writes_with_batch_split(self):
        rows = [
            {"channel": "ch1", "value": 1.0, "timestamp_ns": 100, "sample_rate": "1 Hz"},
            {"channel": "ch1", "value": 2.0, "timestamp_ns": 101, "sample_rate": "1 Hz"},
            {"channel": "ch1", "value": 3.0, "timestamp_ns": 102, "sample_rate": "1 Hz"},
            {"channel": "ch1", "value": 4.0, "timestamp_ns": 103, "sample_rate": "1 Hz"},
            {"channel": "ch1", "value": 5.0, "timestamp_ns": 104, "sample_rate": "1 Hz"},
        ]
        out = backfill_rows_to_influx_stream(
            node_id=16904,
            rows=rows,
            source_tag="mscl_node_export",
            influx_url="http://influxdb:8086",
            influx_token="t",
            influx_org="o",
            influx_bucket="b",
            measurement="mscl_sensors",
            export_batch_size=2,
            ns_to_iso_utc_fn=lambda ns: f"2026-01-01T00:00:{int(ns)%60:02d}.000000000Z",
        )
        self.assertEqual(out["written"], 5)
        self.assertEqual(out["skipped_existing"], 0)
        self.assertEqual([len(x[2]) for x in FakeWriteApi.writes], [2, 2, 1])

    def test_backfill_skips_invalid_rows(self):
        rows = [
            {"channel": "", "value": 1.0, "timestamp_ns": 100},
            {"channel": "ch1", "value": "bad", "timestamp_ns": 101},
            {"channel": "ch1", "value": 2.0, "timestamp_ns": 102},
        ]
        out = backfill_rows_to_influx_stream(
            node_id=16904,
            rows=rows,
            source_tag="mscl_node_export",
            influx_url="http://influxdb:8086",
            influx_token="t",
            influx_org="o",
            influx_bucket="b",
            measurement="mscl_sensors",
            export_batch_size=100,
            ns_to_iso_utc_fn=lambda ns: f"2026-01-01T00:00:{int(ns)%60:02d}.000000000Z",
        )
        self.assertEqual(out["written"], 1)
        self.assertEqual(out["skipped_existing"], 0)
        self.assertEqual(len(FakeWriteApi.writes), 1)
        self.assertEqual(len(FakeWriteApi.writes[0][2]), 1)


if __name__ == "__main__":
    unittest.main()
