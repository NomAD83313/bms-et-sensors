"""Bounded video buffer and merged pre/post-anomaly recordings."""
import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

try:
    from . import source
    from .anomaly_control import analysis_available, read_json, valid_event_id, write_json
    from .source import input_args
except ImportError:
    import source
    from anomaly_control import analysis_available, read_json, valid_event_id, write_json
    from source import input_args

LOG = logging.getLogger('camera-events')
SEGMENT_SECONDS = 5


def overlaps(first, second):
    return first['start'] < second['end'] and first['end'] > second['start']


def link_or_copy(source, target):
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def concatenate(paths, target):
    manifest = target.with_suffix('.concat')
    # Paths are generated internally and contain no quotes or user input.
    manifest.write_text(''.join("file '" + str(path.resolve()) + "'\n" for path in paths))
    partial = target.with_suffix('.partial')
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'quiet',
                        '-f', 'concat', '-safe', '0', '-i', str(manifest), '-map', '0:v:0',
                        '-an', '-c:v', 'copy', '-f', 'matroska', '-y', str(partial)],
                       check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not partial.stat().st_size:
            raise ValueError('Empty event recording')
        partial.replace(target)
    finally:
        manifest.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def has_video(path):
    """Interrupted muxers can leave indexed files with no container header."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except FileNotFoundError:
        return False


class EventBuffer:
    def __init__(self, root, pre=7, post=10, join=concatenate):
        self.root = Path(root)
        self.buffer = self.root / '.buffer'
        self.buffer.mkdir(parents=True, exist_ok=True)
        self.pre, self.post = pre, post
        self.keep = pre + 20 + 2 * SEGMENT_SECONDS
        self.join = join
        saved = read_json(self.buffer / 'catalog.json', {})
        self.segments = {s['name']: s for s in saved.get('segments', [])
                         if has_video(self.buffer / s['name'])}
        self.active = saved.get('active')
        self.closed_until = saved.get('closed_until', 0)
        self._saved = None

    def persist(self):
        state = dict(segments=list(self.segments.values()), active=self.active,
                     closed_until=self.closed_until)
        encoded = json.dumps(state, sort_keys=True)
        if encoded != self._saved:
            write_json(self.buffer / 'catalog.json', state)
            self._saved = encoded

    def add_segment(self, path, start, end):
        if not has_video(path):
            return
        if path.name not in self.segments:
            self.segments[path.name] = dict(name=path.name, start=start, end=end)

    def read_index(self):
        index = self.buffer / 'index.csv'
        try:
            with index.open() as source:
                rows = list(csv.reader(source))
            for row in rows:
                if len(row) != 3:
                    continue
                try:
                    if float(row[2]) <= float(row[1]):
                        continue
                except ValueError:
                    continue
                name = Path(row[0]).name
                if not re.fullmatch(r'seg-\d{8}T\d{6}Z\.mkv', name):
                    continue
                path = self.buffer / name
                if path.is_file():
                    start = datetime.strptime(name[4:-4], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
                    self.add_segment(path, start, max(start + .01, path.stat().st_mtime))
        except FileNotFoundError:
            pass

    def recover(self):
        self.read_index()
        # A file absent from the closed-segment index was interrupted, not completed.
        for path in self.buffer.glob('seg-*.mkv'):
            if path.name not in self.segments:
                path.unlink()
        self.persist()

    def _trigger(self, trigger, now):
        last = trigger.get('last', 0)
        first = trigger.get('start', last)
        name = trigger.get('id', '')
        if (not valid_event_id(name)
                or last > now + 5 or last + self.post <= now or first > last):
            return
        start = max(first - self.pre, now - self.keep, self.closed_until)
        if start >= last + self.post:
            return
        if self.active is None:
            self.active = dict(name='event-' + name + '-' + str(int(start * 1000)), start=start, end=last + self.post,
                               last=last, segments=[], triggers=[name])
        elif start <= self.active['end']:
            self.active['end'] = max(self.active['end'], last + self.post)
            self.active['last'] = max(self.active['last'], last)
            if name not in self.active['triggers']:
                self.active['triggers'].append(name)

    def _protect(self):
        if self.active is None:
            return
        directory = self.buffer / self.active['name']
        directory.mkdir(exist_ok=True)
        selected = {s['name'] for s in self.active['segments']}
        for segment in self.segments.values():
            if segment['name'] not in selected and overlaps(segment, self.active):
                link_or_copy(self.buffer / segment['name'], directory / segment['name'])
                self.active['segments'].append(segment)

    def _finish(self, now):
        if not self.active:
            return
        end = min(self.active['end'], self.active['start'] + 300)
        directory = self.buffer / self.active['name']
        valid = [segment for segment in self.active['segments']
                 if has_video(directory / segment['name'])]
        if len(valid) != len(self.active['segments']):
            LOG.warning('Ignoring missing or empty segments in pending anomaly event')
            self.active['segments'] = valid
        if not self.active['segments']:
            if now > end + 30:
                LOG.warning('Event window had no video; releasing it to allow later events')
                self.closed_until = end
                directory = self.buffer / self.active['name']
                self.active = None
                self.persist()
                if directory.exists():
                    shutil.rmtree(directory)
            return
        coverage = max(s['end'] for s in self.active['segments'])
        if not (now >= end and (coverage >= end or now > end + 30)):
            return
        selected = sorted((s for s in self.active['segments'] if s['start'] <= end), key=lambda s: s['start'])
        directory = self.buffer / self.active['name']
        target = self.root / (self.active['name'] + '.mkv')
        if not target.exists():
            self.join([directory / s['name'] for s in selected], target)
        write_json(target.with_suffix('.json'), dict(kind='event', start=self.active['start'], end=end,
                   actual_start=min(s['start'] for s in selected), actual_end=max(s['end'] for s in selected),
                   triggers=self.active['triggers'], parts=len(selected)))
        self.closed_until = end
        old = self.active
        self.active = None
        self.persist()
        shutil.rmtree(self.buffer / old['name'])
        LOG.info('Saved anomaly video %s', target.name)

    def update(self, now, trigger):
        self._trigger(trigger, now)
        self._protect()
        self._finish(now)
        for name, segment in list(self.segments.items()):
            if segment['end'] < now - self.keep:
                (self.buffer / name).unlink(missing_ok=True)
                del self.segments[name]
        self.persist()

    def finish_on_stop(self, now):
        if self.active:
            self.active['end'] = min(self.active['end'], now)
            self._protect()
            self._finish(now + 31)
        self.persist()

    def details(self, now, status):
        return dict(recording_mode='anomaly', event_active=self.active is not None,
                    event_until=self.active['end'] if self.active else None,
                    buffer_segments=len(self.segments), analysis_available=analysis_available(status, now),
                    pre_seconds=self.pre, post_seconds=self.post)


def segment_command(url, directory):
    return ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'quiet',
            *input_args(url),
            '-map', '0:v:0', '-an', '-c:v', 'copy', '-progress', 'pipe:1', '-stats_period', '1',
            '-f', 'segment', '-segment_format', 'matroska', '-segment_time', str(SEGMENT_SECONDS),
            '-reset_timestamps', '1', '-strftime', '1', '-segment_list', str(directory / 'index.csv'),
            '-segment_list_type', 'csv', '-segment_list_size', '64',
            str(directory / 'seg-%Y%m%dT%H%M%SZ.mkv')]


def run(url, directory, stop, max_bytes, max_age, progress_type, prune, control, anomaly):
    manager = EventBuffer(directory, pre=float(os.environ.get('CAMERA_EVENT_PRE_SEC', '7')),
                          post=float(os.environ.get('CAMERA_EVENT_POST_SEC', '10')))
    if min(manager.pre, manager.post) < 0 or manager.post == 0:
        raise ValueError('Event windows must be non-negative with positive post time')
    manager.recover()
    while not stop.is_set():
        if source.capture_settings()['capture_mode'] == 'snapshots':
            manager.finish_on_stop(time.time())
            return
        if not control.enabled():
            control.heartbeat('paused', **manager.details(time.time(), read_json(anomaly / 'status.json', {})))
            stop.wait(1)
            continue
        process = subprocess.Popen(segment_command(url, manager.buffer), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(process.stdout.fileno(), False)
        progress = progress_type()
        started = time.monotonic()
        try:
            while process.poll() is None:
                try:
                    progress.feed(os.read(process.stdout.fileno(), 65536), time.monotonic())
                except BlockingIOError:
                    pass
                manager.read_index()
                now = time.time()
                status = read_json(anomaly / 'status.json', {})
                manager.update(now, read_json(anomaly / 'video-trigger.json', {}))
                state = progress.state(time.monotonic())
                if state == 'recording':
                    state = 'saving_event' if manager.active else 'buffering'
                control.heartbeat(state, **manager.details(now, status))
                prune(directory, max_bytes, max_age)
                last_progress = progress.updated if progress.updated is not None else started
                if stop.wait(1) or not control.enabled() or source.capture_settings()['capture_mode'] == 'snapshots' or time.monotonic() - last_progress > 30:
                    break
        finally:
            if process.poll() is None:
                # Let the segment muxer finalize the active segment on a clean stop.
                process.send_signal(2)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
            manager.read_index()
            now = time.time()
            manager.update(now, read_json(anomaly / 'video-trigger.json', {}))
            if stop.is_set() or not control.enabled():
                manager.finish_on_stop(now)
        if not stop.is_set() and control.enabled():
            control.heartbeat('connecting', **manager.details(time.time(), read_json(anomaly / 'status.json', {})))
            stop.wait(5)
    control.heartbeat('offline')
