import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.cameras import zero_wifi_setup as ws

CONFIG = dict(ws.DEFAULTS, hotspot_ssid='BMS-camera-13b1', hotspot_password='setup-pass-123', grace_sec=120, retry_sec=300)


class FakeNm:
    """NetworkManager double: saved profiles, networks in range and the active connection."""

    def __init__(self):
        self.interface = 'wlan0'
        self.active = 'BMSensors-DOA'
        self.networks = [
            {'name': 'BMSensors-DOA', 'ssid': 'BMSensors-DOA', 'autoconnect': True, 'priority': 10},
            {'name': 'BMSensors', 'ssid': 'BMSensors', 'autoconnect': True, 'priority': 0},
        ]
        self.in_range = {'BMSensors-DOA', 'Other'}
        self.working = {'BMSensors-DOA', 'BMSensors', ws.HOTSPOT_NAME}
        self.clients = 0
        self.calls = []

    def device(self):
        return {'state': 'connected' if self.active else 'disconnected', 'connection': self.active}

    def address(self):
        return '10.42.0.227/24'

    def saved(self):
        return sorted(self.networks, key=lambda n: -n['priority'])

    def scan(self, rescan):
        self.calls.append(('scan', rescan))
        return [{'ssid': s, 'signal': 70, 'security': 'WPA2', 'channel': '6'} for s in sorted(self.in_range)]

    def stations(self):
        return self.clients

    def up(self, name, wait=40):
        self.calls.append(('up', name))
        if name in self.working and (name == ws.HOTSPOT_NAME or name in self.in_range):
            self.active = name
            return True, ''
        self.active = ''
        return False, 'no network with that SSID'

    def down(self, name):
        self.calls.append(('down', name))
        if self.active == name:
            self.active = ''

    def add(self, ssid, password, priority):
        name = f'wifi-{ssid}'
        self.networks.append({'name': name, 'ssid': ssid, 'autoconnect': True, 'priority': priority})
        self.calls.append(('add', ssid, password, priority))
        return name

    def set_priority(self, name, priority):
        next(n for n in self.networks if n['name'] == name)['priority'] = priority

    def delete(self, name):
        self.networks = [n for n in self.networks if n['name'] != name]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.clock = Clock()
        self.supervisor = ws.Supervisor(self.nm, dict(CONFIG), clock=self.clock)

    def test_connected_node_stays_a_client(self):
        self.supervisor.tick()
        self.assertEqual(self.supervisor.mode, 'client')
        self.assertNotIn(('up', ws.HOTSPOT_NAME), self.nm.calls)

    def test_hotspot_starts_only_after_the_grace_period(self):
        self.supervisor.tick()
        self.nm.active = ''
        self.clock.now += 119
        self.supervisor.tick()
        self.assertEqual(self.supervisor.mode, 'waiting')
        self.clock.now += 2
        self.supervisor.tick()
        self.assertEqual(self.supervisor.mode, 'hotspot')
        # The networks are scanned before the radio turns into an access point.
        self.assertLess(self.nm.calls.index(('scan', True)), self.nm.calls.index(('up', ws.HOTSPOT_NAME)))

    def test_hotspot_retries_saved_networks_only_without_clients(self):
        self.nm.active = ws.HOTSPOT_NAME
        self.supervisor.tick()
        self.clock.now += 301
        self.nm.clients = 1
        self.supervisor.tick()
        self.assertEqual(self.nm.active, ws.HOTSPOT_NAME)  # Somebody is on the setup page.
        self.nm.clients = 0
        self.supervisor.tick()
        self.assertEqual((self.nm.active, self.supervisor.mode), ('BMSensors-DOA', 'client'))

    def test_failed_retry_reopens_the_hotspot(self):
        self.nm.in_range = {'Other'}
        self.nm.active = ws.HOTSPOT_NAME
        self.supervisor.tick()
        self.clock.now += 301
        self.supervisor.tick()
        self.assertEqual((self.nm.active, self.supervisor.mode), (ws.HOTSPOT_NAME, 'hotspot'))
        self.assertNotIn(('up', 'BMSensors'), self.nm.calls)  # Out of range: not tried.

    def test_failed_connect_restores_the_previous_network(self):
        self.nm.networks.append({'name': 'wifi-Far', 'ssid': 'Far', 'autoconnect': True, 'priority': 5})
        result = self.supervisor.connect('wifi-Far')
        self.assertFalse(result['ok'])
        self.assertIn('reconnected to BMSensors-DOA', result['message'])
        self.assertEqual(self.nm.active, 'BMSensors-DOA')

    def test_failed_connect_from_the_hotspot_returns_to_it(self):
        self.nm.active = ws.HOTSPOT_NAME
        self.nm.networks.append({'name': 'wifi-Far', 'ssid': 'Far', 'autoconnect': True, 'priority': 5})
        result = self.supervisor.connect('wifi-Far')
        self.assertFalse(result['ok'])
        self.assertEqual(self.nm.active, ws.HOTSPOT_NAME)

    def test_successful_connect_from_the_hotspot(self):
        self.nm.active = ws.HOTSPOT_NAME
        result = self.supervisor.connect('BMSensors-DOA')
        self.assertTrue(result['ok'])
        self.assertEqual(self.supervisor.mode, 'client')

    def test_forced_hotspot_is_held_before_retrying(self):
        self.supervisor.force_hotspot(10)
        self.assertEqual(self.nm.active, ws.HOTSPOT_NAME)
        self.clock.now += 301
        self.supervisor.tick()
        self.assertEqual(self.nm.active, ws.HOTSPOT_NAME)  # Held for 10 minutes.
        self.clock.now += 300
        self.supervisor.tick()
        self.assertEqual(self.nm.active, 'BMSensors-DOA')


