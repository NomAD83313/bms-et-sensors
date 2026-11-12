"""Analysis for several cameras in one process (one NumPy/ONNX/FAISS runtime).

Each camera keeps its own AnomalyWorker: own data and control directories, baseline,
sessions, status and commands. Each camera has at most one step in flight; blocking
network requests and training run independently from the other cameras.

The cameras come from CAMERA_ANALYSIS_CAMERAS (a JSON list) or the file named by
CAMERA_ANALYSIS_CONFIG. Every entry has: id, data, control, preview and optionally
recording_mode and settings (interval_sec, confirm_frames, normal_frames, model).
"""
import json
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import sys
import threading
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
POLL_SECONDS = .05
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
        cameras = parse_cameras(Path(path).read_text(), allow_empty=True)
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

    def __init__(self, workers, pending=(), factory=AnomalyWorker, clock=time.monotonic, sleep=time.sleep,
                 executor=None, camera_loader=None):
        self.workers = dict(workers)
        self.pending, self.factory = list(pending), factory
        self.clock = clock
        self.wakeup = threading.Event()
        self.sleep = self.wakeup.wait if sleep is time.sleep else sleep
        self.due = {name: 0.0 for name in self.workers}
        self.next_start = clock() + START_RETRY_SECONDS
        # Keep a stable thread per camera so native allocator caches are reused.
        # A separate initializer cannot consume an active camera's thread.
        self.executor = executor
        self.executors = {}
        self.in_flight = {}
        self.starting = None
        self.camera_loader = camera_loader
        self.known_ids = set(workers) | {c['id'] for c in pending}
        self.desired_ids = set(self.known_ids)
        self.next_refresh = 0

    def refresh_cameras(self, now):
        """Discover changes without interrupting other cameras or their learning sessions."""
        if self.camera_loader is None or now < self.next_refresh:
            return
        self.next_refresh = now + 2
        try:
            cameras = self.camera_loader()
        except (OSError, ValueError):
            LOG.warning('Camera registry could not be refreshed; keeping current workers')
            return
        self.desired_ids = {camera['id'] for camera in cameras}
        self.pending = [entry for entry in self.pending if entry['id'] in self.desired_ids]
        self.known_ids.intersection_update(self.desired_ids | set(self.workers))
        for camera in cameras:
            if camera['id'] not in self.known_ids:
                self.known_ids.add(camera['id'])
                self.pending.append(camera)
                self.next_start = now

    def submit(self, name, function, *args, **kwargs):
        if self.executor is not None:
            executor = self.executor
        else:
            if name not in self.executors:
                self.executors[name] = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f'camera-analysis-{name or "init"}')
            executor = self.executors[name]
        future = executor.submit(function, *args, **kwargs)
        future.add_done_callback(lambda _: self.wakeup.set())
        return future

    def start_pending(self, now):
        """Retry cameras that were owned by another worker (for example a per-camera container)."""
        if self.starting is not None:
            if not self.starting.done():
                return
            started, failed = self.starting.result()
            self.pending = [entry for entry in failed if entry['id'] in self.desired_ids] + self.pending
            self.starting = None
            for name, worker in started.items():
                if name not in self.desired_ids:
                    worker.close()
                    self.known_ids.discard(name)
                    continue
                self.workers[name] = worker
                self.due[name] = 0.0
                LOG.info('Camera %s is now analysed by this service', name)
            return
        if not self.pending or now < self.next_start:
            return
        self.next_start = now + START_RETRY_SECONDS
        pending, self.pending = self.pending, []
        self.starting = self.submit(None, build_workers, pending, self.factory, quiet=True)
        if self.starting.done():
            self.start_pending(now)

    def step_worker(self, name, worker):
        """Run only on a pool thread; record the completion time for retry scheduling."""
        try:
            wait = worker.step()
            if worker.state.get('state') == 'unavailable':
                wait = max(wait, OFFLINE_RETRY_SECONDS)
        except Exception:
            LOG.exception('Camera %s failed; retrying', name)
            wait = FAILURE_RETRY_SECONDS
        return max(0.0, wait), self.clock()

    def collect_steps(self):
        for name, future in list(self.in_flight.items()):
            if future.done():
                wait, finished = future.result()
                self.due[name] = finished + wait
                del self.in_flight[name]

    def retire_workers(self):
        """Release deleted cameras only after their current frame has finished."""
        for name in set(self.workers) - self.desired_ids:
            if name in self.in_flight:
                continue
            self.workers.pop(name).close()
            self.due.pop(name, None)
            self.known_ids.discard(name)
            executor = self.executors.pop(name, None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)

    def run_once(self):
        """Dispatch due cameras without waiting for network or model execution."""
        now = self.clock()
        self.refresh_cameras(now)
        self.start_pending(now)
        self.collect_steps()
        self.retire_workers()
        for name in sorted(self.due, key=self.due.get):
            if name not in self.desired_ids or name in self.in_flight or self.due[name] > now:
                continue
            self.in_flight[name] = self.submit(name, self.step_worker, name, self.workers[name])
        self.collect_steps()
        now = self.clock()
        deadlines = [due for name, due in self.due.items() if name not in self.in_flight]
        if self.in_flight or self.starting is not None:
            deadlines.append(now + POLL_SECONDS)
        elif self.pending:
            deadlines.append(self.next_start)
        if self.camera_loader is not None:
            deadlines.append(self.next_refresh)
        return max(0.0, min(deadlines) - now) if deadlines else START_RETRY_SECONDS

    def close(self):
        for executor in ([self.executor] if self.executor is not None else self.executors.values()):
            executor.shutdown(wait=True, cancel_futures=True)

    def run(self):
        try:
            while True:
                self.wakeup.clear()
                try:
                    ALIVE_FILE.touch()
                except OSError:
                    pass
                self.sleep(self.run_once())
        finally:
            self.close()


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
    Scheduler(workers, pending, camera_loader=load_cameras if os.environ.get('CAMERA_ANALYSIS_CONFIG') else None).run()


if __name__ == '__main__':
    sys.exit(main())
