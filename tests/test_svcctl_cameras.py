import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_svcctl_mscl_guard import _load_module

EXTRA = [dict(id='garage', label='Garage', port='8085', names=['cam-garage-recorder', 'cam-garage-dashboard'])]


class CameraGroupTests(unittest.TestCase):
    def test_group_actions_start_the_shared_worker_but_never_stop_it(self):
        mod = _load_module()
        client = mod.app.test_client()
        with tempfile.TemporaryDirectory() as directory, patch.object(mod, 'MANUAL_PAUSE_FILE', Path(directory) / 'pauses.json'), \
                patch.object(mod, '_extra_cameras', return_value=[]):
            for target, prefix in (('cameras', 'camera'), ('pi-camera', 'pi-camera')):
                recorder, dashboard = prefix + '-recorder', prefix + '-dashboard'
                for action, expected in [
                    ('stop', [(dashboard, False), (recorder, False)]),
                    ('start', [(recorder, True), (dashboard, True), ('camera-analysis', True)]),
                    ('restart', [(dashboard, False), (recorder, False),
                                 (recorder, True), (dashboard, True), ('camera-analysis', True)]),
                ]:
                    calls = []
                    with self.subTest(target=target, action=action), patch.object(
                            mod, '_set_container_running', side_effect=lambda name, run: calls.append((name, run)) or 'done'):
                        response = client.post('/services/' + target + '/' + action)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(calls, expected)
                    self.assertNotIn('camera-analysis', [name for name, run in calls if not run],
                                     'The shared worker keeps serving the other cameras')
                    self.assertEqual(target in response.get_json()['manual_pauses'], action == 'stop')
            self.assertEqual(client.post('/services/cameras/delete').status_code, 400)
            self.assertEqual(client.post('/services/unknown/start').status_code, 404)

    def test_the_status_of_a_camera_group_includes_the_shared_worker(self):
        mod = _load_module()
        with patch.object(mod, '_extra_cameras', return_value=EXTRA):
            targets = mod._effective_target_map()
        self.assertEqual(targets['cameras'], ['camera-recorder', 'camera-dashboard', 'camera-analysis'])
        self.assertEqual(targets['pi-camera'], ['pi-camera-recorder', 'pi-camera-dashboard', 'camera-analysis'])
        self.assertEqual(targets['cam-garage'], ['cam-garage-recorder', 'cam-garage-dashboard', 'camera-analysis'])
        self.assertEqual(targets['redlab'], ['redlab-collector'])
        self.assertEqual(sorted(mod.TARGET_MAP['cameras']), ['camera-dashboard', 'camera-recorder'])


class ExtraCameraTests(unittest.TestCase):
    def test_extra_cameras_are_targets_like_the_original_ones(self):
        mod = _load_module()
        client = mod.app.test_client()
        with tempfile.TemporaryDirectory() as directory, patch.object(mod, 'MANUAL_PAUSE_FILE', Path(directory) / 'pauses.json'), \
                patch.object(mod, '_extra_cameras', return_value=EXTRA):
            recorder, dashboard = 'cam-garage-recorder', 'cam-garage-dashboard'
            for action, expected in [
                ('stop', [(dashboard, False), (recorder, False)]),
                ('start', [(recorder, True), (dashboard, True), ('camera-analysis', True)]),
                ('restart', [(dashboard, False), (recorder, False), (recorder, True), (dashboard, True), ('camera-analysis', True)]),
            ]:
                calls = []
                with self.subTest(action=action), patch.object(
                        mod, '_set_container_running', side_effect=lambda name, run: calls.append((name, run)) or 'done'):
                    response = client.post('/services/cam-garage/' + action)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(calls, expected)
            self.assertEqual(client.post('/services/cam-nothing/start').status_code, 404)

    def test_the_cameras_endpoint_lists_every_camera_with_its_route_and_services(self):
        mod = _load_module()
        statuses = {'cameras': {'camera-recorder': 'running'}, 'pi-camera': {'pi-camera-recorder': 'running'},
                    'cam-garage': {'cam-garage-recorder': 'running', 'camera-analysis': 'running'}}
        with patch.object(mod, '_extra_cameras', return_value=EXTRA), patch.object(mod, 'status_payload', return_value=statuses):
            payload = mod.app.test_client().get('/cameras').get_json()
        self.assertNotIn('camera_analysis_mode', payload)
        self.assertEqual([(c['id'], c['target'], c['path']) for c in payload['cameras']], [
            ('wifi', 'cameras', '/cameras/'), ('pi', 'pi-camera', '/cameras/pi/'),
            ('garage', 'cam-garage', '/cameras/garage/')])
        self.assertEqual(payload['cameras'][2]['status_url'], '/cameras/garage/api/status')
        self.assertEqual(payload['cameras'][2]['services']['camera-analysis'], 'running')
        self.assertEqual(payload['cameras'][2]['label'], 'Garage')

    def test_extra_targets_survive_docker_errors(self):
        mod = _load_module()
        with patch.object(mod, 'discover_extra_cameras', side_effect=RuntimeError('docker down')):
            self.assertEqual(mod._extra_cameras(), [])
            self.assertEqual(sorted(mod._all_targets()), sorted(mod.TARGET_MAP))
        with patch.object(mod, '_extra_cameras', return_value=EXTRA):
            self.assertEqual(mod._all_targets()['cam-garage'], ['cam-garage-recorder', 'cam-garage-dashboard'])


