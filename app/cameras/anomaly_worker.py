"""Visual anomaly analysis and linked event-recording session control."""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import control
from anomaly_control import (
    DATA, Confirmation, DEFAULT_INPUT_SIZE, DEFAULT_MODEL, MODELS,
    command_mode, heatmap_dimensions, input_dimensions, read_json,
    validate_confirmation_frames, validate_input_size, validate_model,
    validate_learning_seconds, validate_multiplier, write_json,
)
from patchcore_lite import PatchCoreLite, overlay
try:
    from .model_profiles import ANALYSIS_SOURCE
    from .roi import ROIGeometryChanged, validate_roi
except ImportError:
    from model_profiles import ANALYSIS_SOURCE
    from roi import ROIGeometryChanged, validate_roi

LOG = logging.getLogger('camera-anomaly')


class AnomalyWorker:
    def __init__(self):
        self.preview = os.environ.get(
            'CAMERA_ANOMALY_PREVIEW_URL', 'http://127.0.0.1:8082/api/live.jpg')
        self.recording_mode = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
        self.interval = max(.2, float(os.environ.get('CAMERA_ANOMALY_INTERVAL_SEC', '.2')))
        self.confirmation_frames = max(
            1, min(5, int(os.environ.get('CAMERA_ANOMALY_CONFIRM_FRAMES', '2'))))
        sample_count = max(
            20, min(120, int(os.environ.get('CAMERA_ANOMALY_NORMAL_FRAMES', '60'))))
        self.state = dict(state='loading', message='Loading visual model', updated=time.time())
        write_json(DATA / 'status.json', self.state)

        previous = read_json(DATA / 'session.json', {})
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

        try:
            selected_input_size = validate_input_size(
                previous.get('input_size', DEFAULT_INPUT_SIZE))
        except ValueError:
            selected_input_size = DEFAULT_INPUT_SIZE
            self.baseline_invalidated = configuration_reset = True
        try:
            selected_model = validate_model(previous.get('model_key', DEFAULT_MODEL))
        except ValueError:
            selected_model = DEFAULT_MODEL
            self.baseline_invalidated = configuration_reset = True
        try:
            self.selected_roi = validate_roi(previous.get('roi'))
        except ValueError:
            self.selected_roi = None
            self.baseline_invalidated = configuration_reset = True

        self.model = PatchCoreLite()
        if selected_input_size != getattr(self.model, 'input_size', DEFAULT_INPUT_SIZE):
            self.model.set_input_size(selected_input_size)
        if selected_model != getattr(self.model, 'model_key', DEFAULT_MODEL):
            self.model.set_model(selected_model)
        if self.selected_roi is not None:
            self.model.set_roi(self.selected_roi)

        self.model_path = DATA / 'baseline.pt'
        if self.baseline_invalidated:
            self.invalidate_baseline()
        elif self.model_path.exists():
            try:
                self.model.load(self.model_path)
            except ValueError:
                self.invalidate_baseline()
                configuration_reset = True
                LOG.warning('Baseline is incompatible; new learning required')

        self.last_request = previous.get('last_request')
        if configuration_reset:
            self.last_request = read_json(DATA / 'command.json', {}).get(
                'id', self.last_request)
        self.monitoring = previous.get('monitoring', False) and self.model.memory is not None
        self.multiplier = previous.get('multiplier', 1.25)
        self.learning = False
        self.samples = []
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.candidate_since = None
        self.video_id = None
        self.event_count = len(list((DATA / 'events').glob('*.json')))

        if configuration_reset:
            self.clear_video_trigger()
            self.save_session()

    def invalidate_baseline(self):
        self.baseline_invalidated = True
        self.model_path.unlink(missing_ok=True)

    def clear_video_trigger(self):
        write_json(DATA / 'video-trigger.json', {})

    def reset_detection_candidate(self):
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.candidate_since = None
        self.video_id = None

    def save_session(self):
        write_json(DATA / 'session.json', dict(
            last_request=self.last_request, monitoring=self.monitoring,
            multiplier=self.multiplier, input_size=self.model.input_size,
            model_key=getattr(self.model, 'model_key', DEFAULT_MODEL),
            roi=self.selected_roi, learning_seconds=self.learning_seconds,
            baseline_invalidated=self.baseline_invalidated,
            confirmation_frames=self.confirmation_frames,
            analysis_source=(ANALYSIS_SOURCE if not self.baseline_invalidated
                             and self.model.memory is not None else None)))

    def update_geometry(self, state):
        source_size = getattr(self.model, 'source_size', None)
        size = getattr(self.model, 'effective_input_size', None)
        if source_size is not None:
            state.update(source_width=source_size[0], source_height=source_size[1])
        width, height = size or input_dimensions(self.model.input_size)
        model_key = getattr(self.model, 'model_key', DEFAULT_MODEL)
        heat_width, heat_height = heatmap_dimensions(width, height, model_key)
        state.update(effective_input_width=width, effective_input_height=height,
                     heatmap_width=heat_width, heatmap_height=heat_height)

    def apply_pending_command(self):
        command = read_json(DATA / 'command.json', {})
        if not command.get('id') or command['id'] == self.last_request:
            return
        action = command.get('action')
        resized = (action == 'resize'
                   and validate_input_size(command.get('input_size')) != self.model.input_size)
        model_changed = (action == 'model'
                         and validate_model(command.get('model_key'))
                         != getattr(self.model, 'model_key', DEFAULT_MODEL))
        roi_changed = (action == 'roi'
                       and validate_roi(command.get('roi')) != self.selected_roi)
        if action not in ('resize', 'model', 'roi') or resized or model_changed or roi_changed:
            self.learning, self.monitoring = command_mode(
                action, self.model.memory is not None, self.learning, self.monitoring)
        if action == 'learn' and 'learning_seconds' in command:
            self.learning_seconds = validate_learning_seconds(command['learning_seconds'])
            self.sample_count = self.learning_seconds // 2
        if action == 'confirmation':
            self.confirmation_frames = validate_confirmation_frames(
                command.get('confirmation_frames'))
        elif action == 'resize' and resized:
            self.model.set_input_size(command['input_size'])
            self.invalidate_baseline()
        elif action == 'model' and model_changed:
            self.model.set_model(command['model_key'])
            self.invalidate_baseline()
        elif action == 'roi' and roi_changed:
            self.selected_roi = validate_roi(command['roi'])
            self.model.set_roi(self.selected_roi)
            self.invalidate_baseline()
        else:
            self.multiplier = validate_multiplier(command.get('multiplier', 1.25))
            if action == 'learn' and self.selected_roi is not None:
                # A deliberate new learning session accepts the current source geometry.
                self.model.set_input_size(self.model.input_size)
                self.invalidate_baseline()
        self.last_request = command['id']
        self.reset_detection_candidate()
        if (action not in ('configure', 'resize', 'confirmation', 'model', 'roi')
                or resized or model_changed or roi_changed):
            self.samples = []
            self.clear_video_trigger()
        self.save_session()

    def status_snapshot(self):
        width, height = input_dimensions(self.model.input_size)
        model_key = getattr(self.model, 'model_key', DEFAULT_MODEL)
        heat_width, heat_height = heatmap_dimensions(width, height, model_key)
        memory = getattr(self.model, 'memory', None)
        memory_shape = getattr(memory, 'shape', ())
        memory_vectors = (int(memory_shape[0]) if memory_shape
                          else getattr(self.model, 'coreset_size', 1024))
        state = dict(
            updated=time.time(), model='PatchCore-style / ' + MODELS[model_key]['label'],
            model_key=model_key, model_label=MODELS[model_key]['label'],
            baseline_ready=self.model.memory is not None,
            baseline_frames=self.model.baseline_frames,
            monitoring_enabled=self.monitoring, input_size=self.model.input_size,
            roi=self.selected_roi,
            inference_threads=getattr(self.model, 'inference_threads', 1),
            feature_engine=getattr(self.model, 'feature_engine', 'torch'),
            distance_engine=getattr(self.model, 'distance_engine', 'torch'),
            score_top_k=getattr(self.model, 'top_k', 1),
            coreset_size=memory_vectors,
            onnx_provider=getattr(self.model, 'onnx_provider', None),
            input_width=width, input_height=height,
            heatmap_width=heat_width, heatmap_height=heat_height,
            retraining_required=self.baseline_invalidated,
            analysis_source=ANALYSIS_SOURCE, last_request=self.last_request,
            collected=len(self.samples), target=self.sample_count,
            multiplier=self.multiplier, learning_seconds=self.learning_seconds,
            analysis_interval_sec=max(2, self.interval) if self.learning else self.interval,
            confirmation_frames=self.confirmation_frames,
            event_count=self.event_count,
            state='paused' if self.model.memory is not None else 'untrained',
            message=('Analysis paused' if self.model.memory is not None
                     else 'Learn a normal view to begin'))
        self.update_geometry(state)
        if self.baseline_invalidated:
            state['message'] = (
                'Model, area or analysis source changed. Learn normal view again before detection.')
        return state

    def fetch_analysis_frame(self):
        fetch_started = time.monotonic()
        with urllib.request.urlopen(self.preview + '?analysis=1', timeout=5) as response:
            jpeg = response.read(16 * 1024 * 1024 + 1)
            token = response.headers.get('X-Camera-Frame-Token')
            if response.headers.get('X-Camera-Analysis-Source') != ANALYSIS_SOURCE:
                raise ValueError('Full-frame analysis source is unavailable')
        if (len(jpeg) > 16 * 1024 * 1024 or not jpeg.startswith(b'\xff\xd8')
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
        preparation = getattr(self.model, 'preparation_metrics', {})
        if not preparation:
            return
        state.update(
            preparation_ms=round(preparation['total_ms']),
            jpeg_decode_ms=round(preparation['decode_ms']),
            image_transform_ms=round(preparation['transform_ms']),
            tensor_ms=round(preparation['tensor_ms']),
            encoder_ms=max(0, state['feature_ms'] - round(preparation['total_ms'])),
            jpeg_draft=getattr(self.model, 'jpeg_draft', False),
            decoded_width=preparation['decoded_width'],
            decoded_height=preparation['decoded_height'])

    def finish_learning(self, state):
        state.update(state='training', message='Building normal patch memory')
        write_json(DATA / 'status.json', state)
        done = threading.Event()

        def training_heartbeat():
            while not done.wait(2):
                write_json(DATA / 'status.json', dict(state, updated=time.time()))

        heartbeat = threading.Thread(target=training_heartbeat, daemon=True)
        heartbeat.start()
        try:
            self.model.fit(self.samples)
        finally:
            done.set()
            heartbeat.join()
        self.model.save(self.model_path)
        self.learning = False
        self.monitoring = True
        self.samples = []
        self.confirmation = Confirmation(required=self.confirmation_frames)
        self.baseline_invalidated = False
        self.save_session()
        control.follow_detection(True, self.recording_mode)
        state.update(
            state='normal', baseline_ready=True,
            baseline_frames=self.model.baseline_frames,
            monitoring_enabled=True, retraining_required=False,
            message='Normal view learned; monitoring started')
        LOG.info('Baseline ready; frames=%s', self.model.baseline_frames)

    def save_event(self, jpeg, distances, threshold, score):
        name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(time.time_ns())
        (DATA / 'events' / (name + '.jpg')).write_bytes(jpeg)
        heatmap = overlay(jpeg, distances, threshold, roi=self.selected_roi)
        (DATA / 'events' / (name + '-heat.jpg')).write_bytes(heatmap)
        write_json(DATA / 'events' / (name + '.json'), dict(
            id=name, timestamp=time.time(), score=score, threshold=threshold,
            video_id=self.video_id, roi=self.selected_roi,
            image=name + '.jpg', heatmap=name + '-heat.jpg'))
        for expired in sorted((DATA / 'events').glob('*.json'))[:-100]:
            for suffix in ('.jpg', '-heat.jpg', '.json'):
                expired.with_name(expired.stem + suffix).unlink(missing_ok=True)
        self.event_count += 1
        LOG.info('Unusual view snapshot saved')

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
            write_json(DATA / 'video-trigger.json', dict(
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
        feature_started = time.monotonic()
        features = self.model.features(jpeg)
        self.update_geometry(state)
        state['feature_ms'] = round((time.monotonic() - feature_started) * 1000)
        self.record_preparation_metrics(state)
        result = None
        if not self.learning:
            distance_started = time.monotonic()
            result = self.model.score(features)
            state['distance_ms'] = round((time.monotonic() - distance_started) * 1000)
        self.validate_frame_token(token)
        if self.learning:
            self.samples.append(features)
            state.update(state='learning', collected=len(self.samples),
                         message='Learning current view as normal')
            if len(self.samples) >= self.sample_count:
                self.finish_learning(state)
        else:
            self.monitor_frame(state, jpeg, *result)
        state['inference_ms'] = round((time.monotonic() - started) * 1000)

    def handle_geometry_change(self):
        self.learning = False
        self.monitoring = False
        self.samples = []
        self.invalidate_baseline()
        self.reset_detection_candidate()
        self.clear_video_trigger()
        self.save_session()
        control.follow_detection(False, self.recording_mode)
        self.state.update(
            state='untrained', updated=time.time(), baseline_ready=False,
            monitoring_enabled=False, retraining_required=True,
            message=('Source dimensions changed. Check the analysis area and learn it again '
                     'before detection.'))
        for key in ('score', 'threshold', 'ratio'):
            self.state.pop(key, None)
        self.update_geometry(self.state)
        write_json(DATA / 'status.json', self.state)

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
        write_json(DATA / 'status.json', self.state)
        LOG.warning('Analysis iteration failed (%s); retrying', type(error).__name__)

    def step(self):
        started = time.monotonic()
        try:
            self.apply_pending_command()
            control.follow_detection(self.monitoring, self.recording_mode)
            self.state = self.status_snapshot()
            if self.learning or self.monitoring:
                self.analyze_frame(self.state, started)
            self.state['updated'] = time.time()
            write_json(DATA / 'status.json', self.state)
        except ROIGeometryChanged:
            self.handle_geometry_change()
        except Exception as error:
            self.handle_error(error)
        effective_interval = max(2, self.interval) if self.learning else self.interval
        return max(0, effective_interval - (time.monotonic() - started))

    def run(self):
        while True:
            time.sleep(self.step())


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'events').mkdir(exist_ok=True)
    AnomalyWorker().run()


if __name__ == '__main__':
    main()
