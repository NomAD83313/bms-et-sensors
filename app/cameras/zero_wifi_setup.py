"""Wi-Fi fallback hotspot and setup page for a Zero camera node.

- Watches the Wi-Fi interface. Without a client connection for `grace_sec` (default 120 s,
  counted from start or from the last drop) it starts a WPA2 setup hotspot
  (NetworkManager shared mode, 10.43.0.1/24 with DHCP) and keeps serving the setup page.
- In hotspot mode it retries the saved networks every `retry_sec` (default 300 s), but
  only while no phone or laptop is connected to the hotspot.
- The page (port 80) lists saved networks, scans, adds, removes and reprioritises them and
  connects. A failed connection restores the previous one or returns to the hotspot.
- Access is limited to `allow_networks` (hotspot subnet, central AP subnet, localhost);
  POST and DELETE also need the X-BMS-Wifi-Setup header.

Runs as root because it changes NetworkManager profiles; every nmcli call uses an
argument list (no shell). Passwords are never returned by the API.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

HOTSPOT_NAME = 'bms-camera-setup'
HEADER = 'X-BMS-Wifi-Setup'
DEFAULTS: dict[str, Any] = {
    'interface': 'wlan0',
    'hotspot_ssid': '',            # Empty: BMS-<hostname>-<last 4 MAC digits>.
    'hotspot_password': '',        # Required: WPA2 passphrase, 8-63 characters.
    'hotspot_address': '10.43.0.1/24',
    'hotspot_channel': 6,
    'grace_sec': 120,
    'retry_sec': 300,
    'http_port': 80,
    'allow_networks': ['10.43.0.0/24', '10.42.0.0/24', '127.0.0.1/32'],
}
CONNECT_WAIT_SEC = 40
LOOP_SEC = 5


def log(message: str) -> None:
    print(message, flush=True)


def split_terse(line: str) -> list[str]:
    """Split one `nmcli -t` line: fields separated by ':', with '\\:' and '\\\\' escaped."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)
    fields.append(''.join(current))
    return fields


def validate_ssid(value: Any) -> str:
    ssid = str(value if value is not None else '')
    if not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
        raise ValueError('SSID must be 1-32 bytes without control characters')
    return ssid


def validate_password(value: Any) -> str:
    password = str(value or '')
    if password and not (8 <= len(password) <= 63 and all(32 <= ord(c) < 127 for c in password)
                         or re.fullmatch(r'[0-9A-Fa-f]{64}', password)):
        raise ValueError('Password must be empty (open network), 8-63 printable ASCII characters or 64 hex digits')
    return password


def validate_priority(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError('Priority must be an integer')
    try:
        priority = int(value)
    except (TypeError, ValueError):
        raise ValueError('Priority must be an integer') from None
    if not -999 <= priority <= 999:
        raise ValueError('Priority must be -999..999')
    return priority


def load_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULTS)
    if path.exists():
        config.update(json.loads(path.read_text()))
    password = str(config['hotspot_password'])
    if not 8 <= len(password) <= 63:
        raise ValueError(f'{path}: hotspot_password must be 8-63 characters')
    if not config['hotspot_ssid']:
        config['hotspot_ssid'] = default_ssid(config['interface'])
    return config


def default_ssid(interface: str) -> str:
    host = re.sub(r'[^A-Za-z0-9-]', '', os.uname().nodename)[:18] or 'camera'
    try:
        mac = Path(f'/sys/class/net/{interface}/address').read_text().strip().replace(':', '')
    except OSError:
        mac = ''
    return f'BMS-{host}-{mac[-4:]}' if mac else f'BMS-{host}'


