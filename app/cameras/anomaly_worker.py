"""Visual anomaly analysis and linked event-recording session control."""
import contextlib
import fcntl
import json
import logging
import os
from pathlib import Path
import pickle
import shutil
import threading
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import quote

import control
from anomaly_control import (
    CONFIGURATION_ACTIONS, DATA, Confirmation, DEFAULT_INPUT_SIZE, DEFAULT_MODEL, DEFAULT_MULTIPLIER, MODELS,
    SESSION_ACTIONS, command_mode, input_dimensions, read_json,
    validate_confirmation_frames, validate_input_size, validate_model,
    validate_learning_seconds, validate_multiplier, write_json,
)
from patchcore_lite import PatchCoreLite, overlay
try:
    from .model_profiles import ANALYSIS_SOURCE, geometry_fields
    from .roi import ROIGeometryChanged, validate_roi
    from .learning_sessions import LearningSessions, configuration, configuration_matches, MAX_SESSIONS
    from .frame_gate import FrameGate
    from .source import MAX_FRAME_BYTES
except ImportError:
    from model_profiles import ANALYSIS_SOURCE, geometry_fields
    from roi import ROIGeometryChanged, validate_roi
    from learning_sessions import LearningSessions, configuration, configuration_matches, MAX_SESSIONS
    from frame_gate import FrameGate
    from source import MAX_FRAME_BYTES

LOG = logging.getLogger('camera-anomaly')
MAX_EVENTS = 100
LEARNING_INTERVAL_SEC = 2
# Actions that keep collected samples and the running session unless a setting changed.
NON_DISRUPTIVE_ACTIONS = ('configure', 'confirmation') + CONFIGURATION_ACTIONS
# Timing and encoder fields measured by the last full evaluation, shown for skipped frames.
EVALUATION_METRICS = ('preparation_ms', 'jpeg_decode_ms', 'image_transform_ms', 'tensor_ms', 'encoder_ms',
                      'feature_ms', 'distance_ms', 'jpeg_draft', 'decoded_mode', 'decoded_width',
                      'decoded_height')
LOCK_RETRY_SECONDS = 10
ERROR_LOG_INTERVAL_SECONDS = 60
# Baselines written by the former PyTorch implementation; rebuilt from saved sessions.
LEGACY_BASELINES = ('baseline.pt', 'baseline-previous.pt', 'baseline-next.pt')


class WorkerLocked(RuntimeError):
    """Another analysis worker already owns this camera directory."""


def acquire_directory_lock(path):
    """Hold an exclusive lock on `path` for the life of the returned handle."""
    handle = open(path, 'a+')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise WorkerLocked(f'Another analysis worker already owns {path.parent}') from error
    return handle