if __name__ == '__main__':
    unittest.main()


class SharedCameraTests(unittest.TestCase):
    def test_shared_application_includes_optional_hub_first(self):
        mod = _load_module()
        with patch.object(mod, 'CAMERA_MEDIA_CONTROL_URL', 'http://127.0.0.1:8082'), \
                patch.object(mod, '_shared_cameras', return_value=None), \
                patch.dict(mod.os.environ, {'CAMERA_HUB_ENABLED': '1'}):
            self.assertEqual(mod._all_targets()['cameras'], ['camera-hub', 'camera-media', 'camera-analysis'])

    def test_shared_controls_route_to_one_camera_without_docker_operations(self):
        mod = _load_module()
        snapshot = {'success':True,'cameras':[dict(id='pi',target='cam-pi',label='Pi Camera',
                     path='/cameras/pi/',services={'camera-media':'running','camera-analysis':'running'})],
                    'manual_pauses':{}}
        def upstream(path,body=None):
            return snapshot if body is None else {'id':'pi','enabled':False}
        with patch.object(mod,'CAMERA_MEDIA_CONTROL_URL','http://127.0.0.1:8082'), \
                patch.object(mod,'_shared_camera_request',side_effect=upstream) as request, \
                patch.object(mod,'_set_container_running') as docker_action:
            client = mod.app.test_client()
            self.assertEqual(client.get('/cameras').get_json(),
                             dict(snapshot, control_targets=[{'target':'cameras','label':'Cameras'}]))
            snapshot['manual_pauses']={'cam-pi':True}
            self.assertTrue(client.get('/services').get_json()['manual_pauses']['cam-pi'])
            self.assertEqual(client.post('/services/cam-pi/stop').status_code,200)
            self.assertIn((('/api/cameras/pi/stop',{}),),[(c.args,) for c in request.call_args_list])
            docker_action.assert_not_called()
            self.assertEqual(client.post('/services/cam-nope/stop').status_code,404)

    def test_shared_application_can_restart_even_when_manager_is_stopped(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(mod, 'MANUAL_PAUSE_FILE', Path(directory) / 'pauses.json'), \
                patch.object(mod, 'CAMERA_MEDIA_CONTROL_URL', 'http://127.0.0.1:8082'), \
                patch.object(mod, '_shared_cameras', return_value=None), \
                patch.object(mod, '_set_container_running', return_value='done') as action:
            client = mod.app.test_client()
            listing = client.get('/cameras')
            self.assertEqual(listing.status_code, 200)
            self.assertFalse(listing.get_json()['success'])
            self.assertEqual(listing.get_json()['control_targets'], [{'target':'cameras','label':'Cameras'}])
            self.assertEqual(client.post('/services/cameras/start').status_code, 200)
            self.assertEqual(action.call_args_list, [
                unittest.mock.call('camera-media', True), unittest.mock.call('camera-analysis', True)])
            action.reset_mock()
            self.assertEqual(client.post('/services/cameras/stop').status_code, 200)
            self.assertEqual(action.call_args_list, [
                unittest.mock.call('camera-analysis', False), unittest.mock.call('camera-media', False)])
