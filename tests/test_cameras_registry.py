"""Registry of extra cameras and the files generated from it (standard library only)."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from app.cameras import camera_registry as registry


def entry(identifier='garage', **extra):
    base = dict(id=identifier, label=identifier.title(), url_env='CAMERA_' + identifier.upper().replace('-', '_') + '_URL')
    return dict(base, **extra)


class ValidationTests(unittest.TestCase):
    def test_valid_entries_get_ports_labels_and_keep_their_settings(self):
        cameras = registry.validate_registry(dict(cameras=[
            entry('garage'), entry('yard', port=8090, recording_mode='anomaly', preview_path='/ch1',
                                  settings=dict(interval_sec=.3, model=dict(top_k=5, jpeg_draft=1))),
            dict(id='shed', url_env='CAMERA_SHED_URL')]))
        self.assertEqual([c['port'] for c in cameras], [8085, 8090, 8087])
        self.assertEqual(cameras[2]['label'], 'shed')
        self.assertEqual(cameras[1]['settings']['model'], dict(top_k=5, jpeg_draft=1))
        self.assertEqual(registry.validate_registry(dict(cameras=[])), [])

    def test_invalid_registries_are_rejected_with_a_reason(self):
        bad = {
            'not an object': [],
            'cameras not a list': dict(cameras={}),
            'entry not an object': dict(cameras=['garage']),
            'unknown field': dict(cameras=[entry(password='x')]),
            'upper case id': dict(cameras=[entry('Garage')]),
            'long id': dict(cameras=[entry('a' * 17)]),
            'reserved id': dict(cameras=[entry('pi')]),
            'reserved route id': dict(cameras=[entry('api')]),
            'duplicate id': dict(cameras=[entry('garage'), entry('garage')]),
            'no url variable': dict(cameras=[dict(id='garage')]),
            'url variable in lower case': dict(cameras=[dict(id='garage', url_env='camera_url')]),
            'url instead of variable': dict(cameras=[dict(id='garage', url_env='rtsp://user:pw@10.0.0.2/ch0')]),
            'port too low': dict(cameras=[entry(port=8083)]),
            'port used by the stack': dict(cameras=[entry(port=8082)]),
            'port used by influxdb': dict(cameras=[entry(port=8086)]),
            'duplicate port': dict(cameras=[entry('a', port=8090), entry('b', port=8090)]),
            'bad preview path': dict(cameras=[entry(preview_path='ch1')]),
            'bad recording mode': dict(cameras=[entry(recording_mode='always')]),
            'bad setting': dict(cameras=[entry(settings=dict(interval_sec=0))]),
            'unknown setting': dict(cameras=[entry(settings=dict(speed=1))]),
            'bad model option': dict(cameras=[entry(settings=dict(model=dict(top_k=99)))]),
            'unknown model option': dict(cameras=[entry(settings=dict(model=dict(depth=1)))]),
            'label too long': dict(cameras=[entry(label='x' * 41)]),
        }
        for name, value in bad.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                registry.validate_registry(value)

    def test_automatic_ports_skip_ports_chosen_by_later_entries(self):
        cameras = registry.validate_registry(dict(cameras=[entry('a'), entry('b', port=8085)]))
        self.assertEqual([c['port'] for c in cameras], [8087, 8085])


class RenderingTests(unittest.TestCase):
    def cameras(self):
        return registry.validate_registry(dict(cameras=[
            entry('garage', label='Garage "north"', preview_path='/ch1'),
            entry('yard', port=8090, recording_mode='anomaly')]))

    def test_compose_defines_a_recorder_and_a_dashboard_per_camera_with_labels_and_mounts(self):
        text = registry.render_compose(self.cameras())
        for service in ('cam-garage-recorder', 'cam-garage-dashboard', 'cam-yard-recorder', 'cam-yard-dashboard'):
            self.assertIn(f'\n  {service}:\n', text)
        self.assertIn('"bms.camera.id=garage"', text)
        self.assertIn('"bms.camera.role=dashboard"', text)
        self.assertIn('"bms.camera.port=8090"', text)
        self.assertIn('CAMERA_URL: "${CAMERA_GARAGE_URL:-}"', text)
        self.assertIn('CAMERA_NAME: "Garage \\"north\\""', text, 'Labels are escaped')
        self.assertIn('CAMERA_RECORDING_MODE: "anomaly"', text)
        self.assertIn('CAMERA_RECORDING_MODE: "${CAMERA_RECORDING_MODE:-continuous}"', text)
        self.assertIn('DASHBOARD_PORT: "8090"', text)
        self.assertIn('CAMERA_PREVIEW_PATH: "/ch1"', text)
        self.assertIn('- ./runtime/cameras-extra/garage/anomaly:/anomaly:ro', text)
        self.assertIn('- ./runtime/cameras-extra/garage/anomaly:/anomaly\n', text)
        self.assertIn('profiles: [cameras-extra]', text)
        self.assertNotIn('password', text.lower())
        # The shared worker gets one anomaly and one control mount per extra camera.
        analysis = text.split('  camera-analysis:\n')[1]
        for identifier in ('garage', 'yard'):
            self.assertIn(f'- ./runtime/cameras-extra/{identifier}/anomaly:/anomaly/{identifier}', analysis)
            self.assertIn(f'- ./runtime/cameras-extra/{identifier}/control:/control/{identifier}', analysis)

    def test_no_cameras_produce_no_services(self):
        self.assertNotIn('camera-analysis', registry.render_compose([]))

    def test_analysis_entries_and_nginx_routes_follow_the_registry(self):
        cameras = self.cameras()
        listed = registry.analysis_entries(cameras)
        self.assertEqual(listed[1], dict(id='yard', data='/anomaly/yard', control='/control/yard',
                                         preview='http://127.0.0.1:8090/api/live.jpg', recording_mode='anomaly'))
        self.assertNotIn('recording_mode', listed[0], 'Inherit the same runtime global as the recorder')
        conf = registry.render_nginx(cameras)
        self.assertIn('location /cameras/garage/ {', conf)
        self.assertIn('proxy_pass http://host.docker.internal:8085;', conf)
        self.assertIn('return 301 /cameras/yard/;', conf)
        self.assertEqual(registry.render_nginx([]).count('location'), 0)


class GenerationTests(unittest.TestCase):
    def test_generate_writes_the_files_once_and_removes_the_override_when_the_last_camera_goes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            self.assertEqual(registry.generate(root), ([], [f'{registry.OUTPUT_DIR}/analysis-extra.json',
                                                           f'{registry.OUTPUT_DIR}/nginx/cameras.conf']))
            self.assertFalse((root / registry.COMPOSE_FILE).exists())
            (root / registry.REGISTRY_FILE).write_text(json.dumps(dict(cameras=[entry('garage')])))
            cameras, changed = registry.generate(root)
            self.assertEqual(len(cameras), 1)
            self.assertIn(registry.COMPOSE_FILE, changed)
            for name in ('recordings', 'control', 'anomaly'):
                self.assertTrue((root / registry.EXTRA_DIR / 'garage' / name).is_dir())
            self.assertEqual(json.loads((root / registry.OUTPUT_DIR / 'analysis-extra.json').read_text())[0]['id'], 'garage')
            self.assertEqual(registry.generate(root)[1], [], 'A second run changes nothing')
            (root / registry.REGISTRY_FILE).write_text(json.dumps(dict(cameras=[])))
            registry.generate(root)
            self.assertFalse((root / registry.COMPOSE_FILE).exists())
            self.assertEqual(json.loads((root / registry.OUTPUT_DIR / 'analysis-extra.json').read_text()), [])

    def test_a_broken_registry_is_reported_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            (root / registry.REGISTRY_FILE).write_text('{')
            with self.assertRaises(ValueError):
                registry.generate(root)
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(registry.main(['generate', '--root', str(root)]), 1)
                self.assertEqual(registry.main(['list', '--root', str(root)]), 1)
            self.assertIn('not valid JSON', errors.getvalue())
            self.assertFalse((root / registry.OUTPUT_DIR).exists())


if __name__ == '__main__':
    unittest.main()