class Nm:
    """Thin nmcli wrapper; `run(args, timeout)` returns (exit code, stdout, stderr)."""

    def __init__(self, interface: str, run: Callable[[list[str], float], tuple[int, str, str]] | None = None):
        self.interface = interface
        self.run = run or self._run

    @staticmethod
    def _run(args: list[str], timeout: float) -> tuple[int, str, str]:
        try:
            done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired:
            return 124, '', 'timed out'

    def nmcli(self, *args: str, timeout: float = 15) -> tuple[int, str, str]:
        return self.run(['nmcli', *args], timeout)

    def device(self) -> dict[str, str]:
        """State and active connection of the Wi-Fi interface."""
        _, out, _ = self.nmcli('-t', '-f', 'DEVICE,STATE,CONNECTION', 'device')
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) >= 3 and fields[0] == self.interface:
                return {'state': fields[1], 'connection': fields[2]}
        return {'state': 'unavailable', 'connection': ''}

    def address(self) -> str:
        _, out, _ = self.nmcli('-t', '-g', 'IP4.ADDRESS', 'device', 'show', self.interface)
        return out.strip().split('|')[0].strip()

    def saved(self) -> list[dict[str, Any]]:
        """Saved Wi-Fi client profiles (the setup hotspot and other AP profiles excluded)."""
        _, out, _ = self.nmcli('-t', '-f', 'NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY', 'connection', 'show')
        networks = []
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 4 or fields[1] != '802-11-wireless' or fields[0] == HOTSPOT_NAME:
                continue
            _, detail, _ = self.nmcli('-t', '-g', '802-11-wireless.ssid,802-11-wireless.mode', 'connection', 'show', fields[0])
            values = detail.splitlines()
            if len(values) >= 2 and values[1] == 'ap':
                continue
            networks.append({'name': fields[0], 'ssid': values[0] if values else '', 'autoconnect': fields[2] == 'yes',
                             'priority': int(fields[3]) if fields[3].lstrip('-').isdigit() else 0})
        return sorted(networks, key=lambda item: (-item['priority'], item['name']))

    def scan(self, rescan: bool) -> list[dict[str, Any]]:
        _, out, _ = self.nmcli('-t', '-f', 'SSID,SIGNAL,SECURITY,CHAN', 'device', 'wifi', 'list',
                               'ifname', self.interface, '--rescan', 'yes' if rescan else 'no', timeout=30)
        best: dict[str, dict[str, Any]] = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) < 4 or not fields[0]:
                continue
            signal = int(fields[1]) if fields[1].isdigit() else 0
            if fields[0] not in best or signal > best[fields[0]]['signal']:
                best[fields[0]] = {'ssid': fields[0], 'signal': signal, 'security': fields[2] or 'open', 'channel': fields[3]}
        return sorted(best.values(), key=lambda item: -item['signal'])

    def stations(self) -> int:
        """Clients connected to the hotspot (0 when unknown)."""
        code, out, _ = self.run(['iw', 'dev', self.interface, 'station', 'dump'], 10)
        return out.count('Station ') if code == 0 else 0

    def up(self, name: str, wait: int = CONNECT_WAIT_SEC) -> tuple[bool, str]:
        code, out, err = self.nmcli('--wait', str(wait), 'connection', 'up', name, 'ifname', self.interface, timeout=wait + 10)
        # First line only: nmcli adds a journalctl "Hint:" line to failures.
        lines = (err or out).strip().splitlines()
        return code == 0, lines[0] if lines else ''

    def down(self, name: str) -> None:
        self.nmcli('connection', 'down', name)

    def add(self, ssid: str, password: str, priority: int) -> str:
        """Create a client profile, or update the existing profile for this SSID."""
        existing = next((n for n in self.saved() if n['ssid'] == ssid), None)
        security = (['wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password] if password
                    else ['remove', '802-11-wireless-security'])
        if existing:
            args = ['connection', 'modify', existing['name'], 'connection.autoconnect', 'yes',
                    'connection.autoconnect-priority', str(priority)]
            if password:
                args += security
            else:
                self.nmcli('connection', 'modify', existing['name'], *security)
            code, _, err = self.nmcli(*args)
            if code != 0:
                raise ValueError(err.strip() or 'Could not update the network')
            return existing['name']
        taken = {n['name'] for n in self.saved()} | {HOTSPOT_NAME}
        base = 'wifi-' + (re.sub(r'[^A-Za-z0-9._-]', '-', ssid).strip('-') or 'network')
        name, index = base, 2
        while name in taken:
            name, index = f'{base}-{index}', index + 1
        args = ['connection', 'add', 'type', 'wifi', 'ifname', self.interface, 'con-name', name, 'ssid', ssid,
                'connection.autoconnect', 'yes', 'connection.autoconnect-priority', str(priority)]
        if password:
            args += security
        code, _, err = self.nmcli(*args)
        if code != 0:
            raise ValueError(err.strip() or 'Could not create the network')
        return name

    def set_priority(self, name: str, priority: int) -> None:
        code, _, err = self.nmcli('connection', 'modify', name, 'connection.autoconnect-priority', str(priority))
        if code != 0:
            raise ValueError(err.strip() or 'Could not change the priority')

    def delete(self, name: str) -> None:
        code, _, err = self.nmcli('connection', 'delete', name)
        if code != 0:
            raise ValueError(err.strip() or 'Could not delete the network')

    def ensure_hotspot(self, config: dict[str, Any]) -> None:
        """Create the hotspot profile, or recreate it when its SSID, password, address or PMF changed.

        PMF is disabled (terse value 1): with the default the Pi's brcmfmac access point
        advertises WPA2/WPA3 and iPhones fail to join; plain WPA2-PSK works with every client.
        """
        code, out, _ = self.nmcli('-s', '-t', '-g', '802-11-wireless.ssid,802-11-wireless-security.psk,ipv4.addresses,'
                                  '802-11-wireless-security.pmf', 'connection', 'show', HOTSPOT_NAME)
        wanted = [config['hotspot_ssid'], config['hotspot_password'], config['hotspot_address'], '1']
        if code == 0 and out.splitlines()[:4] == wanted:
            return
        if code == 0:
            self.nmcli('connection', 'delete', HOTSPOT_NAME)
        code, _, err = self.nmcli(
            'connection', 'add', 'type', 'wifi', 'ifname', self.interface, 'con-name', HOTSPOT_NAME,
            'ssid', config['hotspot_ssid'], 'connection.autoconnect', 'no',
            '802-11-wireless.mode', 'ap', '802-11-wireless.band', 'bg',
            '802-11-wireless.channel', str(config['hotspot_channel']),
            'ipv4.method', 'shared', 'ipv4.addresses', config['hotspot_address'], 'ipv6.method', 'disabled',
            'wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.proto', 'rsn', 'wifi-sec.pairwise', 'ccmp',
            'wifi-sec.group', 'ccmp', 'wifi-sec.pmf', 'disable', 'wifi-sec.psk', config['hotspot_password'])
        if code != 0:
            raise RuntimeError(f'cannot create the setup hotspot: {err.strip()}')


class Supervisor:
    """Client/hotspot state machine; every NetworkManager change holds `lock`."""

    def __init__(self, nm: Nm, config: dict[str, Any], clock: Callable[[], float] = time.monotonic):
        self.nm = nm
        self.config = config
        self.clock = clock
        self.lock = threading.RLock()
        now = clock()
        self.mode = 'starting'
        self.last_connected = now
        self.hotspot_since: float | None = None
        self.hold_until = 0.0
        self.busy = ''
        self.last_result: dict[str, Any] | None = None
        self.scan_cache: list[dict[str, Any]] = []
        self.client: dict[str, str] = {}

    # --- loop ---------------------------------------------------------------------
    def tick(self) -> None:
        with self.lock:
            now = self.clock()
            device = self.nm.device()
            if device['state'] == 'connected' and device['connection'] == HOTSPOT_NAME:
                self.mode = 'hotspot'
                if self.hotspot_since is None:
                    self.hotspot_since = now
                due = now - self.hotspot_since >= self.config['retry_sec'] and now >= self.hold_until
                if due and self.nm.stations() == 0:
                    self.retry_saved()
            elif device['state'] == 'connected':
                self.mode = 'client'
                self.hotspot_since = None
                self.last_connected = now
                self.client = {'connection': device['connection']}
            else:
                self.mode = 'waiting'
                self.hotspot_since = None
                if now - self.last_connected >= self.config['grace_sec']:
                    log(f'no Wi-Fi connection for {now - self.last_connected:.0f} s: starting the setup hotspot')
                    self.start_hotspot()

    def start_hotspot(self) -> bool:
        with self.lock:
            # Scanning is unreliable while the radio is an access point, so scan first.
            self.scan_cache = self.nm.scan(rescan=True) or self.scan_cache
            ok, detail = self.nm.up(HOTSPOT_NAME, 30)
            now = self.clock()
            if ok:
                self.mode, self.hotspot_since = 'hotspot', now
                log(f'setup hotspot {self.config["hotspot_ssid"]} is up')
            else:
                # Try again after another grace period rather than in a tight loop.
                self.last_connected = now
                log(f'setup hotspot failed: {detail}')
            return ok

    def retry_saved(self) -> bool:
        """Leave the hotspot and try the saved autoconnect networks that are in range."""
        with self.lock:
            log('retrying the saved networks')
            self.nm.down(HOTSPOT_NAME)
            self.hotspot_since = None
            self.scan_cache = self.nm.scan(rescan=True) or self.scan_cache
            visible = {item['ssid'] for item in self.scan_cache}
            for network in self.nm.saved():
                if network['autoconnect'] and network['ssid'] in visible:
                    ok, detail = self.nm.up(network['name'])
                    if ok:
                        self.mode, self.last_connected = 'client', self.clock()
                        log(f'connected to {network["name"]}')
                        return True
                    log(f'{network["name"]} failed: {detail}')
            self.start_hotspot()
            return False

    # --- actions from the page ----------------------------------------------------
    def connect(self, name: str) -> dict[str, Any]:
        """Connect to a saved network; on failure restore the previous one or the hotspot."""
        with self.lock:
            device = self.nm.device()
            previous = device['connection'] if device['state'] == 'connected' else ''
            self.busy = f'connecting to {name}'
            try:
                if previous == HOTSPOT_NAME:
                    self.nm.down(HOTSPOT_NAME)
                    self.hotspot_since = None
                ok, detail = self.nm.up(name)
                if ok:
                    self.mode, self.last_connected = 'client', self.clock()
                    result = {'ok': True, 'network': name, 'message': f'Connected to {name}'}
                else:
                    result = {'ok': False, 'network': name, 'message': f'Could not connect to {name}: {detail}'}
                    if previous and previous != HOTSPOT_NAME and self.nm.up(previous)[0]:
                        self.mode, self.last_connected = 'client', self.clock()
                        result['message'] += f'; reconnected to {previous}'
                    else:
                        self.start_hotspot()
                        result['message'] += '; the setup hotspot is back'
            finally:
                self.busy = ''
            result['at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            self.last_result = result
            log(result['message'])
            return result

    def force_hotspot(self, minutes: float) -> bool:
        """Start the setup hotspot now and keep it for `minutes` before retrying."""
        with self.lock:
            self.hold_until = self.clock() + minutes * 60
            ok = self.start_hotspot()
            self.last_result = {'ok': ok, 'message': f'Setup hotspot started for {minutes:g} min' if ok
                                else 'The setup hotspot could not be started', 'at': time.strftime('%Y-%m-%d %H:%M:%S')}
            return ok

    def status(self) -> dict[str, Any]:
        now = self.clock()
        device = self.nm.device()
        hotspot = self.mode == 'hotspot'
        next_retry = None
        if hotspot and self.hotspot_since is not None:
            next_retry = max(0.0, self.hotspot_since + self.config['retry_sec'] - now, self.hold_until - now)
        hotspot_in = None
        if self.mode == 'waiting':
            hotspot_in = max(0.0, self.last_connected + self.config['grace_sec'] - now)
        return {
            'mode': self.mode, 'busy': self.busy, 'state': device['state'], 'connection': device['connection'],
            'address': self.nm.address() if device['state'] == 'connected' else '',
            'hotspot': {'ssid': self.config['hotspot_ssid'], 'address': self.config['hotspot_address'].split('/')[0],
                        'active': hotspot, 'clients': self.nm.stations() if hotspot else 0},
            'grace_sec': self.config['grace_sec'], 'retry_sec': self.config['retry_sec'],
            'next_retry_in': None if next_retry is None else round(next_retry),
            'hotspot_in': None if hotspot_in is None else round(hotspot_in),
            'last_result': self.last_result,
        }

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as error:  # The watchdog must never die.
                log(f'watchdog error: {error}')
            time.sleep(LOOP_SEC)


def background(work: Callable[[], Any]) -> None:
    threading.Thread(target=work, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = 'bms-wifi-setup'

    def log_message(self, *_: Any) -> None:
        pass

    def send(self, body: Any, code: int = 200, content_type: str = 'application/json') -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def allowed(self) -> bool:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if peer.version == 6 and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        return any(peer in ipaddress.ip_network(network) for network in self.server.supervisor.config['allow_networks'])

    def body(self) -> dict[str, Any]:
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 <= size <= 4096:
            raise ValueError('Invalid request size')
        data = json.loads(self.rfile.read(size) or b'{}')
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        return data

    def network(self, name: str) -> dict[str, Any]:
        network = next((n for n in self.server.supervisor.nm.saved() if n['name'] == name), None)
        if network is None:
            raise LookupError('Unknown network')
        return network

    def dispatch(self) -> None:
        supervisor: Supervisor = self.server.supervisor
        url = urlparse(self.path)
        try:
            if not self.allowed():
                return self.send({'error': 'Wi-Fi setup is restricted to the setup hotspot and the central network'}, 403)
            if self.command == 'GET':
                if url.path in ('/', '/index.html'):
                    return self.send(PAGE, content_type='text/html; charset=utf-8')
                if url.path == '/api/status':
                    return self.send(supervisor.status())
                if url.path == '/api/networks':
                    return self.send({'networks': supervisor.nm.saved()})
                if url.path == '/api/scan':
                    rescan = parse_qs(url.query).get('rescan') == ['1'] and supervisor.mode != 'hotspot'
                    if rescan:
                        with supervisor.lock:
                            supervisor.scan_cache = supervisor.nm.scan(rescan=True)
                    return self.send({'networks': supervisor.scan_cache, 'live': rescan})
                # Captive-portal checks of phones and laptops land on the setup page.
                if supervisor.mode == 'hotspot':
                    self.send_response(302)
                    self.send_header('Location', f'http://{supervisor.config["hotspot_address"].split("/")[0]}/')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return None
                return self.send({'error': 'Unknown route'}, 404)
            if self.headers.get(HEADER) != '1':
                return self.send({'error': f'{HEADER} header required'}, 403)
            if supervisor.busy:
                return self.send({'error': f'Busy: {supervisor.busy}'}, 409)
            parts = [unquote(part) for part in url.path.strip('/').split('/')]
            if self.command == 'POST' and parts == ['api', 'networks']:
                data = self.body()
                ssid = validate_ssid(data.get('ssid'))
                password = validate_password(data.get('password'))
                priority = validate_priority(data.get('priority', 10))
                with supervisor.lock:
                    name = supervisor.nm.add(ssid, password, priority)
                log(f'saved network {name} ({ssid}, priority {priority})')
                if data.get('connect'):
                    background(lambda: supervisor.connect(name))
                return self.send({'name': name, 'connecting': bool(data.get('connect'))})
            if len(parts) == 4 and parts[:2] == ['api', 'networks'] and self.command == 'POST':
                name = self.network(parts[2])['name']
                if parts[3] == 'priority':
                    priority = validate_priority(self.body().get('priority'))
                    with supervisor.lock:
                        supervisor.nm.set_priority(name, priority)
                    return self.send({'name': name, 'priority': priority})
                if parts[3] == 'connect':
                    background(lambda: supervisor.connect(name))
                    return self.send({'name': name, 'connecting': True})
            if len(parts) == 3 and parts[:2] == ['api', 'networks'] and self.command == 'DELETE':
                name = self.network(parts[2])['name']
                with supervisor.lock:
                    supervisor.nm.delete(name)
                log(f'deleted network {name}')
                return self.send({'deleted': name})
            if self.command == 'POST' and parts == ['api', 'hotspot']:
                minutes = float(self.body().get('minutes', 10))
                if not 1 <= minutes <= 60:
                    raise ValueError('minutes must be 1-60')
                background(lambda: supervisor.force_hotspot(minutes))
                return self.send({'starting': True, 'minutes': minutes})
            return self.send({'error': 'Unknown route'}, 404)
        except LookupError as error:
            self.send({'error': str(error)}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.send({'error': str(error)}, 400)

    do_GET = do_POST = do_DELETE = dispatch


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera Wi-Fi setup</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f3f6fa;color:#1d2733}
main{max-width:760px;margin:0 auto;padding:14px}
h1{font-size:1.3rem;margin:6px 0 12px}h2{font-size:1rem;margin:0 0 10px}
.card{background:#fff;border:1px solid #d6dee8;border-radius:10px;padding:14px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:.9rem}td,th{padding:6px 4px;border-bottom:1px solid #e6ebf1;text-align:left}
th{color:#5b6b7d;font-size:.75rem;text-transform:uppercase}
input,button{font:inherit;padding:7px 9px;border:1px solid #b9c6d6;border-radius:7px}
button{background:#1f5f9a;color:#fff;border-color:#1f5f9a;cursor:pointer}button.secondary{background:#fff;color:#1f5f9a}
button.danger{background:#fff;color:#b42318;border-color:#e7a19a}button:disabled{opacity:.5}
form{display:grid;gap:8px;grid-template-columns:1fr 1fr 90px}form label{display:grid;gap:3px;font-size:.8rem;color:#5b6b7d}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.muted{color:#5b6b7d;font-size:.85rem}
.msg{margin:8px 0 0;font-size:.9rem}.ok{color:#1b7d4d}.err{color:#b42318}.prio{width:70px}
@media(max-width:560px){form{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Camera Wi-Fi setup</h1>
<div class="card"><h2>Status</h2><div id="status" class="muted">Loading…</div><p id="result" class="msg"></p></div>
<div class="card"><h2>Saved networks</h2><p class="muted">The camera connects to the highest priority network in range.</p>
<table><thead><tr><th>Network</th><th>SSID</th><th>Priority</th><th></th></tr></thead><tbody id="saved"></tbody></table></div>
<div class="card"><h2>Add a network</h2>
<form id="add"><label>SSID<input id="ssid" maxlength="32" required></label>
<label>Password<input id="password" type="password" maxlength="64" placeholder="empty = open network"></label>
<label>Priority<input id="priority" type="number" value="20" min="-999" max="999"></label>
<div class="row" style="grid-column:1/-1"><label class="row" style="display:flex"><input type="checkbox" id="connect" checked> Connect now</label>
<button type="submit">Save network</button></div></form><p id="addMsg" class="msg"></p></div>
<div class="card"><div class="row"><h2 style="margin:0">Networks in range</h2><button class="secondary" id="rescan">Rescan</button></div>
<p id="scanNote" class="muted"></p><table><tbody id="scan"></tbody></table></div>
<div class="card"><h2>Setup hotspot</h2><p class="muted" id="hotspotNote"></p>
<button class="secondary" id="hotspotBtn">Start setup hotspot for 10 min</button></div>
</main><script>
const H={'Content-Type':'application/json','X-BMS-Wifi-Setup':'1'};
const $=(id)=>document.getElementById(id);
const esc=(t)=>String(t??'').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let mode='';
async function api(method,url,body){const r=await fetch(url,{method,headers:H,body:body?JSON.stringify(body):undefined,cache:'no-store'});
const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d;}
async function status(){try{const s=await api('GET','api/status');mode=s.mode;
const parts=[];
if(s.mode==='client')parts.push(`Connected to <b>${esc(s.connection)}</b> · ${esc(s.address)}`);
if(s.mode==='hotspot')parts.push(`Setup hotspot <b>${esc(s.hotspot.ssid)}</b> is on · page at http://${esc(s.hotspot.address)}/ · ${s.hotspot.clients} client(s)`+(s.next_retry_in!=null?` · retries saved networks in ${s.next_retry_in} s when no client is connected`:''));
if(s.mode==='waiting')parts.push(`No Wi-Fi connection · setup hotspot starts in ${s.hotspot_in} s`);
if(s.mode==='starting')parts.push('Starting…');
if(s.busy)parts.push(`<b>${esc(s.busy)}…</b>`);
$('status').innerHTML=parts.join('<br>');
if(s.last_result){$('result').className='msg '+(s.last_result.ok?'ok':'err');$('result').textContent=`${s.last_result.at}: ${s.last_result.message}`;}
$('hotspotNote').textContent=`Without a Wi-Fi connection for ${s.grace_sec} s the camera opens the hotspot ${s.hotspot.ssid} (page http://${s.hotspot.address}/). Starting it now disconnects the camera from its network; it returns on its own after the time below if nobody is connected to the hotspot.`;
$('hotspotBtn').disabled=s.mode==='hotspot';$('rescan').disabled=s.mode==='hotspot';
$('scanNote').textContent=s.mode==='hotspot'?'List from before the hotspot started (the radio cannot scan while it is a hotspot).':'';
}catch(e){$('status').textContent='Camera unreachable: '+e.message;}}
async function saved(){const d=await api('GET','api/networks');
$('saved').innerHTML=d.networks.map((n)=>`<tr><td>${esc(n.name)}</td><td>${esc(n.ssid)}</td>
<td><input class="prio" type="number" value="${n.priority}" data-prio="${esc(n.name)}"></td>
<td class="row"><button class="secondary" data-connect="${esc(n.name)}">Connect</button><button class="danger" data-delete="${esc(n.name)}">Delete</button></td></tr>`).join('')||'<tr><td colspan="4" class="muted">No saved networks</td></tr>';}
async function scan(rescan){const d=await api('GET','api/scan'+(rescan?'?rescan=1':''));
$('scan').innerHTML=d.networks.map((n)=>`<tr><td>${esc(n.ssid)}</td><td>${n.signal}%</td><td>${esc(n.security)}</td><td><button class="secondary" data-use="${esc(n.ssid)}">Use</button></td></tr>`).join('')||'<tr><td class="muted">No networks found</td></tr>';}
document.addEventListener('click',async(e)=>{const t=e.target;try{
if(t.dataset.use){$('ssid').value=t.dataset.use;$('password').focus();}
if(t.dataset.connect){if(!confirm(`Connect to ${t.dataset.connect}? The camera leaves its current network; if it fails it comes back or opens the setup hotspot.`))return;
await api('POST',`api/networks/${encodeURIComponent(t.dataset.connect)}/connect`);$('result').className='msg';$('result').textContent='Connecting… this page may lose the connection.';}
if(t.dataset.delete){if(!confirm(`Delete ${t.dataset.delete}?`))return;await api('DELETE',`api/networks/${encodeURIComponent(t.dataset.delete)}`);saved();}
if(t.id==='rescan'){t.disabled=true;await scan(true);t.disabled=false;}
if(t.id==='hotspotBtn'){if(!confirm('Disconnect the camera from its network and start the setup hotspot for 10 minutes?'))return;await api('POST','api/hotspot',{minutes:10});}
}catch(err){alert(err.message);}});
document.addEventListener('change',async(e)=>{const t=e.target;if(!t.dataset.prio)return;
try{await api('POST',`api/networks/${encodeURIComponent(t.dataset.prio)}/priority`,{priority:Number(t.value)});saved();}catch(err){alert(err.message);}});
$('add').addEventListener('submit',async(e)=>{e.preventDefault();try{
const d=await api('POST','api/networks',{ssid:$('ssid').value,password:$('password').value,priority:Number($('priority').value),connect:$('connect').checked});
$('addMsg').className='msg ok';$('addMsg').textContent=d.connecting?`Saved as ${d.name}; connecting… this page may lose the connection.`:`Saved as ${d.name}.`;
$('password').value='';saved();}catch(err){$('addMsg').className='msg err';$('addMsg').textContent=err.message;}});
status();saved();scan(false);setInterval(status,3000);setInterval(saved,15000);
</script></body></html>'''


def main() -> None:
    config = load_config(Path(os.environ.get('BMS_WIFI_SETUP_CONFIG', '/etc/bms-zero-camera/wifi-setup.json')))
    nm = Nm(config['interface'])
    nm.ensure_hotspot(config)
    supervisor = Supervisor(nm, config)
    threading.Thread(target=supervisor.run_forever, name='wifi-watchdog', daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', int(config['http_port'])), Handler)
    server.supervisor = supervisor
    log(f'Wi-Fi setup: hotspot {config["hotspot_ssid"]}, grace {config["grace_sec"]} s, retry {config["retry_sec"]} s, '
        f'page on port {config["http_port"]}')
    server.serve_forever()


if __name__ == '__main__':
    main()
