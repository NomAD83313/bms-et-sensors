import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from tests.test_svcctl_mscl_guard import _load_module

SNAPSHOT = {'success': True, 'manual_pauses': {}, 'cameras': [dict(
    id='pi', target='cam-pi', label='Pi Camera', path='/cameras/pi/',
    services={'camera-media': 'running', 'camera-analysis': 'running'})]}


class CameraApplicationTests(unittest.TestCase):
    """The `cameras` target controls the application; cam-<id> targets one camera inside it."""

    def test_the_application_group_starts_dependencies_first_and_stops_them_last(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(mod, 'MANUAL_PAUSE_FILE', Path(directory) / 'pauses.json'), \
                patch.object(mod, '_shared_cameras', return_value=None), \
                patch.object(mod, '_set_container_running', return_value='done') as action:
            client = mod.app.test_client()
            self.assertEqual(client.post('/services/cameras/start').status_code, 200)
            self.assertEqual(action.call_args_list, [call('camera-media', True), call('camera-analysis', True)])
            action.reset_mock()
            response = client.post('/services/cameras/stop')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(action.call_args_list, [call('camera-analysis', False), call('camera-media', False)])
            self.assertIn('cameras', response.get_json()['manual_pauses'])
            self.assertEqual(client.post('/services/cameras/delete').status_code, 400)
            self.assertEqual(client.post('/services/unknown/start').status_code, 404)

    def test_the_optional_relay_is_part_of_the_group_and_starts_first(self):
        mod = _load_module()
        with patch.dict(mod.os.environ, {'CAMERA_HUB_ENABLED': '1'}):
            self.assertEqual(mod._camera_group_targets()['cameras'], ['camera-hub', 'camera-media', 'camera-analysis'])
        with patch.dict(mod.os.environ, {'CAMERA_HUB_ENABLED': ''}):
            self.assertEqual(mod._camera_group_targets()['cameras'], ['camera-media', 'camera-analysis'])
        self.assertEqual(mod._camera_group_targets()['redlab'], ['redlab-collector'])

    def test_the_media_url_defaults_to_the_local_camera_media_port(self):
        self.assertEqual(_load_module().CAMERA_MEDIA_CONTROL_URL, 'http://127.0.0.1:8082')


class SingleCameraTests(unittest.TestCase):
    def test_a_camera_is_controlled_through_camera_media_without_docker_operations(self):
        mod = _load_module()
        snapshot = {**SNAPSHOT, 'manual_pauses': {}}

        def upstream(path, body=None):
            return snapshot if body is None else {'id': 'pi', 'enabled': False}

        with patch.object(mod, '_shared_camera_request', side_effect=upstream) as request, \
                patch.object(mod, '_set_container_running') as docker_action:
            client = mod.app.test_client()
            self.assertEqual(client.get('/cameras').get_json(),
                             dict(snapshot, control_targets=[{'target': 'cameras', 'label': 'Cameras'}]))
            snapshot['manual_pauses'] = {'cam-pi': True}
            services = client.get('/services').get_json()
            self.assertTrue(services['manual_pauses']['cam-pi'])
            self.assertEqual(services['services']['cam-pi'], SNAPSHOT['cameras'][0]['services'])
            self.assertEqual(client.post('/services/cam-pi/stop').status_code, 200)
            self.assertIn(call('/api/cameras/pi/stop', {}), request.call_args_list)
            docker_action.assert_not_called()
            self.assertEqual(client.post('/services/cam-nope/stop').status_code, 404)
            self.assertEqual(client.post('/services/cam-pi/delete').status_code, 400)

    def test_cameras_stay_listable_and_controllable_while_camera_media_is_down(self):
        mod = _load_module()
        with patch.object(mod, '_shared_cameras', return_value=None):
            client = mod.app.test_client()
            listing = client.get('/cameras')
            self.assertEqual(listing.status_code, 200)
            self.assertFalse(listing.get_json()['success'])
            self.assertEqual(listing.get_json()['control_targets'], [{'target': 'cameras', 'label': 'Cameras'}])
            self.assertEqual(client.post('/services/cam-pi/start').status_code, 503)


if __name__ == '__main__':
    unittest.main()
