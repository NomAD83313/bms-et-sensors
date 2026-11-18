import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.cameras import anomaly_control, dashboard, history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.recordings = self.root / 'recordings'
        self.recordings.mkdir()
        self.anomaly = self.root / 'anomaly'
        (self.anomaly / 'events').mkdir(parents=True)
        (self.anomaly / 'baseline.npz').write_bytes(b'model')
        (self.anomaly / 'session.json').write_text('{}')
        (self.recordings / '.buffer').mkdir()
        (self.recordings / '.buffer/live.mkv').write_bytes(b'active')

    def event(self, name='test-event'):
        for suffix in ('.jpg', '-heat.jpg'):
            (self.anomaly / 'events' / (name + suffix)).write_bytes(b'image')
        (self.anomaly / 'events' / (name + '.json')).write_text(json.dumps(dict(id=name, timestamp=time.time())))

    def recording(self, name='event-test.mkv'):
        (self.recordings / name).write_bytes(b'video')
        (self.recordings / name).with_suffix('.json').write_text('{}')

    def test_individual_deletions_keep_the_other_media_type(self):
        self.event()
        self.recording()
        result = history.delete_saved(self.recordings, self.anomaly, 'event', 'test-event')
        self.assertEqual(result, dict(events=1, recordings=0))
        self.assertEqual(list((self.anomaly / 'events').iterdir()), [])
        self.assertTrue((self.recordings / 'event-test.mkv').exists())
        self.event()
        result = history.delete_saved(self.recordings, self.anomaly, 'recording', 'event-test.mkv')
        self.assertEqual(result, dict(events=0, recordings=1))
        self.assertFalse((self.recordings / 'event-test.json').exists())
        self.assertTrue((self.anomaly / 'events/test-event.json').exists())

    def test_clear_history_keeps_model_settings_and_live_buffer(self):
        self.event()
        self.recording()
        self.recording('old-continuous.mkv')
        result = history.delete_saved(self.recordings, self.anomaly, 'history')
        self.assertEqual(result, dict(events=1, recordings=2))
        self.assertTrue((self.anomaly / 'baseline.npz').exists())
        self.assertTrue((self.anomaly / 'session.json').exists())
        self.assertEqual((self.recordings / '.buffer/live.mkv').read_bytes(), b'active')
        self.assertEqual(list(self.recordings.glob('*.mkv')), [])

    def test_paths_and_symlinks_cannot_delete_other_files(self):
        outside = self.root / 'secret.mkv'
        outside.write_bytes(b'private')
        (self.recordings / 'linked.mkv').symlink_to(outside)
        for action, name in [('recording', '../secret.mkv'), ('recording', '.buffer/live.mkv'), ('recording', 'linked.mkv'), ('event', '../baseline'), ('event', '/etc/passwd')]:
            with self.subTest(action=action, name=name), self.assertRaises(ValueError):
                history.delete_saved(self.recordings, self.anomaly, action, name)
        history.delete_saved(self.recordings, self.anomaly, 'history')
        self.assertEqual(outside.read_bytes(), b'private')
        self.assertTrue((self.recordings / 'linked.mkv').is_symlink())

    def test_bulk_delete_does_not_remove_newly_completed_video(self):
        self.recording()
        original = history.delete_recording
        def finish_another_video(root, name):
            self.recording('completed-later.mkv')
            return original(root, name)
        with patch.object(history, 'delete_recording', side_effect=finish_another_video):
            result = history.delete_saved(self.recordings, self.anomaly, 'all-recordings')
        self.assertEqual(result['recordings'], 1)
        self.assertTrue((self.recordings / 'completed-later.mkv').exists())

    def test_saved_event_count_tracks_deletions(self):
        self.event()
        anomaly_control.write_json(self.anomaly / 'status.json', dict(state='normal', updated=time.time(), event_count=42))
        with patch.object(anomaly_control, 'DATA', self.anomaly):
            self.assertEqual(anomaly_control.status()['event_count'], 1)
            history.delete_saved(self.recordings, self.anomaly, 'all-events')
            self.assertEqual(anomaly_control.status()['event_count'], 0)

    def test_http_delete_requires_control_header_and_valid_name(self):
        self.recording()
        with patch.object(dashboard, 'RECORDINGS', self.recordings), patch.object(anomaly_control, 'DATA', self.anomaly):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/api/delete/recording'
                for headers, name, expected in [({}, 'event-test.mkv', 403), ({'X-Camera-Control': '1'}, '../secret.mkv', 400)]:
                    request = urllib.request.Request(url, method='POST', headers=headers, data=json.dumps(dict(name=name)).encode())
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=3)
                    self.assertEqual(error.exception.code, expected)
                request = urllib.request.Request(url, method='POST', headers={'X-Camera-Control': '1'}, data=b'{"name":"event-test.mkv"}')
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(json.load(response)['recordings'], 1)
                self.assertFalse((self.recordings / 'event-test.mkv').exists())
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
