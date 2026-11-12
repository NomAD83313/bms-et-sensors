"""Persistent normal sessions, transactional updates and camera isolation."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from app.cameras import anomaly_control, control, dashboard
from app.cameras.learning_sessions import LearningSessions, MAX_SESSIONS
from tests.isolated_import import load_isolated_module
from tests.test_cameras_worker_quality import FakeModelGeometry


CONFIG = dict(model_key='mobilenetv3', input_size='768x432', roi=None,
              color_mode='rgb',
              source_size=[1920, 1080], effective_input_size=[768, 432],
              jpeg_draft=False, score_top_k=1)
JPEG = b'\xff\xd8confirmed-source\xff\xd9'


class LearningStoreTests(unittest.TestCase):
    def test_existing_sessions_without_color_mode_remain_rgb_and_can_be_extended(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningSessions(directory)
            store.publish(self.collect(store), 'original')
            catalog = store.catalog()
            catalog['configuration'].pop('color_mode')
            anomaly_control.write_json(store.root / 'catalog.json', catalog)
            self.assertEqual(store.catalog()['configuration']['color_mode'], 'rgb')
            store.begin('add')
            store.append(JPEG, CONFIG)
            self.assertEqual(len(store.public()), 1)
            with self.assertRaises(ValueError):
                store.append(JPEG, dict(CONFIG, color_mode='grayscale'))

    def test_clear_removes_only_saved_learning_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningSessions(root)
            store.publish(self.collect(store), 'old')
            saved = store.path(store.public()[0]['id'])
            store.begin('add')
            store.append(JPEG, CONFIG)
            pending = store.pending['path']
            (root / 'events').mkdir()
            event = root / 'events' / 'keep.jpg'
            event.write_bytes(JPEG)
            self.assertEqual(store.clear(), 1)
            self.assertEqual(store.public(), [])
            self.assertFalse(saved.exists())
            self.assertFalse(pending.exists())
            self.assertEqual(event.read_bytes(), JPEG)
            self.assertEqual(store.clear(), 0)

    def collect(self, store, mode='new', **kwargs):
        store.begin(mode, **kwargs)
        for _ in range(20):
            store.append(JPEG, CONFIG)
        return store.proposed()

    def test_add_replace_rename_remove_and_restart_preserve_other_states(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningSessions(directory)
            items = self.collect(store)
            store.publish(items, 'first')
            first = store.public()[0]
            self.assertEqual(first['name'], 'Session 1')
            items = self.collect(store, 'add', name='New object', note='Both states are allowed')
            store.publish(items, 'second')
            second = store.public()[1]
            store.rename(second['id'], 'Changed label', 'Changed reason')
            self.assertEqual(store.catalog()['revision'], 'second')
            self.assertEqual(store.public()[1]['note'], 'Changed reason')
            items = self.collect(store, 'replace', identifier=first['id'])
            store.publish(items, 'replacement')
            self.assertEqual(store.public()[0]['name'], 'Session 1')
            self.assertEqual(store.public()[1]['id'], second['id'])
            self.assertFalse(store.path(first['id']).exists())
            items = store.proposed(remove=second['id'])
            store.publish(items, 'removed')
            restarted = LearningSessions(directory)
            self.assertEqual(len(restarted.public()), 1)
            self.assertEqual(restarted.groups(items)[0][0].read_bytes(), JPEG)

    def test_limit_incompatible_geometry_abort_and_camera_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningSessions(Path(directory) / 'pi')
            for index in range(MAX_SESSIONS):
                store.publish(self.collect(store, 'new' if not index else 'add'), str(index))
            with self.assertRaises(ValueError):
                store.begin('add')
            selected = store.public()[0]['id']
            store.begin('replace', identifier=selected)
            with self.assertRaises(ValueError):
                store.append(JPEG, dict(CONFIG, source_size=[1280, 720]))
            store.abort()
            self.assertEqual(len(store.public()), 8)
            store.begin('replace', identifier=selected)
            with patch('app.cameras.learning_sessions.MAX_STORAGE_BYTES', 1):
                with self.assertRaises(OSError):
                    store.append(JPEG, CONFIG)
            store.abort()
            self.assertEqual(len(store.public()), 8)
            self.assertEqual(LearningSessions(Path(directory) / 'wifi').public(), [])
            for value in ('../catalog.json', 'a/b', '', None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.thumbnail(value)

    def test_partial_capture_and_failed_catalog_write_keep_published_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LearningSessions(directory)
            store.publish(self.collect(store), 'original')
            original = store.catalog()
            store.begin('new')
            store.append(JPEG, CONFIG)
            with self.assertRaises(ValueError):
                store.proposed()
            store.abort()
            items = self.collect(store, 'add')
            with patch('app.cameras.learning_sessions.write_json', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.publish(items, 'failed')
            self.assertEqual(store.catalog(), original)
            self.assertTrue(store.thumbnail(original['sessions'][0]['id']).is_file())
            recovered = LearningSessions(directory)
            recovered.recover()
            self.assertEqual(recovered.catalog(), original)
            self.assertEqual([path.name for path in recovered.root.iterdir() if path.is_dir()],
                             [original['sessions'][0]['id']])


class LearningWorkerTests(unittest.TestCase):
    def test_only_incompatible_changes_clear_the_baseline_and_sessions(self):
        class Model(FakeModelGeometry):
            model_key, input_size = 'mobilenetv3', '768x432'
            color_mode = 'rgb'
            roi = None
            memory = None
            baseline_frames = 20
            source_size, effective_input_size = (1920, 1080), (768, 432)
            def set_input_size(self, value): self.input_size, self.memory = value, None
            def set_model(self, value): self.model_key, self.memory = value, None
            def set_roi(self, value): self.roi, self.memory = value, None
            def set_color_mode(self, value): self.color_mode, self.memory = value, None
            def load(self, path): self.memory, self.learning_revision = True, 'original'
            def features(self, jpeg): self.source_size = (1280, 720)
            def score(self, features): raise AssertionError('Do not score a changed source geometry')

        cases = [
            ('roi', dict(roi=dict(x=.25, y=.25, width=.5, height=.5)), True),
            ('resize', dict(input_size='1024x576'), True),
            ('model', dict(model_key='mobilenetv4-convsmall-x0.5'), True),
            ('color', dict(color_mode='grayscale'), True),
            ('invalidate', {}, True), ('source-geometry', {}, True), ('startup-settings', {}, True),
            ('pause', {}, False), ('configure', dict(multiplier=1.3), False),
            ('confirmation', dict(confirmation_frames=1), False),
            ('roi', dict(roi=None), False), ('resize', dict(input_size='768x432'), False),
            ('model', dict(model_key='mobilenetv3'), False), ('temporary-error', {}, False),
            ('color', dict(color_mode='rgb'), False),
        ]
        for action, arguments, cleared in cases:
            with self.subTest(action=action, arguments=arguments), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = LearningSessions(root)
                store.begin('new')
                for _ in range(20): store.append(JPEG, CONFIG)
                store.publish(store.proposed(), 'original')
                saved = store.path(store.public()[0]['id'])
                (root / 'baseline.npz').write_bytes(b'baseline')
                anomaly_control.write_json(root / 'session.json', dict(
                    model_key='mobilenetv3', input_size='1024x576' if action == 'startup-settings' else '768x432',
                    monitoring=True))
                module = types.ModuleType('patchcore_lite')
                module.PatchCoreLite, module.overlay = Model, Mock()
                with patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                    worker_module = load_isolated_module('app.cameras.anomaly_worker', {
                        'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                    worker = worker_module.AnomalyWorker()
                    if action == 'source-geometry':
                        worker.fetch_analysis_frame = Mock(return_value=(JPEG, None, 0))
                        worker.step()
                    elif action == 'temporary-error':
                        worker.handle_error(OSError('Unavailable'))
                    elif action != 'startup-settings':
                        anomaly_control.request(action, **arguments)
                        worker.apply_pending_command()
                    self.assertEqual(store.public() == [], cleared)
                    self.assertEqual(not saved.exists(), cleared)
                    self.assertEqual(not (root / 'baseline.npz').exists(), cleared)
                    if cleared:
                        self.assertIsNone(worker.model.memory)
                        self.assertFalse(worker.monitoring)
                    if action == 'color':
                        self.assertEqual(anomaly_control.read_json(root / 'session.json')['color_mode'], arguments['color_mode'])
                        restored = worker_module.AnomalyWorker()
                        self.assertEqual(restored.model.color_mode, arguments['color_mode'])

    def test_pytorch_era_baseline_is_rebuilt_from_saved_sessions_once(self):
        class Model(FakeModelGeometry):
            model_key, input_size = 'mobilenetv3', '768x432'
            source_size, effective_input_size = (1920, 1080), (768, 432)
            threshold = 1

            def set_input_size(self, value):
                self.input_size, self.memory = value, None

            def fit_sessions(self, groups, progress=None):
                self.memory = True
                self.baseline_frames = sum(map(len, groups))
                return [dict(calibration_frames=len(paths) // 5, calibration_threshold=1) for paths in groups]

            def save(self, path):
                path.write_text(json.dumps(dict(revision=self.learning_revision, frames=self.baseline_frames)))

            def load(self, path):
                saved = json.loads(path.read_text())
                self.learning_revision, self.baseline_frames = saved['revision'], saved['frames']
                self.memory = True

        for resume in (True, False):
            with self.subTest(monitoring=resume), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = LearningSessions(root)
                store.begin('new')
                for _ in range(20):
                    store.append(JPEG, CONFIG)
                store.publish(store.proposed(), 'legacy')
                for name in ('baseline.pt', 'baseline-previous.pt'):
                    (root / name).write_bytes(b'pytorch pickle')
                anomaly_control.write_json(root / 'session.json', dict(model_key='mobilenetv3', monitoring=resume))
                module = types.ModuleType('patchcore_lite')
                module.PatchCoreLite, module.overlay = Model, Mock()
                with patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                    worker_module = load_isolated_module('app.cameras.anomaly_worker', {
                        'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                    worker = worker_module.AnomalyWorker()
                    self.assertTrue(worker.pending_migration)
                    self.assertIsNone(worker.model.memory)
                    self.assertFalse(worker.monitoring)
                    worker.fetch_analysis_frame = Mock(return_value=(JPEG, None, 0))
                    worker.validate_frame_token = Mock()
                    worker.step()
                    self.assertFalse(worker.pending_migration)
                    self.assertTrue((root / 'baseline.npz').exists())
                    for name in ('baseline.pt', 'baseline-previous.pt'):
                        self.assertFalse((root / name).exists())
                    self.assertTrue(worker.model.memory)
                    self.assertEqual(worker.monitoring, resume)
                    self.assertFalse(worker.baseline_invalidated)
                    self.assertEqual(worker.sessions.catalog()['revision'], worker.model.learning_revision)
                    again = worker_module.AnomalyWorker()
                    self.assertFalse(again.pending_migration)
                    self.assertTrue(again.model.memory)

    def test_pytorch_era_baseline_without_saved_sessions_requires_new_learning(self):
        class Model(FakeModelGeometry):
            model_key, input_size = 'mobilenetv3', '768x432'

            def set_input_size(self, value):
                self.input_size, self.memory = value, None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'baseline.pt').write_bytes(b'pytorch pickle')
            anomaly_control.write_json(root / 'session.json', dict(model_key='mobilenetv3', monitoring=True))
            module = types.ModuleType('patchcore_lite')
            module.PatchCoreLite, module.overlay = Model, Mock()
            with patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                worker_module = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                worker = worker_module.AnomalyWorker()
                self.assertFalse(worker.pending_migration)
                self.assertFalse(worker.monitoring)
                self.assertTrue(worker.baseline_invalidated)
                self.assertFalse((root / 'baseline.pt').exists())

    def test_accumulation_rebuild_delete_and_failed_training_rollback(self):
        class Model(FakeModelGeometry):
            model_key, input_size = 'mobilenetv3', '768x432'
            source_size, effective_input_size = (1920, 1080), (768, 432)
            roi = None
            memory = None
            baseline_frames = 0
            threshold = 1
            learning_revision = None
            fail = False
            def set_input_size(self, value):
                self.input_size, self.memory = value, None
            def features(self, jpeg):
                if jpeg != JPEG:
                    raise AssertionError('Use the full verified JPEG')
                return 'features'
            def fit_sessions(self, groups, progress=None):
                if self.fail:
                    raise RuntimeError('Simulated fit failure')
                for paths in groups:
                    for path in paths:
                        assert path.read_bytes() == JPEG
                self.memory = True
                self.baseline_frames = sum(map(len, groups))
                return [dict(calibration_frames=len(paths) // 5, calibration_threshold=1)
                        for paths in groups]
            def save(self, path):
                path.write_text(json.dumps(dict(revision=self.learning_revision, frames=self.baseline_frames)))
            def load(self, path):
                saved = json.loads(path.read_text())
                self.learning_revision, self.baseline_frames = saved['revision'], saved['frames']
                self.memory = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anomaly_control.write_json(root / 'session.json', dict(model_key='mobilenetv3'))
            module = types.ModuleType('patchcore_lite')
            module.PatchCoreLite, module.overlay = Model, Mock()
            with patch.object(anomaly_control, 'DATA', root), patch.object(control, 'CONTROL', root / 'control'):
                worker_module = load_isolated_module('app.cameras.anomaly_worker', {
                    'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
                worker = worker_module.AnomalyWorker()
                worker.fetch_analysis_frame = Mock(return_value=(JPEG, None, 0))
                worker.validate_frame_token = Mock()

                def command(action, **kwargs):
                    anomaly_control.request(action, learning_seconds=60, **kwargs)
                    worker.apply_pending_command()

                def collect():
                    for _ in range(30):
                        worker.analyze_frame({}, time.monotonic())

                command('learn')
                collect()
                first = worker.sessions.public()[0]
                self.assertTrue(worker.monitoring)
                self.assertEqual(worker.model.baseline_frames, 30)
                command('add-session')
                worker.analyze_frame({}, time.monotonic())
                command('pause')
                self.assertFalse(worker.learning)
                self.assertIsNone(worker.sessions.pending)
                self.assertTrue(worker.model.memory)
                self.assertEqual(len(worker.sessions.public()), 1)
                command('add-session', session_name='A normal object')
                collect()
                self.assertEqual(len(worker.sessions.public()), 2)
                self.assertEqual(worker.model.baseline_frames, 60)
                command('rename-session', session_id=first['id'], session_name='Original view', session_note='No object')
                self.assertTrue(worker.monitoring)
                self.assertEqual(worker.sessions.public()[0]['name'], 'Original view')

                baseline = worker.model_path.read_bytes()
                catalog = worker.sessions.catalog()
                worker.model_path.with_name('baseline-previous.npz').write_bytes(baseline)
                worker.model_path.write_text(json.dumps(dict(revision='interrupted', frames=90)))
                recovered = worker_module.AnomalyWorker()
                self.assertEqual(recovered.model_path.read_bytes(), baseline)
                self.assertEqual(recovered.model.baseline_frames, 60)
                worker.model_path.write_text(json.dumps(dict(revision='unmatched', frames=90)))
                rejected = worker_module.AnomalyWorker()
                self.assertIsNone(rejected.model.memory)
                self.assertFalse(rejected.monitoring)
                self.assertTrue(rejected.baseline_invalidated)
                worker.model_path.write_bytes(baseline)
                command('add-session')
                worker.model.fail = True
                with self.assertRaises(RuntimeError):
                    collect()
                self.assertEqual(worker.model_path.read_bytes(), baseline)
                self.assertEqual(worker.sessions.catalog(), catalog)
                self.assertFalse(worker.monitoring)
                self.assertTrue(worker.model.memory)
                worker.model.fail = False

                command('remove-session', session_id=first['id'])
                self.assertEqual(len(worker.sessions.public()), 1)
                self.assertEqual(worker.model.baseline_frames, 30)
                self.assertFalse(worker.monitoring)
                last = worker.sessions.public()[0]['id']
                command('remove-session', session_id=last)
                self.assertEqual(worker.sessions.public(), [])
                self.assertIsNone(worker.model.memory)
                self.assertFalse(worker.model_path.exists())


class LearningApiTests(unittest.TestCase):
    def test_thumbnail_serves_only_published_frames_at_preview_size(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Requires the camera dashboard Pillow dependency')
        stream = io.BytesIO()
        Image.new('RGB', (640, 480), (120, 140, 160)).save(stream, format='JPEG')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningSessions(root)
            store.begin('new')
            for _ in range(20):
                store.append(stream.getvalue(), CONFIG)
            store.publish(store.proposed(), 'published')
            identifier = store.public()[0]['id']
            with patch.object(anomaly_control, 'DATA', root):
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}/api/anomaly/sessions/'
                try:
                    with urllib.request.urlopen(base + identifier, timeout=3) as response:
                        self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
                        image = Image.open(io.BytesIO(response.read()))
                    self.assertEqual(image.size, (320, 240))
                    catalog = store.catalog()
                    catalog['configuration']['roi'] = dict(x=.25, y=.25, width=.5, height=.5)
                    anomaly_control.write_json(store.root / 'catalog.json', catalog)
                    # Current live settings must not move the saved session's region.
                    anomaly_control.write_json(root / 'status.json', dict(roi=dict(x=0, y=0, width=.1, height=.1)))
                    with urllib.request.urlopen(base + identifier, timeout=3) as response:
                        roi_image = Image.open(io.BytesIO(response.read())).convert('RGB')
                    inside = roi_image.getpixel((160, 120))
                    outside = roi_image.getpixel((20, 200))
                    border = roi_image.getpixel((80, 120))
                    self.assertGreater(sum(inside), sum(outside) + 100)
                    self.assertGreater(border[1], border[0] + 15)
                    self.assertGreater(border[1], border[2] + 20)
                    for name in ('b' * 32, '..%2Fcatalog.json'):
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            urllib.request.urlopen(base + name, timeout=3)
                        self.assertEqual(error.exception.code, 404)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)

    def test_session_commands_validate_ids_slots_geometry_and_pause_event_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningSessions(root)
            store.begin('new')
            for _ in range(20):
                store.append(JPEG, CONFIG)
            store.publish(store.proposed(), 'first')
            identifier = store.public()[0]['id']
            with patch.object(anomaly_control, 'DATA', root), \
                    patch.object(control, 'CONTROL', root / 'control'), \
                    patch.object(dashboard, 'RECORDINGS', root / 'recordings'), \
                    patch.object(dashboard.LIVE, 'geometry', return_value=(1920, 1080)) as geometry, \
                    patch.dict('os.environ', {'CAMERA_RECORDING_MODE': 'anomaly'}):
                state = dict(CONFIG, state='paused', updated=time.time(), baseline_ready=True)
                anomaly_control.write_json(root / 'status.json', state)
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}/api/anomaly'
                def post(action, body, authenticated=True):
                    return urllib.request.urlopen(urllib.request.Request(
                        base + '/' + action, data=json.dumps(body).encode(),
                        headers={'X-Camera-Control': '1'} if authenticated else {}), timeout=3)
                try:
                    with urllib.request.urlopen(base, timeout=3) as response:
                        status = json.load(response)
                    self.assertTrue(status['sessions_compatible'])
                    self.assertEqual(status['learning_sessions'][0]['name'], 'Session 1')
                    control.set_enabled(True)
                    with post('add-session', dict(learning_seconds=60, session_name='New normal thing')) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)['session_name'], 'New normal thing')
                    self.assertFalse(control.enabled())
                    saved = (root / 'command.json').read_bytes()
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('remove-session', dict(session_id='../catalog.json'))
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual((root / 'command.json').read_bytes(), saved)
                    geometry.return_value = (1280, 720)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('add-session', dict(learning_seconds=60))
                    self.assertEqual(error.exception.code, 409)
                    with post('rename-session', dict(session_id=identifier, session_name='Renamed')) as response:
                        self.assertEqual(response.status, 202)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('remove-session', dict(session_id=identifier), authenticated=False)
                    self.assertEqual(error.exception.code, 403)
                    control.set_enabled(True)
                    with post('color', dict(color_mode='grayscale')) as response:
                        self.assertEqual(response.status, 202)
                        self.assertEqual(json.load(response)['color_mode'], 'grayscale')
                    self.assertFalse(control.enabled())
                    saved = (root / 'command.json').read_bytes()
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('color', dict(color_mode='gray'))
                    self.assertEqual(error.exception.code, 400)
                    self.assertEqual((root / 'command.json').read_bytes(), saved)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