class AnomalyWorker:
    def __init__(self, data=None, control_dir=None, preview_url=None, recording_mode=None,
                 settings=None, name=None, exclusive=False):
        """One camera's analysis; the arguments default to the container environment.

        `settings` may override interval_sec, confirm_frames, normal_frames and the
        PatchCoreLite options (`model`) for this camera. `exclusive` takes a lock so that
        two workers never analyse the same camera directory at the same time.
        """
        settings = settings or {}
        self.name = name
        self.log = LOG.getChild(name) if name else LOG
        self.data = DATA if data is None else Path(data)
        self.control_dir = None if control_dir is None else Path(control_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self._lock = acquire_directory_lock(self.data / '.worker.lock') if exclusive else None
        (self.data / 'events').mkdir(exist_ok=True)
        self.preview = preview_url or os.environ.get(
            'CAMERA_ANOMALY_PREVIEW_URL', 'http://127.0.0.1:8082/api/live.jpg')
        self.recording_mode = recording_mode or os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
        self.interval = max(.2, float(settings.get(
            'interval_sec', os.environ.get('CAMERA_ANOMALY_INTERVAL_SEC', '.2'))))
        self.confirmation_frames = max(1, min(5, int(settings.get(
            'confirm_frames', os.environ.get('CAMERA_ANOMALY_CONFIRM_FRAMES', '2')))))
        sample_count = max(20, min(120, int(settings.get(
            'normal_frames', os.environ.get('CAMERA_ANOMALY_NORMAL_FRAMES', '60')))))
        self.state = dict(state='loading', message='Loading visual model', updated=time.time())
        self.baseline_source = None
        self.publish_status()

        previous = read_json(self.data / 'session.json', {})
        try:
            self.learning_seconds = validate_learning_seconds(
                previous.get('learning_seconds', sample_count * 2))
            self.sample_count = self.learning_seconds // 2
        except ValueError:
            self.learning_seconds = sample_count * 2
            self.sample_count = sample_count
        self.confirmation_frames = validate_confirmation_frames(
            previous.get('confirmation_frames', self.confirmation_frames))
        self.baseline_invalidated = previous.get('baseline_invalidated', False)
        configuration_reset = False

        def saved_setting(validator, key, default):
            # An invalid saved setting falls back to its default and forces new learning.
            nonlocal configuration_reset
            try:
                return validator(previous.get(key, default))
            except ValueError:
                configuration_reset = True
                return default

        selected_input_size = saved_setting(validate_input_size, 'input_size', DEFAULT_INPUT_SIZE)
        selected_model = saved_setting(validate_model, 'model_key', DEFAULT_MODEL)
        self.selected_roi = saved_setting(validate_roi, 'roi', None)
        self.baseline_invalidated = self.baseline_invalidated or configuration_reset

        self.model = PatchCoreLite(**settings.get('model', {}))
        if selected_input_size != self.model.input_size:
            self.model.set_input_size(selected_input_size)
        if selected_model != self.model.model_key:
            self.model.set_model(selected_model)
        if self.selected_roi is not None:
            self.model.set_roi(self.selected_roi)

        self.model_path = self.data / 'baseline.npz'
        # A rebuilt baseline is staged, then swapped in while the previous one is kept as backup.
        self.staging_path = self.data / 'baseline-next.npz'
        self.backup_path = self.data / 'baseline-previous.npz'
        self.pending_migration = False
        self.gate = FrameGate()
        self.last_metrics = {}
        self._last_error = (None, float('-inf'))
        self.sessions = LearningSessions(self.data)
        self.sessions.recover()
        self.learning_error = None
        catalog = self.sessions.catalog()
        current = configuration(self.model)
        if catalog['sessions'] and not configuration_matches(current, catalog):
            self.baseline_invalidated = configuration_reset = True
        if self.baseline_invalidated:
            self.invalidate_baseline(clear_sessions=configuration_reset)
        elif self.model_path.exists() or self.backup_path.exists():
            try:
                self.load_baseline()
            except ValueError:
                self.invalidate_baseline()
                configuration_reset = True
                self.log.warning('Baseline is incompatible; new learning required')
        elif any((self.data / name).exists() for name in LEGACY_BASELINES):
            if catalog['sessions']:
                self.pending_migration = True
                self.log.info('Rebuilding the baseline from saved normal sessions (format change)')
            else:
                self.invalidate_baseline()
                configuration_reset = True
                self.log.warning('Legacy baseline has no saved sessions; new learning required')

        self.last_request = previous.get('last_request')
        if configuration_reset:
            self.last_request = read_json(self.data / 'command.json', {}).get(
                'id', self.last_request)
        self.resume_after_migration = previous.get('monitoring', False)
        self.monitoring = previous.get('monitoring', False) and self.model.memory is not None
        self.multiplier = previous.get('multiplier', DEFAULT_MULTIPLIER)
        self.learning = False
        self.collected = 0
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.candidate_since = None
        self.video_id = None
        self.event_count = len(list((self.data / 'events').glob('*.json')))

        if configuration_reset:
            self.clear_video_trigger()
            self.save_session()

    def close(self):
        """Release the exclusive camera lock, if this worker holds one."""
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def publish_status(self, state=None):
        write_json(self.data / 'status.json', self.state if state is None else state)

    def stop_learning(self):
        """Abandon the running learning session and its collected frames."""
        self.learning = self.monitoring = False
        self.collected = 0
        self.sessions.abort()

    def invalidate_baseline(self, clear_sessions=False):
        self.baseline_invalidated = True
        self.baseline_source = None
        self.model.memory = None
        self.model.threshold = None
        self.model.baseline_frames = 0
        self.model_path.unlink(missing_ok=True)
        self.backup_path.unlink(missing_ok=True)
        for name in LEGACY_BASELINES:
            (self.data / name).unlink(missing_ok=True)
        if clear_sessions:
            self.learning_error = None
            count = self.sessions.clear()
            if count:
                self.log.info('Removed %d incompatible normal sessions', count)

    def load_baseline(self):
        backup = self.backup_path
        for path in (self.model_path, backup):
            if not path.is_file():
                continue
            try:
                self.model.set_input_size(self.model.input_size)
                self.model.load(path)
                revision = self.model.learning_revision
                catalog = self.sessions.catalog()
                if (revision or catalog['sessions']) and revision != catalog['revision']:
                    raise ValueError('Interrupted learning publication')
                if path == backup:
                    backup.replace(self.model_path)
                else:
                    backup.unlink(missing_ok=True)
                self.baseline_source = self.model.source_size
                return
            except (OSError, ValueError, RuntimeError, EOFError, pickle.UnpicklingError):
                continue
        self.model.set_input_size(self.model.input_size)
        raise ValueError('No compatible normal baseline is available')

    def clear_video_trigger(self):
        write_json(self.data / 'video-trigger.json', {})

    def reset_detection_candidate(self):
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.candidate_since = None
        self.video_id = None
        self.gate.reset()

    def save_session(self):
        write_json(self.data / 'session.json', dict(
            last_request=self.last_request, monitoring=self.monitoring,
            multiplier=self.multiplier, input_size=self.model.input_size,
            model_key=self.model.model_key,
            roi=self.selected_roi, learning_seconds=self.learning_seconds,
            baseline_invalidated=self.baseline_invalidated,
            confirmation_frames=self.confirmation_frames,
            analysis_source=(ANALYSIS_SOURCE if not self.baseline_invalidated
                             and self.model.memory is not None else None)))

    def update_geometry(self, state):
        source_size = self.model.source_size
        size = self.model.effective_input_size
        if source_size is not None:
            state.update(source_width=source_size[0], source_height=source_size[1])
        width, height = size or input_dimensions(self.model.input_size)
        state.update(geometry_fields(width, height, self.model.model_key))

    def configuration_change(self, action, command):
        """Return whether a resize/model/roi command differs from the current setting."""
        if action == 'resize':
            return validate_input_size(command.get('input_size')) != self.model.input_size
        if action == 'model':
            return validate_model(command.get('model_key')) != self.model.model_key
        if action == 'roi':
            return validate_roi(command.get('roi')) != self.selected_roi
        return False

    def apply_configuration(self, action, command):
        if action == 'resize':
            self.model.set_input_size(command['input_size'])
        elif action == 'model':
            self.model.set_model(command['model_key'])
        elif action == 'roi':
            self.selected_roi = validate_roi(command['roi'])
            self.model.set_roi(self.selected_roi)

    def apply_pending_command(self):
        command = read_json(self.data / 'command.json', {})
        if not command.get('id') or command['id'] == self.last_request:
            return
        action = command.get('action')
        if action in SESSION_ACTIONS:
            self.apply_session_command(command)
            return
        was_learning = self.learning
        changed = self.configuration_change(action, command)
        if action not in CONFIGURATION_ACTIONS or changed:
            self.learning, self.monitoring = command_mode(
                action, self.model.memory is not None, self.learning, self.monitoring)
        if action == 'confirmation':
            self.confirmation_frames = validate_confirmation_frames(
                command.get('confirmation_frames'))
        elif changed:
            self.apply_configuration(action, command)
            self.invalidate_baseline(clear_sessions=True)
        elif action == 'invalidate':
            self.model.set_input_size(self.model.input_size)
            self.invalidate_baseline(clear_sessions=True)
        else:
            self.multiplier = validate_multiplier(command.get('multiplier', self.multiplier))
        self.last_request = command['id']
        self.reset_detection_candidate()
        if action not in NON_DISRUPTIVE_ACTIONS or changed:
            self.collected = 0
            self.sessions.abort()
            self.clear_video_trigger()
        if action == 'pause' and was_learning and self.model_path.exists():
            try:
                self.load_baseline()
                self.baseline_invalidated = False
            except ValueError:
                self.baseline_invalidated = True
        self.save_session()

    def apply_session_command(self, command):
        action = command['action']
        self.last_request = command['id']
        self.learning_error = None
        try:
            if self.learning:
                if action != 'learn':
                    raise ValueError('Pause the current learning session first')
            self.multiplier = validate_multiplier(command.get('multiplier', self.multiplier))
            if action == 'rename-session':
                self.sessions.rename(command['session_id'], command.get('session_name', ''),
                                     command.get('session_note', ''))
                return
            if action != 'learn':
                catalog = self.sessions.catalog()
                current = configuration(self.model)
                if not catalog['sessions'] or not configuration_matches(current, catalog):
                    raise ValueError('Create a new baseline for the current model and area first')
            if action in ('learn', 'add-session', 'replace-session'):
                mode = {'learn': 'new', 'add-session': 'add', 'replace-session': 'replace'}[action]
                self.sessions.begin(mode, command.get('session_name', ''),
                                    command.get('session_note', ''), command.get('session_id'))
                if 'learning_seconds' in command:
                    self.learning_seconds = validate_learning_seconds(command['learning_seconds'])
                    self.sample_count = self.learning_seconds // 2
                if action == 'learn' and self.selected_roi is not None:
                    self.model.set_input_size(self.model.input_size)
                self.learning, self.monitoring = True, False
                self.collected = 0
            else:
                self.learning = self.monitoring = False
                self.sessions.abort()
                items = self.sessions.proposed(command['session_id']) if action == 'remove-session' else self.sessions.catalog()['sessions']
                self.clear_video_trigger()
                control.follow_detection(False, self.recording_mode, self.control_dir)
                self.rebuild_sessions(self.state, items, resume=False)
        except (ValueError, OSError) as error:
            self.learning_error = str(error) if isinstance(error, ValueError) else 'Session storage unavailable; check free disk space'
        finally:
            if action != 'rename-session':
                self.reset_detection_candidate()
                self.clear_video_trigger()
            self.save_session()

    def effective_interval(self):
        return max(LEARNING_INTERVAL_SEC, self.interval) if self.learning else self.interval

    def status_snapshot(self):
        width, height = input_dimensions(self.model.input_size)
        model_key = self.model.model_key
        memory = self.model.memory
        memory_shape = getattr(memory, 'shape', ())
        memory_vectors = (int(memory_shape[0]) if memory_shape
                          else self.model.coreset_size)
        state = dict(
            updated=time.time(), model='PatchCore-style / ' + MODELS[model_key]['label'],
            model_key=model_key, model_label=MODELS[model_key]['label'],
            baseline_ready=self.model.memory is not None,
            baseline_frames=self.model.baseline_frames,
            monitoring_enabled=self.monitoring, input_size=self.model.input_size,
            roi=self.selected_roi,
            inference_threads=self.model.inference_threads,
            feature_engine=self.model.feature_engine,
            distance_engine=self.model.distance_engine,
            score_top_k=self.model.top_k,
            coreset_size=memory_vectors,
            onnx_provider=self.model.onnx_provider,
            input_width=width, input_height=height,
            retraining_required=self.baseline_invalidated,
            analysis_source=ANALYSIS_SOURCE, last_request=self.last_request,
            collected=self.collected, target=self.sample_count,
            multiplier=self.multiplier, learning_seconds=self.learning_seconds,
            analysis_interval_sec=self.effective_interval(),
            confirmation_frames=self.confirmation_frames,
            event_count=self.event_count,
            learning_error=self.learning_error,
            learning_sessions=self.sessions.public(), session_limit=MAX_SESSIONS,
            state='paused' if self.model.memory is not None else 'untrained',
            message=('Analysis paused' if self.model.memory is not None
                     else 'Learn a normal view to begin'))
        self.update_geometry(state)  # effective input and heatmap size, which depend on the area
        state.update(self.gate.status())
        if self.baseline_invalidated:
            state['message'] = (
                'Model, area or analysis source changed. Learn normal view again before detection.')
        return state

    def fetch_analysis_frame(self):
        fetch_started = time.monotonic()
        with urllib.request.urlopen(self.preview + '?analysis=1', timeout=5) as response:
            jpeg = response.read(MAX_FRAME_BYTES + 1)
            token = response.headers.get('X-Camera-Frame-Token')
            if response.headers.get('X-Camera-Analysis-Source') != ANALYSIS_SOURCE:
                raise ValueError('Full-frame analysis source is unavailable')
        if (len(jpeg) > MAX_FRAME_BYTES or not jpeg.startswith(b'\xff\xd8')
                or not jpeg.endswith(b'\xff\xd9')):
            raise ValueError('Full analysis frame is incomplete')
        return jpeg, token, round((time.monotonic() - fetch_started) * 1000)

    def validate_frame_token(self, token):
        if not token:
            return
        # Errors arriving while inference ran invalidate the earlier JPEG too.
        with urllib.request.urlopen(
                self.preview + '?check=' + quote(token, safe=''), timeout=2) as response:
            if json.loads(response.read(4096)).get('valid') is not True:
                raise ValueError('Preview frame invalidated')

    def record_preparation_metrics(self, state):
        preparation = self.model.preparation_metrics
        if not preparation:
            return
        state.update(
            preparation_ms=round(preparation['total_ms']),
            jpeg_decode_ms=round(preparation['decode_ms']),
            image_transform_ms=round(preparation['transform_ms']),
            tensor_ms=round(preparation['tensor_ms']),
            encoder_ms=max(0, state['feature_ms'] - round(preparation['total_ms'])),
            jpeg_draft=self.model.jpeg_draft,
            decoded_mode=preparation.get('decoded_mode', 'RGB'),
            decoded_width=preparation['decoded_width'],
            decoded_height=preparation['decoded_height'])

    def finish_learning(self, state):
        self.rebuild_sessions(state, self.sessions.proposed(), resume=True)

    @contextlib.contextmanager
    def training_heartbeat(self, state):
        """Keep status.json fresh while a long rebuild blocks the worker loop."""
        done = threading.Event()

        def beat():
            while not done.wait(2):
                self.publish_status(dict(state, updated=time.time()))

        heartbeat = threading.Thread(target=beat, daemon=True)
        heartbeat.start()
        try:
            yield
        finally:
            done.set()
            heartbeat.join()

    def publish_baseline(self, items, revision, progress):
        """Fit and atomically publish the baseline; restore the previous file if publication fails."""
        temporary = self.staging_path
        backup = self.backup_path
        published = False
        try:
            if items:
                calibration = self.model.fit_sessions(self.sessions.groups(items), progress=progress)
                for item, metrics in zip(items, calibration):
                    item.update(metrics)
                self.model.learning_revision = revision
                self.model.save(temporary)
                if self.model_path.exists():
                    shutil.copyfile(self.model_path, backup)
                temporary.replace(self.model_path)
            else:
                self.model.set_input_size(self.model.input_size)
                if self.model_path.exists():
                    shutil.copyfile(self.model_path, backup)
                self.model_path.unlink(missing_ok=True)
            published = True
            self.sessions.publish(items, revision)
        except Exception:
            if published:
                if backup.exists():
                    backup.replace(self.model_path)
                else:
                    self.model_path.unlink(missing_ok=True)
            raise

    def rebuild_sessions(self, state, items, resume):
        state.update(state='training', training_collected=0,
                     training_target=sum(item['frames'] for item in items),
                     message='Rebuilding saved normal sessions and calibrating the threshold')
        self.publish_status(state)
        temporary = self.staging_path
        backup = self.backup_path
        revision = uuid.uuid4().hex

        def progress(completed, total):
            state.update(training_collected=completed, training_target=total)

        try:
            with self.training_heartbeat(state):
                self.publish_baseline(items, revision, progress)
        except Exception:
            self.stop_learning()
            self.learning_error = 'Learning rebuild failed; previous sessions are retained. Check storage and retry.'
            self.model.set_input_size(self.model.input_size)
            try:
                self.load_baseline()
                self.baseline_invalidated = False
            except (OSError, ValueError):
                self.baseline_invalidated = True
            self.save_session()
            raise
        finally:
            temporary.unlink(missing_ok=True)
            backup.unlink(missing_ok=True)
        self.learning = False
        queued = read_json(self.data / 'command.json', {})
        if queued.get('id') != self.last_request and queued.get('action') == 'pause':
            resume = False
        self.monitoring = bool(items) and resume
        self.baseline_source = self.model.source_size if items else None
        self.collected = 0
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.gate.reset()
        self.baseline_invalidated = not bool(items)
        self.save_session()
        control.follow_detection(self.monitoring, self.recording_mode, self.control_dir)
        state.update(
            state='normal' if self.monitoring else 'paused' if items else 'untrained', baseline_ready=bool(items),
            baseline_frames=self.model.baseline_frames,
            monitoring_enabled=self.monitoring, retraining_required=self.baseline_invalidated,
            learning_sessions=self.sessions.public(),
            message='Normal sessions rebuilt; monitoring started' if self.monitoring else
                    'Normal sessions rebuilt; detection paused' if items else 'No normal sessions; learn a normal view')
        self.log.info('Baseline ready; frames=%s', self.model.baseline_frames)

    def save_event(self, jpeg, distances, threshold, score):
        name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(time.time_ns())
        (self.data / 'events' / (name + '.jpg')).write_bytes(jpeg)
        heatmap = overlay(jpeg, distances, threshold, roi=self.selected_roi)
        (self.data / 'events' / (name + '-heat.jpg')).write_bytes(heatmap)
        write_json(self.data / 'events' / (name + '.json'), dict(
            id=name, timestamp=time.time(), score=score, threshold=threshold,
            video_id=self.video_id, roi=self.selected_roi,
            image=name + '.jpg', heatmap=name + '-heat.jpg'))
        for expired in sorted((self.data / 'events').glob('*.json'))[:-MAX_EVENTS]:
            for suffix in ('.jpg', '-heat.jpg', '.json'):
                expired.with_name(expired.stem + suffix).unlink(missing_ok=True)
        self.event_count += 1
        self.log.info('Unusual view snapshot saved')

    def monitor_frame(self, state, jpeg, score, distances):
        threshold = self.model.threshold * self.multiplier
        abnormal, event = self.confirmation.update(score, threshold, time.monotonic())
        if score > threshold:
            if self.candidate_since is None:
                self.candidate_since = time.time()
        else:
            self.candidate_since = None
            self.video_id = None
        if abnormal:
            if self.video_id is None:
                self.video_id = (time.strftime(
                    '%Y%m%dT%H%M%SZ', time.gmtime(self.candidate_since))
                    + '-' + str(time.time_ns()))
            write_json(self.data / 'video-trigger.json', dict(
                id=self.video_id, start=self.candidate_since, last=time.time()))
        state.update(
            state='anomaly' if abnormal else 'suspect' if score > threshold else 'normal',
            score=score, threshold=threshold, ratio=score / threshold,
            message=('Unusual view detected' if abnormal else
                     'Confirming unusual view' if score > threshold else
                     'View matches normal baseline'))
        if event:
            self.save_event(jpeg, distances, threshold, score)
            state['event_count'] = self.event_count

    def analyze_frame(self, state, started):
        jpeg, token, fetch_ms = self.fetch_analysis_frame()
        state['fetch_ms'] = fetch_ms
        thumbnail = None
        if self.monitoring and not self.learning and self.gate.enabled:
            thumbnail = self.gate.thumbnail(jpeg, self.selected_roi)
            reused = self.gate.reusable(thumbnail)
            if reused is not None:
                self.validate_frame_token(token)
                self.gate.count(skipped=True)
                state.update(self.last_metrics, frame_skipped=True)
                self.monitor_frame(state, jpeg, *reused)
                state.update(self.gate.status())
                state['inference_ms'] = round((time.monotonic() - started) * 1000)
                return
        feature_started = time.monotonic()
        previous_source = self.baseline_source
        features = self.model.features(jpeg)
        geometry_changed = (self.monitoring and previous_source is not None
                            and previous_source != self.model.source_size)
        self.update_geometry(state)
        state['feature_ms'] = round((time.monotonic() - feature_started) * 1000)
        self.record_preparation_metrics(state)
        result = None
        if not self.learning and not geometry_changed:
            distance_started = time.monotonic()
            result = self.model.score(features)
            state['distance_ms'] = round((time.monotonic() - distance_started) * 1000)
        self.validate_frame_token(token)
        if geometry_changed:
            raise ROIGeometryChanged()
        if self.learning:
            try:
                self.sessions.append(jpeg, configuration(self.model))
            except ValueError as error:
                raise ROIGeometryChanged() from error
            except OSError:
                self.stop_learning()
                self.learning_error = 'Learning stopped: storage limit or free-space check failed. Saved sessions are retained.'
                raise
            self.collected += 1
            state.update(state='learning', collected=self.collected,
                         message='Learning current view as normal')
            if self.collected >= self.sample_count:
                self.finish_learning(state)
        else:
            self.gate.remember(thumbnail, result)
            self.gate.count(skipped=False)
            self.last_metrics = {key: state[key] for key in EVALUATION_METRICS if key in state}
            self.monitor_frame(state, jpeg, *result)
            state.update(self.gate.status(), frame_skipped=False)
        state['inference_ms'] = round((time.monotonic() - started) * 1000)

    def handle_geometry_change(self):
        self.stop_learning()
        self.invalidate_baseline(clear_sessions=True)
        self.reset_detection_candidate()
        self.clear_video_trigger()
        self.save_session()
        control.follow_detection(False, self.recording_mode, self.control_dir)
        self.state.update(
            state='untrained', updated=time.time(), baseline_ready=False,
            monitoring_enabled=False, retraining_required=True,
            message=('Source dimensions changed. Check the analysis area and learn it again '
                     'before detection.'))
        for key in ('score', 'threshold', 'ratio'):
            self.state.pop(key, None)
        self.update_geometry(self.state)
        self.publish_status()

    def handle_error(self, error):
        # Never include URLs, credentials or arbitrary exception text in status/logs.
        self.reset_detection_candidate()
        self.state.update(
            state='unavailable', updated=time.time(),
            message='Analysis unavailable; check camera and worker logs')
        for key in ('score', 'threshold', 'ratio'):
            self.state.pop(key, None)
        if (isinstance(error, urllib.error.HTTPError)
                and (error.headers or {}).get('X-Camera-Stream-Error') == '1'):
            self.state['message'] = (
                'Video stream error; damaged frames excluded from analysis and learning')
        self.publish_status()
        # A camera that stays unreachable would repeat the same line every retry.
        kind, logged_at = self._last_error
        if kind != type(error).__name__ or time.monotonic() - logged_at >= ERROR_LOG_INTERVAL_SECONDS:
            self.log.warning('Analysis iteration failed (%s); retrying', type(error).__name__)
            self._last_error = (type(error).__name__, time.monotonic())

    def migrate_legacy_baseline(self):
        """Rebuild the baseline from saved sessions once, then drop the old PyTorch files."""
        self.pending_migration = False
        self.rebuild_sessions(self.state, self.sessions.catalog()['sessions'],
                              resume=self.resume_after_migration)
        for name in LEGACY_BASELINES:
            (self.data / name).unlink(missing_ok=True)

    def step(self):
        started = time.monotonic()
        stopped = False
        try:
            stopped = self.control_dir is not None and (self.control_dir / 'camera-stopped').exists()
            if stopped and (self.learning or self.monitoring):
                write_json(self.data / 'command.json', dict(id=uuid.uuid4().hex, action='pause',
                           multiplier=self.multiplier))
            if self.pending_migration and not stopped:
                self.migrate_legacy_baseline()
            self.apply_pending_command()
            control.follow_detection(self.monitoring, self.recording_mode, self.control_dir)
            self.state = self.status_snapshot()
            if stopped:
                self.state['message'] = 'Camera stopped; saved learning is kept'
            elif self.learning or self.monitoring:
                self.analyze_frame(self.state, started)
            self.state['updated'] = time.time()
            self.publish_status()
        except ROIGeometryChanged:
            self.handle_geometry_change()
        except Exception as error:
            self.handle_error(error)
        return max(1 if stopped else 0, self.effective_interval() - (time.monotonic() - started))

    def run(self):
        while True:
            time.sleep(self.step())


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    worker = None
    while worker is None:
        try:
            worker = AnomalyWorker(exclusive=True)
        except WorkerLocked as error:
            # The shared analysis service (or another worker) owns this camera: wait instead
            # of exiting, so a restart policy cannot turn the conflict into a restart loop.
            LOG.warning('%s; waiting for it to stop', error)
            time.sleep(LOCK_RETRY_SECONDS)
    worker.run()


if __name__ == '__main__':
    main()
