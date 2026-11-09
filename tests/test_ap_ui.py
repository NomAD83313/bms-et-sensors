import importlib
import unittest
from unittest.mock import patch


ap_host = importlib.import_module("app.ap.host")


class ApHostParsingTests(unittest.TestCase):
    def test_parse_station_dump_extracts_clients_and_signal(self):
        sample = """
Station 40:91:51:9a:93:24 (on wlan0)
\tinactive time:\t12 ms
\trx bytes:\t1024
\trx packets:\t24
\ttx bytes:\t2048
\ttx packets:\t80
\ttx failed:\t5
\tsignal:\t\t-61 [-67, -62] dBm
\trx bitrate:\t72.2 MBit/s
\ttx bitrate:\t65.0 MBit/s
Station aa:bb:cc:dd:ee:ff (on wlan0)
\tinactive time:\t1530 ms
\tsignal avg:\t\t-79 dBm
"""

        clients = ap_host.parse_station_dump(sample)

        self.assertEqual(len(clients), 2)
        self.assertEqual(clients[0]["mac"], "40:91:51:9a:93:24")
        self.assertEqual(clients[0]["signal_dbm"], -61)
        self.assertEqual(clients[0]["rx_bytes"], 1024)
        self.assertEqual(clients[0]["rx_packets"], 24)
        self.assertEqual(clients[0]["tx_packets"], 80)
        self.assertEqual(clients[0]["tx_failed"], 5)
        self.assertEqual(clients[1]["inactive_ms"], 1530)
        self.assertEqual(clients[1]["signal_dbm"], -79)

    def test_parse_ip_neigh_maps_mac_to_ip(self):
        sample = """
10.42.0.218 dev wlan0 lladdr 40:91:51:9a:93:24 REACHABLE
10.42.0.219 dev wlan0 lladdr aa:bb:cc:dd:ee:ff STALE
"""

        mapping = ap_host.parse_ip_neigh(sample)

        self.assertEqual(mapping["40:91:51:9a:93:24"], "10.42.0.218")
        self.assertEqual(mapping["aa:bb:cc:dd:ee:ff"], "10.42.0.219")

    def test_parse_ip_neigh_accepts_filtered_dev_output_without_dev_token(self):
        sample = """
10.42.0.66 lladdr 9c:b1:50:a6:6a:49 REACHABLE
10.42.0.179 lladdr ca:73:dc:05:03:b2 STALE
"""

        mapping = ap_host.parse_ip_neigh(sample)

        self.assertEqual(mapping["9c:b1:50:a6:6a:49"], "10.42.0.66")
        self.assertEqual(mapping["ca:73:dc:05:03:b2"], "10.42.0.179")

    def test_parse_ip_neigh_prefers_ipv4_over_link_local_ipv6(self):
        sample = """
10.42.0.66 lladdr 9c:b1:50:a6:6a:49 REACHABLE
fe80::4945:b328:5491:7f6 lladdr 9c:b1:50:a6:6a:49 STALE
"""

        mapping = ap_host.parse_ip_neigh(sample)

        self.assertEqual(mapping["9c:b1:50:a6:6a:49"], "10.42.0.66")

    def test_parse_dnsmasq_leases_extracts_hostname_and_ip(self):
        sample = """
1777540580 ca:73:dc:05:03:b2 10.42.0.179 Watch 01:ca:73:dc:05:03:b2
1777539998 9c:b1:50:a6:6a:49 10.42.0.66 NB250339 01:9c:b1:50:a6:6a:49
1777541364 40:91:51:9a:93:24 10.42.0.218 espressif 01:40:91:51:9a:93:24
"""

        leases = ap_host.parse_dnsmasq_leases(sample)

        self.assertEqual(leases["ca:73:dc:05:03:b2"]["hostname"], "Watch")
        self.assertEqual(leases["9c:b1:50:a6:6a:49"]["ip"], "10.42.0.66")
        self.assertEqual(leases["40:91:51:9a:93:24"]["hostname"], "espressif")


