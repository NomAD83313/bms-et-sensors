"""Analysis for several cameras in one process (one NumPy/ONNX/FAISS runtime).

Each camera keeps its own AnomalyWorker: own data and control directories, baseline,
sessions, status and commands. Cameras are stepped round-robin by due time; an
unreachable camera is retried slowly so it cannot starve the others.

The cameras come from CAMERA_ANALYSIS_CAMERAS (a JSON list) or the file named by
CAMERA_ANALYSIS_CONFIG. Every entry has: id, data, control, preview and optionally
recording_mode and settings (interval_sec, confirm_frames, normal_frames, model).
"""
import json
import logging
import os
from pathlib import Path
import sys
import time

try:
    from .anomaly_worker import AnomalyWorker, WorkerLocked
except ImportError:
    from anomaly_worker import AnomalyWorker, WorkerLocked
try:
    from .camera_registry import CAMERA_ID
except ImportError:
    from camera_registry import CAMERA_ID

LOG = logging.getLogger('camera-analysis')
OFFLINE_RETRY_SECONDS = 2.0
FAILURE_RETRY_SECONDS = 5.0
START_RETRY_SECONDS = 10.0
ALIVE_FILE = Path(os.environ.get('CAMERA_ANALYSIS_ALIVE_FILE', '/tmp/camera-analysis.alive'))


def parse_cameras(raw, allow_empty=False):
    """Validate the camera list; raises ValueError with a readable message."""
    try:
        cameras = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError as error:
        raise ValueError('Camera list is not valid JSON') from error
    if not isinstance(cameras, list) or (not cameras and not allow_empty):
        raise ValueError('Camera list must be a non-empty JSON array')
    seen, result = set(), []
    for entry in cameras:
        if not isinstance(entry, dict):
            raise ValueError('Every camera must be a JSON object')
        identifier = entry.get('id')
        if not isinstance(identifier, str) or not CAMERA_ID.fullmatch(identifier):
            raise ValueError('Camera id must be 1-16 lowercase letters, digits or dashes')
        if identifier in seen:
            raise ValueError(f'Duplicate camera id: {identifier}')
        seen.add(identifier)
        for key in ('data', 'control', 'preview'):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise ValueError(f'Camera {identifier} needs a "{key}" value')
        if not entry['preview'].startswith(('http://127.0.0.1:', 'http://localhost:')):
            raise ValueError(f'Camera {identifier} preview must be a local dashboard URL')
        if not isinstance(entry.get('settings', {}), dict):
            raise ValueError(f'Camera {identifier} settings must be an object')
        result.append(entry)
    return result


def load_cameras(environment=None):
    """The configured cameras plus those generated from the registry (CAMERA_ANALYSIS_EXTRA_FILE)."""
    environment = os.environ if environment is None else environment
    path = environment.get('CAMERA_ANALYSIS_CONFIG')
    if path:
        cameras = parse_cameras(Path(path).read_text())
    else:
        raw = environment.get('CAMERA_ANALYSIS_CAMERAS')
        if not raw:
            raise ValueError('Set CAMERA_ANALYSIS_CAMERAS or CAMERA_ANALYSIS_CONFIG')
        cameras = parse_cameras(raw)
    extra = environment.get('CAMERA_ANALYSIS_EXTRA_FILE')
    if extra and Path(extra).is_file():
        # Validate the union so an extra camera cannot reuse a configured id.
        cameras = parse_cameras(cameras + parse_cameras(Path(extra).read_text(), allow_empty=True))
    return cameras


def build_workers(cameras, factory=AnomalyWorker, quiet=False):
    """Create one worker per camera.

    Returns (workers by id, entries that could not start). A camera owned by another worker
    is reported once; the caller retries the rest later.
    """
    workers, failed = {}, []
    for entry in cameras:
        try:
            workers[entry['id']] = factory(
                data=entry['data'], control_dir=entry['control'], preview_url=entry['preview'],
                recording_mode=entry.get('recording_mode'), settings=entry.get('settings'),
                name=entry['id'], exclusive=True)
        except WorkerLocked as error:
            LOG.log(logging.DEBUG if quiet else logging.WARNING, 'Camera %s is not started: %s', entry['id'], error)
            failed.append(entry)
        except Exception:
            LOG.exception('Camera %s could not be started', entry['id'])
            failed.append(entry)
    return workers, failed


class Scheduler:
    """Step each worker when it is due; never let one camera delay or break the others."""

    def __init__(self, workers, pending=(), factory=AnomalyWorker, clock=time.monotonic, sleep=time.sleep):
        self.workers = dict(workers)
        self.pending, self.factory = list(pending), factory
        self.clock, self.sleep = clock, sleep
        self.due = {name: 0.0 for name in self.workers}
        self.next_start = clock() + START_RETRY_SECONDS

    def start_pending(self, now):
        """Retry cameras that were owned by another worker (for example a per-camera container)."""
        if not self.pending or now < self.next_start:
            return
        self.next_start = now + START_RETRY_SECONDS
        started, self.pending = build_workers(self.pending, self.factory, quiet=True)
        for name, worker in started.items():
            self.workers[name] = worker
            self.due[name] = 0.0
            LOG.info('Camera %s is now analysed by this service', name)

    def run_once(self):
        """Step every due camera once; return how long to sleep until the next one is due."""
        now = self.clock()
        self.start_pending(now)
        for name in sorted(self.due, key=self.due.get):
            if self.due[name] > now:
                continue
            worker = self.workers[name]
            try:
                wait = worker.step()
                if worker.state.get('state') == 'unavailable':
                    wait = max(wait, OFFLINE_RETRY_SECONDS)
            except Exception:
                LOG.exception('Camera %s failed; retrying', name)
                wait = FAILURE_RETRY_SECONDS
            self.due[name] = self.clock() + wait
        if not self.due:
            return START_RETRY_SECONDS
        return max(0.0, min(self.due.values()) - self.clock())

    def run(self):
        while True:
            try:
                ALIVE_FILE.touch()
            except OSError:
                pass
            self.sleep(self.run_once())


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    try:
        cameras = load_cameras()
    except (ValueError, OSError) as error:
        LOG.error('%s', error)
        return 2
    workers, pending = build_workers(cameras)
    LOG.info('Analysing %d camera(s): %s%s', len(workers), ', '.join(sorted(workers)) or '-',
             f'; waiting for {", ".join(entry["id"] for entry in pending)}' if pending else '')
    Scheduler(workers, pending).run()


if __name__ == '__main__':
    sys.exit(main())
