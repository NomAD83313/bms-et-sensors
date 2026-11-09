"""Capture validation, JPEG event windows and deferred video encoding."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.cameras.capture_settings import validate, validate_update
from app.cameras.snapshot_recording import SnapshotBuffer, encode
from app.cameras import pi_source


class CaptureSettingsTests(unittest.TestCase):
    def test_high_fps_profiles_are_rejected(self):
        with self.assertRaises(ValueError):
            validate(dict(fps=42))
        for width, height in ((640, 480), (1280, 720), (1920, 1080), (1640, 1232)):
            with self.subTest(size=(width, height)), self.assertRaises(ValueError):
                validate_update(validate({}), dict(width=width, height=height, fps=30))

    def test_snapshot_exposure_fits_frame_period(self):
        for shutter in (200, 500, 40000):
            settings = validate(dict(capture_mode='snapshots', snapshot_interval_ms=200, shutter_us=shutter))
            self.assertEqual(settings['shutter_us'], shutter)
        for data in [dict(capture_mode='video', fps=30, shutter_us=40000),
                     *(dict(capture_mode='snapshots', snapshot_interval_ms=1000, shutter_us=shutter)
                       for shutter in (60000, 100000, 200000, 500000, 1000000)),
                     dict(shutter_us=-1),
                     dict(snapshot_interval_ms=0), dict(shutter_us=True), dict(fps=float('nan'))]:
            with self.subTest(data=data), self.assertRaises(ValueError): validate(data)

    def test_video_update_validates_resolution_fps_and_numeric_types(self):
        current = validate({})
        for width, height, fps in ((1280, 720, 10), (1920, 1080, 15),
                                   (1640, 1232, 15), (3280, 2464, 15)):
            update = dict(capture_mode='video', width=width, height=height, fps=fps, shutter_us=0)
            self.assertEqual(validate_update(current, update)['fps'], fps)
        for update in (dict(width=3280, height=2464, fps=30), dict(width=1920, height=1080, fps=42),
                       dict(width=1000, height=720), dict(fps=True), dict(fps='30'),
                       dict(fps=float('inf')), dict(bitrate=1000000), []):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_update(current, update)

    def test_snapshot_command_has_no_h264_encoder_and_sets_shutter(self):
        with tempfile.TemporaryDirectory() as directory:
            persistent = Path(directory) / 'capture.json'
            override = Path(directory) / 'override.json'
            persistent.write_text(json.dumps(dict(capture_mode='snapshots', width=1920, height=1080, snapshot_interval_ms=200, shutter_us=10000)))
            with patch.object(pi_source, 'SETTINGS', persistent), patch.dict(os.environ, {'PI_CAMERA_MODE_FILE': str(override)}):
                args = pi_source.command()
            self.assertEqual(args[args.index('--libav-video-codec')+1], 'rawvideo')
            self.assertEqual(args[args.index('--framerate')+1], '5.0')
            self.assertEqual(args[args.index('--shutter')+1], '10000')
            self.assertNotIn('libx264', args)
            self.assertEqual(args[args.index('--width')+1], '1438')
            self.assertEqual(args[args.index('--height')+1], '1080')


class SnapshotWindowTests(unittest.TestCase):
    def test_pre_post_frames_are_protected_and_event_is_extended(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer = SnapshotBuffer(directory, validate({}), pre=2, post=2)
            for stamp in range(100, 106): buffer.add(b'jpeg', stamp)
            buffer.trigger(dict(id='first', start=103, last=103), 103)
            self.assertEqual(buffer.active['start'], 101)
            buffer.trigger(dict(id='second', start=104, last=105), 105)
            self.assertEqual(buffer.active['end'], 107)
            for stamp in (106,107): buffer.add(b'jpeg', stamp)
            self.assertIsNone(buffer.close(106))
            raw, target = buffer.close(107)
            event = json.loads((raw/'event.json').read_text())
            self.assertEqual(event['triggers'], ['first','second'])
            self.assertEqual([frame['time'] for frame in event['frames']], list(range(101,108)))
            self.assertTrue(all((raw / frame['name']).exists() for frame in event['frames']))
            self.assertFalse(target.exists(), 'Video is encoded after finalizing the window')
            buffer.trigger(dict(id='expired', start=103, last=103), 108)
            self.assertIsNone(buffer.active)

    def test_raw_protected_frames_survive_ring_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            buffer = SnapshotBuffer(directory, validate({}), pre=2, post=2)
            buffer.add(b'first',100)
            buffer.trigger(dict(id='event',start=100,last=100),100)
            buffer.add(b'next',200)
            self.assertFalse((buffer.buffer/'frame-100000000000.jpg').exists())
            raw, _ = buffer.close(200)
            self.assertEqual((raw/'frame-100000000000.jpg').read_bytes(), b'first')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Requires FFmpeg')
    def test_snapshots_encode_to_h264_with_playable_frame_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = validate(dict(capture_mode='snapshots', snapshot_interval_ms=200))
            buffer = SnapshotBuffer(directory, settings, pre=0, post=1)
            for index,color in enumerate(('red','green','blue','white','black','yellow')):
                image = subprocess.check_output(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=64x48',
                    '-frames:v','1','-c:v','mjpeg','-threads','1','-f','image2pipe','pipe:1'],timeout=10)
                buffer.add(image,100+index*.2)
                if index==0: buffer.trigger(dict(id='test',start=100,last=100),100)
            raw,target=buffer.close(101)
            encode(raw,target)
            result=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                'stream=codec_name,width,height,avg_frame_rate:frame=pts_time','-show_frames','-of','json',str(target)],text=True,timeout=10))
            stream=result['streams'][0]
            self.assertEqual((stream['codec_name'],stream['width'],stream['height']),('h264',64,48))
            self.assertEqual(stream['avg_frame_rate'],'5/1')
            timestamps=[float(frame['pts_time']) for frame in result['frames']]
            self.assertTrue(all(abs(b-a-.2)<.001 for a,b in zip(timestamps,timestamps[1:])))
            self.assertTrue(target.with_suffix('.json').exists())


class HostCaptureAPITests(unittest.TestCase):
    def test_settings_persist_and_invalid_exposure_does_not_replace_them(self):
        import threading
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as directory:
            settings=Path(directory)/'capture.json'
            with patch.object(pi_source,'SETTINGS',settings), patch.dict(os.environ,{'PI_CAMERA_MODE_FILE':str(Path(directory)/'override.json')}):
                server=ThreadingHTTPServer(('127.0.0.1',0),pi_source.Handler)
                server.broadcast=pi_source.Broadcast()
                worker=threading.Thread(target=server.serve_forever,daemon=True)
                worker.start()
                try:
                    url=f'http://127.0.0.1:{server.server_port}/api/config'
                    body={'capture_mode':'snapshots','width':1920,'height':1080,'snapshot_interval_ms':200,'shutter_us':10000}
                    with urlopen(Request(url,data=json.dumps(body).encode(),headers={'X-Camera-Control':'1'}),timeout=5) as response:
                        applied=json.load(response)
                    self.assertEqual(applied['shutter_us'],10000)
                    self.assertEqual((applied['width'],applied['height']),(1920,1080))
                    self.assertEqual(json.loads(settings.read_text()),applied)
                    self.assertTrue(server.broadcast.reload.is_set())
                    body = dict(capture_mode='video', width=1920, height=1080, fps=15, shutter_us=0)
                    with urlopen(Request(url,data=json.dumps(body).encode(),headers={'X-Camera-Control':'1'}),timeout=5) as response:
                        applied=json.load(response)
                    self.assertEqual((applied['width'], applied['height'], applied['fps']), (1920,1080,15))
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url,data=b'{"width":3280,"height":2464,"fps":30}',headers={'X-Camera-Control':'1'}),timeout=5)
                    self.assertEqual(error.exception.code,400)
                    self.assertEqual(json.loads(settings.read_text()),applied)
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url,data=b'{"shutter_us":1000000}',headers={'X-Camera-Control':'1'}),timeout=5)
                    self.assertEqual(error.exception.code,400)
                    self.assertEqual(json.loads(settings.read_text()),applied)
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(url,data=b'{}'),timeout=5)
                    self.assertEqual(error.exception.code,403)
                finally:
                    server.shutdown();server.server_close();worker.join(timeout=3)


class SnapshotExportFailureTests(unittest.TestCase):
    def test_empty_event_after_power_loss_is_archived_without_blocking_recording(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.cameras import snapshot_recording
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / '.snapshot-buffer' / ('event-20261106T000000Z-' + 'b' * 32)
            raw.mkdir(parents=True)
            (raw / 'event.json').write_text(json.dumps(dict(frames=[
                dict(name='frame-1.jpg', time=1), dict(name='frame-2.jpg', time=2)])))
            (raw / 'frame-1.jpg').touch()
            stop = threading.Event()
            states = []
            def heartbeat(state, **details):
                states.append(state)
                stop.set()
            control = SimpleNamespace(enabled=lambda: False, heartbeat=heartbeat)
            settings = validate(dict(capture_mode='snapshots'))
            executor = Mock()
            with patch.object(snapshot_recording, 'ThreadPoolExecutor', return_value=executor), \
                    patch.object(snapshot_recording.source, 'capture_settings', return_value=settings), \
                    self.assertLogs('camera-snapshots', level='WARNING'):
                snapshot_recording.run(root, stop, 1000000, 3600, Mock(), control,
                                       root / 'anomaly', 'anomaly', 60)
            self.assertEqual(states, ['paused'])
            executor.submit.assert_not_called()
            self.assertFalse(raw.exists())
            saved = list((root / '.snapshot-recovery').iterdir())
            self.assertEqual(len(saved), 1)
            self.assertTrue((saved[0] / 'event.json').exists())
            self.assertEqual((saved[0] / 'frame-1.jpg').stat().st_size, 0)
            self.assertEqual(json.loads((saved[0] / 'recovery-error.json').read_text())['frames'], 2)

    def test_failed_export_retains_raw_frames_and_stops_new_event_collection(self):
        from concurrent.futures import Future
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from app.cameras import snapshot_recording
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            raw=root/'.snapshot-buffer'/('event-20261106T000000Z-'+'a'*32)
            raw.mkdir(parents=True)
            (raw/'event.json').write_text(json.dumps(dict(frames=[dict(name='frame-1.jpg',time=1)])))
            (raw/'frame-1.jpg').write_bytes(b'protected')
            failed=Future();failed.set_exception(ValueError('bad frame'))
            executor=Mock();executor.submit.return_value=failed
            stop=threading.Event();states=[]
            def heartbeat(state,**details):
                states.append(state)
                if state=='export_failed':stop.set()
            control=SimpleNamespace(enabled=lambda:True,heartbeat=heartbeat)
            settings=validate(dict(capture_mode='snapshots'))
            with patch.object(snapshot_recording,'ThreadPoolExecutor',return_value=executor), patch.object(snapshot_recording.source,'capture_settings',return_value=settings), patch.object(snapshot_recording.source,'snapshot') as snapshot, self.assertLogs('camera-snapshots',level='WARNING'):
                snapshot_recording.run(root,stop,1000000,3600,Mock(),control,root/'anomaly','anomaly',60)
            self.assertIn('export_failed',states)
            self.assertEqual((raw/'frame-1.jpg').read_bytes(),b'protected')
            snapshot.assert_not_called()