class ApHostBehaviorTests(unittest.TestCase):
    def test_normalize_mac_text_removes_nmcli_colon_escapes(self):
        self.assertEqual(ap_host.normalize_mac_text("00\\:C0\\:CA\\:B9\\:B3\\:75"), "00:C0:CA:B9:B3:75")

    def test_build_ap_snapshot_combines_profile_and_clients(self):
        profile = {
            "interface": "wlan0",
            "active": True,
            "active_connection": "rpi-ap",
            "state": "100 (connected)",
            "mac_address": "dc:a6:32:00:11:22",
            "ipv4_addresses": ["10.42.0.1/24"],
            "profile": {
                "name": "rpi-ap",
                "exists": True,
                "ssid": "RPI-DAQ-AP",
                "mode": "ap",
                "band": "bg",
                "channel": "4",
                "autoconnect": "yes",
                "ipv4_method": "shared",
                "ipv6_method": "link-local",
                "interface_name": "wlan0",
            },
        }
        clients = [{"mac": "40:91:51:9a:93:24", "signal_dbm": -61}]

        with patch.object(ap_host, "get_ap_profile", return_value=profile):
            with patch.object(ap_host, "list_ap_clients", return_value=clients):
                snapshot = ap_host.build_ap_snapshot()

        self.assertEqual(snapshot["status"], "ok")
        self.assertTrue(snapshot["active"])
        self.assertEqual(snapshot["client_count"], 1)
        self.assertEqual(snapshot["profile"]["ssid"], "RPI-DAQ-AP")

    def test_list_ap_clients_merges_hostname_from_leases(self):
        station_dump = """
Station 40:91:51:9a:93:24 (on wlan0)
\tinactive time:\t12 ms
\tsignal:\t\t-61 dBm
"""
        neigh_dump = "10.42.0.218 lladdr 40:91:51:9a:93:24 REACHABLE"

        with patch.object(ap_host, "_run_command", side_effect=[(0, station_dump, ""), (0, neigh_dump, "")]):
            with patch.object(
                ap_host,
                "load_ap_leases",
                return_value={"40:91:51:9a:93:24": {"ip": "10.42.0.218", "hostname": "espressif"}},
            ):
                clients = ap_host.list_ap_clients()

        self.assertEqual(clients[0]["hostname"], "espressif")
        self.assertEqual(clients[0]["display_name"], "espressif")
        self.assertEqual(clients[0]["ip"], "10.42.0.218")
        self.assertEqual(clients[0]["signal_quality"], 78)
        self.assertEqual(clients[0]["signal_quality_source"], "rssi")

    def test_display_name_ignores_device_id_metadata(self):
        client = {
            "mac": "84:fc:e6:86:59:0c",
            "hostname": "espressif",
            "device_id": "esp32s-cam-86590C",
        }

        self.assertEqual(ap_host._display_name_for_client(client), "espressif")

    def test_list_ap_clients_marks_signal_unavailable_when_no_rssi_source_exists(self):
        station_dump = """
Station 6e:c0:7a:e1:8a:40 (on wlan0)
\tinactive time:\t14000 ms
\trx bytes:\t1070491
\ttx bytes:\t10911209
\ttx packets:\t100
\ttx failed:\t25
\ttx bitrate:\t32.5 MBit/s
"""
        neigh_dump = "10.42.0.109 lladdr 6e:c0:7a:e1:8a:40 REACHABLE"

        with patch.object(ap_host, "_run_command", side_effect=[(0, station_dump, ""), (0, neigh_dump, "")]):
            with patch.object(ap_host, "load_ap_leases", return_value={}):
                clients = ap_host.list_ap_clients()

        self.assertNotIn("signal_dbm", clients[0])
        self.assertIsNone(clients[0]["signal_quality"])
        self.assertEqual(clients[0]["signal_band"], "unknown")
        self.assertEqual(clients[0]["signal_source"], "")
        self.assertEqual(clients[0]["signal_quality_source"], "unknown")

    def test_set_ap_state_restart_brings_profile_up(self):
        calls = []

        def fake_run(args):
            calls.append(args)
            return 0, "ok", ""

        with patch.object(ap_host, "_run_command", side_effect=fake_run):
            ok, _message = ap_host.set_ap_state("restart", profile="rpi-ap")

        self.assertTrue(ok)
        self.assertEqual(
            calls,
            [
                ["nmcli", "connection", "down", "rpi-ap"],
                ["nmcli", "connection", "up", "rpi-ap"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
