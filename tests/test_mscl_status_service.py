import unittest

from app.mscl.mscl_status_service import build_status_payload, comm_age_sec, compute_link_health


class _FakeBase:
    def __init__(self, *, last_comm=None, serial_raises=False):
        self._last_comm = last_comm
        self._serial_raises = serial_raises

    def model(self):
        return "63072040"

    def firmwareVersion(self):
        return "6.44079.0"

    def serial(self):
        if self._serial_raises:
            raise RuntimeError("no serial")
        return "S-1"

    def serialNumber(self):
        return "SN-FALLBACK"

    def regionCode(self):
        return 1

    def frequency(self):
        return 18

    def lastCommunicationTime(self):
        return self._last_comm

    def lastDeviceState(self):
        return "Healthy"


class _FakeState:
    def __init__(self, base):
        self.BASE_STATION = base
        self.LAST_BASE_STATUS = {"message": "Connected", "port": "/dev/ttyUSB1", "ts": "12:00:00"}
        self.BASE_BEACON_STATE = True
        self.BAUDRATE = 3000000
        self.LAST_PING_OK_TS = 0.0
        self.PING_TTL_SEC = 10.0


class StatusServiceTests(unittest.TestCase):
    def test_compute_link_health_table(self):
        cases = [
            ("fresh", 2.0, None, 10.0, "healthy"),
            ("stale", 15.0, None, 10.0, "degraded"),
            ("offline_ping", 40.0, None, 10.0, "offline"),
            ("no_ping", None, None, 10.0, "degraded"),
            ("comm_offline_override", 2.0, 130.0, 10.0, "offline"),
            ("comm_degraded_override", 2.0, 40.0, 10.0, "degraded"),
        ]
        for _name, ping_age, comm_age, ttl, expected in cases:
            health, _reason = compute_link_health(
                ping_age_sec=ping_age,
                comm_age_sec_value=comm_age,
                ping_ttl_sec=ttl,
            )
            self.assertEqual(health, expected)

    def test_comm_age_sec_valid(self):
        now = 1_770_000_000.0
        value = "2026-02-11 12:00:00"
        age = comm_age_sec(value, now)
        self.assertTrue(age is None or age >= 0)

    def test_build_status_payload_connected(self):
        state = _FakeState(_FakeBase())
        now = 1_770_813_000.0
        state.LAST_PING_OK_TS = now - 1.2
        payload = build_status_payload(state, now)
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["link_health"], "healthy")
        self.assertEqual(payload["base_serial"], "S-1")
        self.assertEqual(payload["base_connection"], "Serial, /dev/ttyUSB1, 3000000")

    def test_build_status_payload_serial_fallback(self):
        state = _FakeState(_FakeBase(serial_raises=True))
        now = 1_770_813_000.0
        state.LAST_PING_OK_TS = now - 1.2
        payload = build_status_payload(state, now)
        self.assertEqual(payload["base_serial"], "SN-FALLBACK")

    def test_build_status_payload_disconnected(self):
        state = _FakeState(base=None)
        now = 1_770_813_000.0
        payload = build_status_payload(state, now)
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["link_health"], "offline")


if __name__ == "__main__":
    unittest.main()
