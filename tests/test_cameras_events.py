import json
from pathlib import Path
import tempfile
import unittest

from app.cameras.event_recording import EventBuffer, analysis_available, segment_command


def normal(now):
    return dict(state='normal', updated=now)


def join(paths, target):
    target.write_bytes(b''.join(p.read_bytes() for p in paths))


def segment(manager, start, end):
    path = manager.buffer / f'seg-{start}.mkv'
    path.write_bytes(str(start).encode() + b',')
    manager.add_segment(path, start, end)
    return path


class EventVideoTests(unittest.TestCase):
    def test_normal_video_expires_without_permanent_recordings(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=10, post=20, join=join)
            old = segment(manager, 100, 105)
            recent = segment(manager, 155, 160)
            manager.update(160, normal(160), {})
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertEqual(list(Path(directory).glob('*.mkv')), [])

    def test_pre_post_window_is_merged_and_survives_worker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=10, post=20, join=join)
            for stamp in range(100, 130, 5):
                segment(manager, stamp, stamp + 5)
            trigger = dict(id='first', start=120, last=120)
            manager.update(125, normal(125), trigger)
            self.assertEqual(manager.active['start'], 110)
            self.assertEqual(manager.active['end'], 140)
            self.assertEqual(list(Path(directory).glob('event-*.mkv')), [])
            manager = EventBuffer(directory, pre=10, post=20, join=join)
            for stamp in range(130, 160, 5):
                segment(manager, stamp, stamp + 5)
            manager.update(135, normal(135), dict(id='second', start=132, last=135))
            self.assertEqual(manager.active['end'], 155)
            manager.update(160, normal(160), {})
            videos = list(Path(directory).glob('event-*.mkv'))
            self.assertEqual(len(videos), 1)
            metadata = json.loads(videos[0].with_suffix('.json').read_text())
            self.assertEqual(metadata['actual_start'], 110)
            self.assertEqual(metadata['actual_end'], 155)
            self.assertEqual(metadata['triggers'], ['first', 'second'])
            self.assertEqual(videos[0].read_bytes(), b'110,115,120,125,130,135,140,145,150,')
            # An expired marker cannot recreate a completed event.
            manager.update(161, normal(161), trigger)
            self.assertIsNone(manager.active)

    def test_unavailable_analysis_does_not_save_unchecked_video(self):
        states = [dict(state='normal', updated=75)]
        states += [dict(state=state, updated=106) for state in
                   ('paused', 'learning', 'loading', 'unavailable')]
        states.append({})
        for status in states:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                manager = EventBuffer(directory, join=join)
                path = segment(manager, 100, 105)
                manager.update(106, status, {})
                self.assertTrue(path.exists())
                manager.update(200, normal(200), {})
                self.assertFalse(path.exists())
                self.assertEqual(list(Path(directory).glob('*.mkv')), [])
                self.assertEqual(list(Path(directory).glob('*.json')), [])

    def test_old_unknown_ranges_do_not_resume_saving_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, join=join)
            segment(manager, 100, 105)
            manager.persist()
            path = manager.buffer / 'catalog.json'
            catalog = json.loads(path.read_text())
            catalog.update(unknown_start=100, unknown=[dict(start=90, end=106)])
            path.write_text(json.dumps(catalog))
            historical = Path(directory) / 'unclassified-old.mkv'
            historical.write_bytes(b'previously saved')
            manager = EventBuffer(directory, join=join)
            manager.update(106, normal(106), {})
            manager.update(200, normal(200), {})
            self.assertEqual(list(Path(directory).glob('*.mkv')), [historical])
            self.assertEqual(list(manager.buffer.glob('seg-*.mkv')), [])

    def test_confirmed_event_keeps_post_window_when_analysis_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=10, post=20, join=join)
            for stamp in range(100, 130, 5):
                segment(manager, stamp, stamp + 5)
            manager.update(125, normal(125), dict(id='confirmed', start=120, last=120))
            for stamp in range(130, 140, 5):
                segment(manager, stamp, stamp + 5)
            manager.update(140, dict(state='paused', updated=140), {})
            videos = list(Path(directory).glob('*.mkv'))
            self.assertEqual(len(videos), 1)
            self.assertTrue(videos[0].name.startswith('event-'))
            metadata = json.loads(videos[0].with_suffix('.json').read_text())
            self.assertEqual((metadata['start'], metadata['end']), (110, 140))

    def test_continuing_anomaly_splits_with_unique_video_names(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=0, post=60, join=join)
            for stamp in range(100, 410, 5):
                segment(manager, stamp, stamp + 5)
            manager.update(100, normal(100), dict(id='long', start=100, last=100))
            for stamp in range(150, 401, 50):
                manager.update(stamp, normal(stamp), dict(id='long', start=100, last=stamp))
            first = list(Path(directory).glob('event-*.mkv'))[0]
            manager.update(402, normal(402), dict(id='long', start=100, last=402))
            self.assertNotEqual(manager.active['name'] + '.mkv', first.name)
            self.assertEqual(manager.active['start'], 400)

    def test_manual_stop_finalizes_available_video(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=10, post=60, join=join)
            segment(manager, 100, 105)
            manager.update(106, normal(106), dict(id='stop', start=105, last=105))
            manager.finish_on_stop(107)
            self.assertIsNone(manager.active)
            self.assertEqual(len(list(Path(directory).glob('event-*.mkv'))), 1)

    def test_empty_event_cannot_block_future_events_after_camera_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = EventBuffer(directory, pre=10, post=20, join=join)
            manager.update(100, normal(100), dict(id='no-video', start=100, last=100))
            self.assertIsNotNone(manager.active)
            manager.update(151, normal(151), {})
            self.assertIsNone(manager.active)
            segment(manager, 150, 155)
            manager.update(156, normal(156), dict(id='recovered', start=155, last=155))
            self.assertIsNotNone(manager.active)
            self.assertEqual(manager.active['triggers'], ['recovered'])

    def test_readiness_and_command(self):
        self.assertTrue(analysis_available(normal(100), 101))
        self.assertFalse(analysis_available(normal(100), 121))
        self.assertFalse(analysis_available(dict(state='paused', updated=100), 101))
        args = segment_command('rtsp://camera/stream', Path('/buffer'))
        self.assertEqual(args[args.index('-c:v') + 1], 'copy')
        self.assertIn('-reset_timestamps', args)
        self.assertIn('-segment_list', args)
