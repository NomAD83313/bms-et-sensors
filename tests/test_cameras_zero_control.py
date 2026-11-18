"""Zero capture validation, atomic publisher updates and access restrictions."""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.cameras.zero_control import CaptureControl, Handler, CAPABILITIES


class ZeroCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'mediamtx.json'
        config = json.loads(Path('config/zero2w-camera.example.json').read_text())
        config['paths']['cam']['rpiCameraMode'] = '1296:972:10:P'
        self.path.write_text(json.dumps(config))
        self.control = CaptureControl(self.path)

    def test_persistent_updates_keep_transport_and_field_of_view(self):
        before = self.control.config()
        for width, height in ((1920, 1080), (640, 360), (1280, 720)):
            with self.subTest(width=width):
                self.control.update(dict(width=width, height=height, fps=5))
                restarted = CaptureControl(self.path)
                settings = restarted.settings()
                self.assertEqual((settings['width'], settings['height'], settings['fps']), (width, height, 5))
                saved = restarted.config()
                camera = saved['paths']['cam']
                self.assertEqual(camera['rpiCameraCodec'], 'hardwareH264')
                self.assertEqual(camera['rpiCameraIDRPeriod'], 5)
                self.assertEqual(camera['rpiCameraMode'], '2592:1944:10:P' if width == 1920 else '1296:972:10:P')
                self.assertEqual(saved['authInternalUsers'], before['authInternalUsers'])
                self.assertEqual(saved['rtspTransports'], before['rtspTransports'])
                self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    def test_unsupported_updates_leave_publisher_config_unchanged(self):
        original = self.path.read_bytes()
        for body in (None, [], {'width': 2560, 'height': 1440}, {'width': True},
                     {'capture_mode': 'snapshots'}, {'fps': 30}, {'fps': 7},
                     {'shutter_us': 200}, {'runOnInit': 'malicious'}, {'snapshot_interval_ms': 500}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.control.update(body)
            self.assertEqual(self.path.read_bytes(), original)

    def test_imx415_mode_is_preserved_when_output_changes(self):
        config = self.control.config()
        config['paths']['cam']['rpiCameraMode'] = '3864:2192:10:P'
        self.path.write_text(json.dumps(config))

        self.control.update(dict(width=640, height=360, fps=5))

        self.assertEqual(self.control.config()['paths']['cam']['rpiCameraMode'], '3864:2192:10:P')

    def test_access_is_limited_to_configured_reader_ips(self):
        self.assertTrue(self.control.allowed('10.42.0.1'))
        self.assertTrue(self.control.allowed('127.0.0.1'))
        self.assertFalse(self.control.allowed('10.42.0.110'))
        self.assertFalse(self.control.allowed('192.168.1.1'))

    def test_http_settings_capabilities_and_control_header(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.control = self.control
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/api/capabilities') as response:
                self.assertEqual(json.load(response), CAPABILITIES)
            body = json.dumps(dict(width=1920, height=1080, fps=10)).encode()
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + '/api/config', data=body))
            self.assertEqual(caught.exception.code, 403)
            with urlopen(Request(base + '/api/config', data=body, headers={'X-Camera-Control': '1'})) as response:
                self.assertEqual(json.load(response)['width'], 1920)
            with urlopen(base + '/api/config') as response:
                self.assertEqual(json.load(response)['height'], 1080)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