class NmParsingTests(unittest.TestCase):
    def test_terse_split_handles_escaped_colons(self):
        self.assertEqual(ws.split_terse(r'My\:Net:80:WPA2:6'), ['My:Net', '80', 'WPA2', '6'])
        self.assertEqual(ws.split_terse(r'a\\b:c'), ['a\\b', 'c'])

    def test_saved_lists_only_wifi_client_profiles(self):
        outputs = {
            ('connection', 'show'): 'BMSensors-DOA:802-11-wireless:yes:10\nlo:loopback:no:0\n'
                                    f'{ws.HOTSPOT_NAME}:802-11-wireless:no:0\nother-ap:802-11-wireless:no:0\n',
            'BMSensors-DOA': 'BMSensors-DOA\ninfrastructure\n',
            'other-ap': 'X\nap\n',
        }

        def run(args, timeout):
            if args[-2:] == ['connection', 'show']:
                return 0, outputs[('connection', 'show')], ''
            return 0, outputs.get(args[-1], ''), ''

        networks = ws.Nm('wlan0', run).saved()
        self.assertEqual(networks, [{'name': 'BMSensors-DOA', 'ssid': 'BMSensors-DOA', 'autoconnect': True, 'priority': 10}])

    def test_scan_keeps_the_strongest_entry_per_ssid(self):
        out = 'Net:40:WPA2:1\nNet:80:WPA2:6\n:90:WPA2:11\nOpen:50::3\n'
        networks = ws.Nm('wlan0', lambda args, timeout: (0, out, '')).scan(False)
        self.assertEqual(networks, [{'ssid': 'Net', 'signal': 80, 'security': 'WPA2', 'channel': '6'},
                                    {'ssid': 'Open', 'signal': 50, 'security': 'open', 'channel': '3'}])

    def test_add_uses_an_argument_list_and_a_unique_name(self):
        calls = []

        def run(args, timeout):
            calls.append(args)
            if args[-2:] == ['connection', 'show']:
                return 0, 'wifi-Lab:802-11-wireless:yes:0\n', ''
            if args[-1] == 'wifi-Lab':
                return 0, 'Lab 2\ninfrastructure\n', ''
            return 0, '', ''

        name = ws.Nm('wlan0', run).add('Lab', 'secret-pass', 20)
        self.assertEqual(name, 'wifi-Lab-2')
        add = calls[-1]
        self.assertEqual(add[:4], ['nmcli', 'connection', 'add', 'type'])
        self.assertIn('secret-pass', add)
        self.assertEqual(add[add.index('connection.autoconnect-priority') + 1], '20')

    def test_up_reports_only_the_first_error_line(self):
        nm = ws.Nm('wlan0', lambda args, timeout: (4, '', 'Error: activation failed\nHint: use journalctl\n'))
        self.assertEqual(nm.up('x'), (False, 'Error: activation failed'))

    def test_hotspot_is_plain_wpa2_without_pmf(self):
        calls = []

        def run(args, timeout):
            calls.append(args)
            if '-g' in args:  # Existing profile from an older version: PMF at its default (0).
                return 0, f"BMS-x\n{CONFIG['hotspot_password']}\n10.43.0.1/24\n0\n", ''
            return 0, '', ''

        ws.Nm('wlan0', run).ensure_hotspot(dict(CONFIG, hotspot_ssid='BMS-x'))
        self.assertIn(['nmcli', 'connection', 'delete', ws.HOTSPOT_NAME], calls)
        add = calls[-1]
        self.assertEqual(add[add.index('wifi-sec.pmf') + 1], 'disable')
        self.assertEqual(add[add.index('wifi-sec.key-mgmt') + 1], 'wpa-psk')

    def test_validation(self):
        self.assertEqual(ws.validate_password(''), '')
        for bad in ('short', 'x' * 64, 'bad\npass'):
            with self.assertRaises(ValueError):
                ws.validate_password(bad)
        self.assertEqual(ws.validate_password('ab' * 32), 'ab' * 32)  # 64 hex digits
        with self.assertRaises(ValueError):
            ws.validate_ssid('')
        with self.assertRaises(ValueError):
            ws.validate_ssid('x' * 33)
        with self.assertRaises(ValueError):
            ws.validate_priority(True)

    def test_config_requires_a_hotspot_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi-setup.json'
            path.write_text(json.dumps({'hotspot_password': 'short'}))
            with self.assertRaises(ValueError):
                ws.load_config(path)
            path.write_text(json.dumps({'hotspot_password': 'long-enough', 'hotspot_ssid': 'BMS-x'}))
            self.assertEqual(ws.load_config(path)['hotspot_ssid'], 'BMS-x')


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.supervisor = ws.Supervisor(self.nm, dict(CONFIG), clock=Clock())
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ws.Handler)
        self.server.supervisor = self.supervisor
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        patcher = patch.object(ws, 'background', lambda work: work())
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, method, path, body=None, header=True):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Content-Type': 'application/json'}
        if header:
            headers[ws.HEADER] = '1'
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_page_and_status(self):
        code, body = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn(b'Camera Wi-Fi setup', body)
        code, body = self.request('GET', '/api/status')
        status = json.loads(body)
        self.assertEqual((status['hotspot']['ssid'], status['hotspot']['address']), ('BMS-camera-13b1', '10.43.0.1'))
        self.assertNotIn('setup-pass-123', body.decode())

    def test_add_network_and_connect(self):
        code, body = self.request('POST', '/api/networks', {'ssid': 'Other', 'password': 'other-pass', 'priority': 30,
                                                            'connect': True})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['name'], 'wifi-Other')
        self.assertIn(('add', 'Other', 'other-pass', 30), self.nm.calls)
        self.assertEqual(self.nm.active, 'BMSensors-DOA')  # 'Other' is not a working network: restored.
        code, body = self.request('GET', '/api/networks')
        self.assertNotIn(b'other-pass', body)

    def test_post_requires_the_header_and_valid_input(self):
        self.assertEqual(self.request('POST', '/api/networks', {'ssid': 'X', 'password': 'p' * 8}, header=False)[0], 403)
        self.assertEqual(self.request('POST', '/api/networks', {'ssid': '', 'password': ''})[0], 400)
        self.assertEqual(self.request('POST', '/api/networks/missing/connect', {})[0], 404)

    def test_priority_and_delete(self):
        self.assertEqual(self.request('POST', '/api/networks/BMSensors/priority', {'priority': 50})[0], 200)
        self.assertEqual(self.nm.saved()[0]['name'], 'BMSensors')
        self.assertEqual(self.request('DELETE', '/api/networks/BMSensors')[0], 200)
        self.assertEqual([n['name'] for n in self.nm.saved()], ['BMSensors-DOA'])

    def test_access_is_limited_to_allowed_networks(self):
        self.supervisor.config['allow_networks'] = ['10.43.0.0/24']
        self.assertEqual(self.request('GET', '/api/status')[0], 403)

    def test_captive_portal_redirect_in_hotspot_mode(self):
        self.supervisor.mode = 'hotspot'
        req = urllib.request.Request(self.base + '/generate_204')
        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(req, timeout=5)
            self.fail('expected a redirect')
        except urllib.error.HTTPError as error:
            self.assertEqual((error.code, error.headers['Location']), (302, 'http://10.43.0.1/'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


if __name__ == '__main__':
    unittest.main()
