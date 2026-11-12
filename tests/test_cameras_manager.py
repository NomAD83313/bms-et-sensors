"""Shared camera registry, process isolation and HTTP routing."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.cameras.camera_manager import CameraRegistry, CameraManager, Handler, validate_camera


def camera(identifier='first', **changes):
    return dict(dict(id=identifier, label=identifier.title(), url='rtsp://user:secret@camera:8554/live'), **changes)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.directory, self.data = root / 'registry', root / 'data'
        self.directory.mkdir()
        (self.directory / 'cameras.json').write_text(json.dumps({'cameras': []}))
        self.registry = CameraRegistry(self.directory, self.data, 'http://127.0.0.1:8082')

    def test_registry_keeps_paths_and_credentials_private(self):
        self.registry.add(camera('first'))
        self.registry.add(camera('second', enabled=False))
        self.assertNotEqual(self.registry.paths('first'), self.registry.paths('second'))
        self.assertEqual(stat.S_IMODE(self.registry.path.stat().st_mode), 0o600)
        published = (self.directory / 'analysis.json').read_text()
        self.assertNotIn('secret', published)
        self.assertIn('/view/first/api/live.jpg', published)
        self.assertTrue((self.registry.paths('second')['control'] / 'camera-stopped').exists())
        restored = CameraRegistry(self.directory, self.data, 'http://127.0.0.1:8082')
        self.assertFalse(restored.get('second')['enabled'])
        self.assertIn('secret', restored.get('first')['url'])

    def test_invalid_ids_urls_and_duplicates_do_not_replace_existing_camera(self):
        self.registry.add(camera())
        for value in [camera('../bad'), camera('api'), camera(url='file:///etc/passwd'),
                      camera(url='http://example.org/stream.ts'), camera(enabled='true'),
                      camera('pi'), camera(), camera(url='rtsp://host:bad/live')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.registry.add(value)
        self.assertEqual(len(self.registry.cameras), 1)
        self.assertFalse((self.data.parent / 'bad').exists())

    def test_local_csi_capture_controls_are_explicit(self):
        self.assertEqual(validate_camera(dict(id='pi', url='http://127.0.0.1:8084/stream.ts',
                         capture_url='http://127.0.0.1:8084'))['capture_url'], 'http://127.0.0.1:8084')
        with self.assertRaises(ValueError):
            validate_camera(camera(capture_url='http://camera/api'))

    def test_builtin_camera_can_be_registered_again_after_removal(self):
        entry = dict(id='pi', label='Pi', url='http://127.0.0.1:8084/stream.ts',
                     capture_url='http://127.0.0.1:8084', enabled=False)
        self.registry.add(entry)
        self.registry.modify('pi')
        self.registry.add(entry)
        self.assertEqual(self.registry.get('pi')['capture_url'], entry['capture_url'])

    def test_failed_persistence_does_not_disable_or_remove_a_camera(self):
        self.registry.add(camera())
        with patch.object(self.registry, 'persist', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.registry.set_enabled('first', False)
            with self.assertRaises(OSError):self.registry.add(camera('second'))
        self.assertTrue(self.registry.get('first')['enabled'])
        self.assertEqual(len(self.registry.cameras), 1)


class ManagerTests(RegistryTests):
    def manager(self):
        spawned = []
        def spawn(*args, **kwargs):
            process = Mock(pid=1000 + len(spawned))
            process.poll.return_value = None
            spawned.append((process, args, kwargs))
            return process
        manager = CameraManager(self.registry, spawn=spawn)
        return manager, spawned

    def test_edit_stops_only_selected_camera_and_preserves_credentials_and_learning(self):
        self.registry.add(camera())
        self.registry.add(camera('second'))
        manager, _ = self.manager()
        manager.reconcile()
        untouched = {k: p for k, p in manager.jobs.items() if k[0] == 'second'}
        baseline = self.registry.paths('first')['anomaly'] / 'baseline.npz'
        baseline.write_bytes(b'learned')
        with patch.object(manager, 'terminate') as terminate:
            result = manager.modify('first', {'label': 'New name'})
        self.assertEqual(terminate.call_count, 2)
        self.assertEqual(result['label'], 'New name')
        self.assertFalse(result['enabled'])
        self.assertEqual(self.registry.get('first')['url'], camera()['url'])
        self.assertEqual(baseline.read_bytes(), b'learned')
        self.assertEqual(manager.jobs, untouched)
        manager.modify('first', {'url': 'rtsp://new:password@new-host/live'})
        self.assertNotIn('password', json.dumps(manager.snapshot()))
        restored = CameraRegistry(self.directory, self.data, 'http://127.0.0.1:8082')
        self.assertEqual(restored.get('first')['url'], 'rtsp://new:password@new-host/live')

    def test_invalid_edits_do_not_stop_a_running_camera(self):
        self.registry.add(camera())
        manager, _ = self.manager()
        manager.reconcile()
        for changes in ({'id': 'other'}, {'url': 'file:///tmp/file'}, {'label': ''}, [], {'url': 'http://127.0.0.1:8084/stream.ts'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                manager.modify('first', changes)
        self.assertTrue(self.registry.get('first')['enabled'])
        self.assertEqual(len(manager.jobs), 2)

    def test_delete_removes_registry_and_analysis_entry_but_keeps_files(self):
        self.registry.add(camera())
        self.registry.add(camera('second'))
        manager, _ = self.manager()
        manager.reconcile()
        paths = self.registry.paths('first')
        saved = paths['recordings'] / 'event.mkv'
        saved.write_bytes(b'video')
        with patch.object(manager, 'terminate') as terminate:
            manager.modify('first')
        self.assertEqual(terminate.call_count, 2)
        self.assertEqual(saved.read_bytes(), b'video')
        self.assertTrue((paths['control'] / 'camera-stopped').exists())
        self.assertEqual([c['id'] for c in json.loads((self.directory / 'analysis.json').read_text())], ['second'])
        self.assertEqual([c['id'] for c in manager.snapshot()['cameras']], ['second'])
        self.assertEqual({key[0] for key in manager.jobs}, {'second'})
        with self.assertRaises(KeyError):
            self.registry.get('first')

    def test_edit_and_delete_persistence_failures_preserve_registration(self):
        self.registry.add(camera(enabled=False))
        for changes in ({'label': 'Changed'}, None):
            with patch.object(self.registry, 'persist', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    self.registry.modify('first', changes)
            self.assertEqual(self.registry.get('first')['label'], 'First')

    def test_stopping_one_camera_keeps_the_other_processes_and_data(self):
        self.registry.add(camera())
        self.registry.add(camera('second'))
        manager, spawned = self.manager()
        manager.reconcile()
        self.assertEqual(len(spawned), 4)
        untouched = {k: p for k, p in manager.jobs.items() if k[0] == 'second'}
        baseline = self.registry.paths('first')['anomaly'] / 'baseline.npz'
        baseline.write_bytes(b'normal baseline')
        with patch.object(manager, 'terminate') as terminate:
            manager.action('first', 'stop')
            self.assertEqual(terminate.call_count, 2)
        self.assertEqual({k: p for k, p in manager.jobs.items() if k[0] == 'second'}, untouched)
        self.assertEqual(baseline.read_bytes(), b'normal baseline')
        self.assertEqual(json.loads((baseline.parent / 'command.json').read_text())['action'], 'pause')
        self.assertTrue((self.registry.paths('first')['control'] / 'camera-stopped').exists())
        manager.action('first', 'start')
        self.assertEqual(len(spawned), 6)
        self.assertEqual(manager.snapshot()['manual_pauses'], {})
        self.assertNotIn('secret', json.dumps(manager.snapshot()))
        self.assertNotEqual(spawned[0][2]['env']['CAMERA_CONTROL_DIR'], spawned[2][2]['env']['CAMERA_CONTROL_DIR'])

    def test_a_failed_camera_restarts_without_restarting_another(self):
        self.registry.add(camera())
        self.registry.add(camera('second'))
        manager, spawned = self.manager()
        manager.reconcile()
        manager.jobs[('first','dashboard')].poll.return_value = 1
        manager.retry_at[('first','dashboard')] = 0
        with patch.object(manager, 'terminate'):
            manager.reconcile()
        self.assertEqual(len(spawned), 5)
        self.assertIn(('second','dashboard'), manager.jobs)
        self.assertEqual(manager.snapshot()['cameras'][1]['state'], 'running')

    def test_stale_port_file_cannot_route_to_an_unrelated_process(self):
        self.registry.add(camera())
        manager, _ = self.manager()
        manager.reconcile()
        path = self.registry.paths('first')['control'] / 'dashboard-port.json'
        path.write_text(json.dumps(dict(pid=1, port=8000)))
        self.assertIsNone(manager.dashboard_port('first'))
        process = manager.jobs[('first','dashboard')]
        path.write_text(json.dumps(dict(pid=process.pid, port=8000)))
        self.assertEqual(manager.dashboard_port('first'), 8000)


class HTTPTests(RegistryTests):
    def setUp(self):
        super().setUp()
        self.manager = CameraManager(self.registry)
        self.manager.reconcile = Mock()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.manager = self.manager
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self, path, body=None, headers=None):
        request = Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers or {})
        with urlopen(request, timeout=4) as response:
            return response.status, response.read(), response.headers

    def test_add_requires_control_header_and_public_list_never_contains_credentials(self):
        with self.assertRaises(HTTPError) as caught:self.request('/api/cameras', camera())
        self.assertEqual(caught.exception.code, 403)
        status, _, _ = self.request('/api/cameras', camera(enabled=False), {'X-Camera-Control':'1'})
        self.assertEqual(status, 201)
        status, data, _ = self.request('/api/cameras')
        self.assertNotIn(b'secret', data)
        self.assertEqual(json.loads(data)['cameras'][0]['state'], 'stopped')
        _, data, _ = self.request('/first/', headers={'X-Forwarded-Prefix':'/cameras/'})
        self.assertIn(b'<base href="/cameras/">', data)

    def test_proxy_preserves_range_requests_and_frame_tokens(self):
        self.registry.add(camera())
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                assert self.headers['Range'] == 'bytes=2-4'
                self.send_response(206)
                for key,value in [('Content-Length','3'),('Content-Range','bytes 2-4/10'),('X-Camera-Frame-Token','generation-1')]:self.send_header(key,value)
                self.end_headers();self.wfile.write(b'234')
        upstream = ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        thread = threading.Thread(target=upstream.serve_forever);thread.start()
        try:
            with patch.object(self.manager,'dashboard_port',return_value=upstream.server_port):
                status,data,headers=self.request('/view/first/api/play/event.mkv',headers={'Range':'bytes=2-4'})
            self.assertEqual(status,206);self.assertEqual(data,b'234')
            self.assertEqual(headers['Content-Range'],'bytes 2-4/10')
            self.assertEqual(headers['X-Camera-Frame-Token'],'generation-1')
        finally:upstream.shutdown();upstream.server_close();thread.join()

    def test_edit_and_delete_routes_are_guarded_and_keep_sources_private(self):
        self.registry.add(camera(enabled=False))
        for invalid in (b'null', b'[]'):
            request = Request(self.base + '/api/cameras/first/edit', data=invalid,
                              headers={'X-Camera-Control': '1'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=4)
            self.assertEqual(caught.exception.code, 400)
            self.assertEqual(self.registry.get('first')['label'], 'First')
        for action in ('edit', 'delete'):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/cameras/first/' + action, {})
            self.assertEqual(caught.exception.code, 403)
        status, data, _ = self.request('/api/cameras/first/edit', {'label': 'Renamed'}, {'X-Camera-Control': '1'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)['label'], 'Renamed')
        self.assertNotIn(b'secret', data)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/cameras/first/delete', {'unexpected': True}, {'X-Camera-Control': '1'})
        self.assertEqual(caught.exception.code, 400)
        self.request('/api/cameras/first/delete', {}, {'X-Camera-Control': '1'})
        self.assertEqual(self.registry.cameras, [])
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/cameras/first/delete', {}, {'X-Camera-Control': '1'})
        self.assertEqual(caught.exception.code, 404)

    def test_invalid_paths_cannot_escape_the_selected_camera(self):
        for path in ('/view/../api/cameras', '/view/unknown/api/status', '/api/cameras/first/stop/unexpected'):
            with self.subTest(path=path),self.assertRaises(HTTPError) as caught:self.request(path)
            self.assertEqual(caught.exception.code,404)


class StopConcurrencyTests(ManagerTests):
    def test_waiting_for_camera_shutdown_does_not_lock_another_camera(self):
        self.registry.add(camera())
        self.registry.add(camera('second'))
        manager,_=self.manager();manager.reconcile()
        entered,release=threading.Event(),threading.Event()
        def terminate(process):
            entered.set();release.wait(3)
        with patch.object(manager,'terminate',side_effect=terminate):
            thread=threading.Thread(target=manager.action,args=('first','stop'));thread.start()
            try:
                self.assertTrue(entered.wait(1))
                done=threading.Event()
                probe=threading.Thread(target=lambda:(manager.snapshot(),done.set()))
                probe.start()
                self.assertTrue(done.wait(.5),'Another camera must remain accessible during shutdown')
                probe.join()
            finally:release.set();thread.join()


class MigrationSafetyTests(RegistryTests):
    def test_learning_options_are_preserved_or_rejected_before_any_deletion(self):
        paths=self.registry.paths('pi')
        folder=paths['anomaly']/'learning';folder.mkdir(parents=True)
        catalog={'sessions':[{'id':'normal'}],'configuration':{'jpeg_draft':True,'score_top_k':5}}
        (folder/'catalog.json').write_text(json.dumps(catalog))
        baseline=paths['anomaly']/'baseline.npz';baseline.write_bytes(b'learned baseline')
        entry=dict(id='pi',label='Pi',url='http://127.0.0.1:8084/stream.ts',enabled=False)
        self.registry.path.write_text(json.dumps({'cameras':[entry]}))
        with self.assertRaisesRegex(ValueError,'original JPEG draft'):
            CameraRegistry(self.directory,self.data,'http://127.0.0.1:8082')
        self.assertEqual(baseline.read_bytes(),b'learned baseline')
        self.assertEqual(json.loads((folder/'catalog.json').read_text()),catalog)
        entry['settings']={'model':{'jpeg_draft':1,'top_k':5,'threads':2}}
        self.registry.path.write_text(json.dumps({'cameras':[entry]}))
        registry=CameraRegistry(self.directory,self.data,'http://127.0.0.1:8082')
        published=json.loads((self.directory/'analysis.json').read_text())
        self.assertEqual(published[0]['settings'],entry['settings'])
        self.assertEqual(baseline.read_bytes(),b'learned baseline')

    def test_analysis_file_failure_retries_without_losing_an_added_camera(self):
        with patch.object(self.registry,'publish_analysis',side_effect=OSError('disk busy')):
            with self.assertLogs('camera-manager',level='WARNING'):
                self.registry.add(camera(enabled=False))
        self.assertTrue(self.registry.analysis_dirty)
        self.assertEqual(len(json.loads(self.registry.path.read_text())['cameras']),1)
        CameraManager(self.registry).reconcile()
        self.assertFalse(self.registry.analysis_dirty)
        self.assertEqual(len(json.loads((self.directory/'analysis.json').read_text())),1)
