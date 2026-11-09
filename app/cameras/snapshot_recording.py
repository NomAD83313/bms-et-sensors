"""Bounded JPEG pre-event buffer with asynchronous H.264 event encoding."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

try:
    from .anomaly_control import read_json, write_json
    from . import source
except ImportError:
    from anomaly_control import read_json, write_json
    import source

LOG = logging.getLogger('camera-snapshots')


def encode(directory, target):
    metadata = read_json(directory / 'event.json', {})
    frames = metadata.get('frames', [])
    if not frames:
        raise ValueError('No snapshots available for event')
    metadata.update(actual_start=frames[0]['time'], actual_end=frames[-1]['time'], parts=len(frames))
    if target.exists() and target.stat().st_size > 0:
        write_json(target.with_suffix('.json'), {k: v for k, v in metadata.items() if k != 'frames'})
        return
    interval = metadata['snapshot_interval_ms'] / 1000
    lines = []
    for index, frame in enumerate(frames):
        name = frame['name']
        if not re.fullmatch(r'frame-\d+\.jpg', name):
            raise ValueError('Invalid snapshot filename')
        duration = frames[index+1]['time']-frame['time'] if index+1 < len(frames) else interval
        lines.extend(["file '" + name + "'", 'duration ' + str(max(.001, duration))])
    lines.append("file '" + frames[-1]['name'] + "'")
    manifest = directory / 'frames.concat'
    manifest.write_text('\n'.join(lines)+'\n')
    partial = target.with_suffix('.partial')
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0', '-i', str(manifest.resolve()),
            '-vf', 'fps=' + str(1000/metadata['snapshot_interval_ms']),
            '-r', str(1000/metadata['snapshot_interval_ms']), '-fps_mode', 'cfr',
            '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-threads', '1', '-f', 'matroska', '-y', str(partial.resolve())],
            check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not partial.stat().st_size:
            raise ValueError('Empty snapshot video')
        partial.replace(target)
        write_json(target.with_suffix('.json'), {k: v for k, v in metadata.items() if k != 'frames'})
    finally:
        partial.unlink(missing_ok=True)


class SnapshotBuffer:
    def __init__(self, root, settings, pre=7, post=10):
        self.root = Path(root)
        self.buffer = self.root / '.snapshot-buffer'
        self.buffer.mkdir(parents=True, exist_ok=True)
        self.settings, self.pre, self.post = settings, pre, post
        self.frames = []
        self.active = None
        self.closed_until = 0
        # Orphan ring frames are temporary; protected event frames live in subdirectories.
        for path in self.buffer.glob('frame-*.jpg'):
            path.unlink()

    def add(self, jpeg, captured_at):
        name = 'frame-' + str(int(captured_at * 1e9)) + '.jpg'
        path = self.buffer / name
        if path.exists():
            return
        path.write_bytes(jpeg)
        self.frames.append(dict(name=name, time=captured_at))
        self.protect()
        cutoff = captured_at - self.pre - self.post - 10
        while self.frames and self.frames[0]['time'] < cutoff:
            (self.buffer / self.frames.pop(0)['name']).unlink(missing_ok=True)

    def trigger(self, marker, now):
        first, last = marker.get('start', 0), marker.get('last', 0)
        identifier = marker.get('id', '')
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier)
                or first > last or last > now + 5 or last + self.post <= now):
            return
        start = max(first-self.pre, self.closed_until)
        if start >= last+self.post:
            return
        if self.active is None:
            self.open(start, last+self.post, 'event', [identifier])
        elif start <= self.active['end']:
            self.active['end'] = max(self.active['end'], last+self.post)
            if identifier not in self.active['triggers']:
                self.active['triggers'].append(identifier)
        self.protect()

    def open(self, start, end, kind, triggers=None):
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(start))
        name = ('event-' if kind == 'event' else '') + stamp + '-' + uuid.uuid4().hex
        self.active = dict(name=name, kind=kind, start=start, end=end,
            triggers=triggers or [], frames=[], capture_mode='snapshots',
            snapshot_interval_ms=self.settings['snapshot_interval_ms'], shutter_us=self.settings['shutter_us'])
        (self.buffer / name).mkdir()
        self.protect()

    def protect(self):
        if self.active is None:
            return
        names = {frame['name'] for frame in self.active['frames']}
        end = min(self.active['end'], self.active['start']+300)
        for frame in self.frames:
            if frame['name'] in names or not self.active['start'] <= frame['time'] <= end:
                continue
            original, target = self.buffer / frame['name'], self.buffer / self.active['name'] / frame['name']
            try:
                os.link(original, target)
            except OSError:
                shutil.copyfile(original, target)
            self.active['frames'].append(frame)
        if self.active['frames']:
            write_json(self.buffer / self.active['name'] / 'event.json', self.active)

    def close(self, now, force=False):
        if self.active is None:
            return None
        end = min(self.active['end'], self.active['start']+300)
        if not force and now < end:
            return None
        event = self.active
        self.active = None
        self.closed_until = min(now, end)
        directory = self.buffer / event['name']
        if not event['frames']:
            shutil.rmtree(directory)
            return None
        event['end'] = self.closed_until
        event['actual_start'] = event['frames'][0]['time']
        event['actual_end'] = event['frames'][-1]['time']
        event['parts'] = len(event['frames'])
        write_json(directory / 'event.json', event)
        return directory, self.root / (event['name']+'.mkv')


def run(directory, stop, max_bytes, max_age, prune, control, anomaly, recording_mode, seconds):
    settings = source.capture_settings()
    manager = SnapshotBuffer(directory, settings,
        pre=float(os.environ.get('CAMERA_EVENT_PRE_SEC', '7')),
        post=float(os.environ.get('CAMERA_EVENT_POST_SEC', '10')))
    signature = source.capture_signature(settings)
    pool = ThreadPoolExecutor(max_workers=1)
    pending = []
    failed = False
    last_token = None

    def schedule(item):
        if item:
            raw, target = item
            pending.append((pool.submit(encode, raw, target), raw, target))

    # Resume protected exports, including an active window interrupted by a shutdown.
    recovery = list(sorted(manager.buffer.glob('*/event.json')))

    def recover_exports():
        while recovery and len(pending) < 2 and not failed:
            path = recovery.pop(0)
            name = path.parent.name
            metadata = read_json(path, {})
            frames = metadata.get('frames', [])
            if not re.fullmatch(r'(event-)?\d{8}T\d{6}Z-[a-f0-9]{32}', name) or not frames:
                continue
            # Power loss can leave metadata pointing only to empty frame files.
            # Keep the evidence outside the retry queue so new recording can resume.
            if all(re.fullmatch(r'frame-\d+\.jpg', frame.get('name', ''))
                   and (not (path.parent / frame['name']).exists()
                        or (path.parent / frame['name']).stat().st_size == 0)
                   for frame in frames):
                archive = Path(directory) / '.snapshot-recovery'
                archive.mkdir(exist_ok=True)
                destination = archive / (name + '-' + uuid.uuid4().hex)
                path.parent.rename(destination)
                write_json(destination / 'recovery-error.json', dict(
                    reason='All recorded JPEG files are empty or missing',
                    frames=len(frames), recovered_at=time.time()))
                LOG.warning('Archived unrecoverable snapshot event: %s (%d empty or missing frames)',
                            name, len(frames))
                continue
            manager.closed_until = max(manager.closed_until, frames[-1]['time'])
            schedule((path.parent, Path(directory) / (name+'.mkv')))

    recover_exports()
    try:
        while not stop.is_set() and source.capture_signature(source.capture_settings()) == signature:
            for item in list(pending):
                future, raw, target = item
                if not future.done():
                    continue
                pending.remove(item)
                try:
                    future.result()
                    shutil.rmtree(raw)
                    LOG.info('Saved video from snapshots: %s', target.name)
                except (OSError, ValueError, subprocess.SubprocessError):
                    failed = True
                    LOG.warning('Snapshot export failed; raw event retained for retry')
            recover_exports()
            prune(Path(directory), max_bytes, max_age)
            if failed:
                schedule(manager.close(time.time(), force=True))
                control.heartbeat('export_failed', capture_mode='snapshots', export_failed=True)
                stop.wait(.5)
                continue
            if not control.enabled():
                schedule(manager.close(time.time(), force=True))
                control.heartbeat('paused', capture_mode='snapshots', export_failed=failed)
                stop.wait(.2)
                continue
            try:
                jpeg, captured_at, token = source.snapshot(last_token)
                if token != last_token:
                    manager.add(jpeg, captured_at)
                    last_token = token
                now = time.time()
                if recording_mode == 'anomaly':
                    manager.trigger(read_json(anomaly / 'video-trigger.json', {}), now)
                elif manager.active is None and len(pending) < 2:
                    manager.open(captured_at, captured_at+seconds, 'continuous')
                if len(pending) < 2:
                    schedule(manager.close(now))
                analysis = read_json(anomaly / 'status.json', {})
                analysis_ok = analysis.get('state') in ('normal', 'suspect', 'anomaly') and 0 <= now-analysis.get('updated', 0) < 20
                state = ('saving_event' if manager.active else 'buffering') if recording_mode == 'anomaly' else 'recording'
                control.heartbeat(state, capture_mode='snapshots', event_active=manager.active is not None,
                    event_until=manager.active['end'] if manager.active else None,
                    analysis_available=analysis_ok, exporting=len(pending), export_failed=failed)
            except (OSError, ValueError):
                control.heartbeat('connecting', capture_mode='snapshots')
            stop.wait(min(.1, settings['snapshot_interval_ms']/2000))
    finally:
        schedule(manager.close(time.time(), force=True))
        pool.shutdown(wait=True)
        for future, raw, target in pending:
            try:
                future.result()
                shutil.rmtree(raw)
            except (OSError, ValueError, subprocess.SubprocessError):
                LOG.warning('Snapshot export interrupted; protected frames retained')
