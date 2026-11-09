"""Visual anomaly analysis and linked event-recording session control."""
import json
import logging
import os
import time
import threading
import urllib.request
import urllib.error
from urllib.parse import quote

import control
from anomaly_control import DATA, Confirmation, read_json, write_json, validate_multiplier, validate_input_size, validate_confirmation_frames, command_mode, input_dimensions, DEFAULT_INPUT_SIZE, DEFAULT_MODEL, MODELS, validate_model, heatmap_dimensions
from patchcore_lite import PatchCoreLite, overlay
try:
    from .model_profiles import ANALYSIS_SOURCE
    from .roi import ROIGeometryChanged, validate_roi
except ImportError:
    from model_profiles import ANALYSIS_SOURCE
    from roi import ROIGeometryChanged, validate_roi

LOG = logging.getLogger('camera-anomaly')


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'events').mkdir(exist_ok=True)
    preview = os.environ.get('CAMERA_ANOMALY_PREVIEW_URL', 'http://127.0.0.1:8082/api/live.jpg')
    recording_mode = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
    interval = max(.2, float(os.environ.get('CAMERA_ANOMALY_INTERVAL_SEC', '.2')))
    confirmation_frames = max(1, min(5, int(os.environ.get('CAMERA_ANOMALY_CONFIRM_FRAMES', '2'))))
    sample_count = max(20, min(120, int(os.environ.get('CAMERA_ANOMALY_NORMAL_FRAMES', '60'))))
    state = dict(state='loading', message='Loading visual model', updated=time.time())
    write_json(DATA / 'status.json', state)
    previous = read_json(DATA / 'session.json', {})
    confirmation_frames = validate_confirmation_frames(previous.get('confirmation_frames', confirmation_frames))
    model = PatchCoreLite()
    baseline_invalidated = previous.get('baseline_invalidated', False)
    configuration_reset = False
    try:
        model.input_size = validate_input_size(previous.get('input_size', DEFAULT_INPUT_SIZE))
    except ValueError:
        model.input_size = DEFAULT_INPUT_SIZE
        baseline_invalidated = configuration_reset = True
    try:
        selected_model = validate_model(previous.get('model_key', DEFAULT_MODEL))
    except ValueError:
        selected_model = DEFAULT_MODEL
        baseline_invalidated = configuration_reset = True
    if selected_model != getattr(model, 'model_key', DEFAULT_MODEL):
        model.set_model(selected_model)
    try:
        selected_roi = validate_roi(previous.get('roi'))
    except ValueError:
        selected_roi = None
        baseline_invalidated = configuration_reset = True
    if selected_roi is not None:
        model.set_roi(selected_roi)
    model_path = DATA / 'baseline.pt'
    if model_path.exists() and not baseline_invalidated:
        try:
            model.load(model_path)
        except ValueError:
            baseline_invalidated = configuration_reset = True
            LOG.warning('Baseline is incompatible; new learning required')
    last_request = previous.get('last_request')
    if configuration_reset:
        last_request = read_json(DATA / 'command.json', {}).get('id', last_request)
    monitoring = previous.get('monitoring', False) and model.memory is not None
    multiplier = previous.get('multiplier', 1.25)
    learning = False
    samples = []
    confirmation = Confirmation(required=confirmation_frames)
    candidate_since, video_id = None, None
    event_count = len(list((DATA / 'events').glob('*.json')))
    def save_session():
        write_json(DATA / 'session.json', dict(last_request=last_request, monitoring=monitoring,
                   multiplier=multiplier, input_size=model.input_size,
                   model_key=getattr(model, 'model_key', DEFAULT_MODEL),
                   roi=selected_roi,
                   baseline_invalidated=baseline_invalidated, confirmation_frames=confirmation_frames,
                   analysis_source=ANALYSIS_SOURCE if not baseline_invalidated and model.memory is not None else None))

    def update_geometry(state):
        source_size = getattr(model, 'source_size', None)
        size = getattr(model, 'effective_input_size', None)
        if source_size is not None:
            state.update(source_width=source_size[0], source_height=source_size[1])
        width, height = size or input_dimensions(model.input_size)
        heat_width, heat_height = heatmap_dimensions(width, height, getattr(model, 'model_key', DEFAULT_MODEL))
        state.update(effective_input_width=width, effective_input_height=height,
                     heatmap_width=heat_width, heatmap_height=heat_height)

    if configuration_reset:
        write_json(DATA / 'video-trigger.json', {})
        save_session()

    while True:
        started = time.monotonic()
        try:
            command = read_json(DATA / 'command.json', {})
            if command.get('id') and command['id'] != last_request:
                action = command.get('action')
                resized = action == 'resize' and validate_input_size(command.get('input_size')) != model.input_size
                model_changed = action == 'model' and validate_model(command.get('model_key')) != getattr(model, 'model_key', DEFAULT_MODEL)
                roi_changed = action == 'roi' and validate_roi(command.get('roi')) != selected_roi
                if action not in ('resize', 'model', 'roi') or resized or model_changed or roi_changed:
                    learning, monitoring = command_mode(action, model.memory is not None, learning, monitoring)
                if action == 'confirmation':
                    confirmation_frames = validate_confirmation_frames(command.get('confirmation_frames'))
                elif action == 'resize':
                    if resized:
                        model.set_input_size(command['input_size'])
                        baseline_invalidated = True
                elif action == 'model':
                    if model_changed:
                        model.set_model(command['model_key'])
                        baseline_invalidated = True
                elif action == 'roi':
                    if roi_changed:
                        selected_roi = validate_roi(command['roi'])
                        model.set_roi(selected_roi)
                        baseline_invalidated = True
                else:
                    multiplier = validate_multiplier(command.get('multiplier', 1.25))
                    if action == 'learn' and selected_roi is not None:
                        # A deliberate new learning session accepts the current source geometry.
                        model.set_input_size(model.input_size)
                        baseline_invalidated = True
                last_request = command['id']
                confirmation = Confirmation(required=confirmation_frames)
                candidate_since, video_id = None, None
                if action not in ('configure', 'resize', 'confirmation', 'model', 'roi') or resized or model_changed or roi_changed:
                    samples = []
                    # A restarted session must not reuse an earlier event marker.
                    write_json(DATA / 'video-trigger.json', {})
                save_session()
            control.follow_detection(monitoring, recording_mode)
            width, height = input_dimensions(model.input_size)
            model_key = getattr(model, 'model_key', DEFAULT_MODEL)
            heat_width, heat_height = heatmap_dimensions(width, height, model_key)
            memory = getattr(model, 'memory', None)
            memory_shape = getattr(memory, 'shape', ())
            memory_vectors = (int(memory_shape[0]) if memory_shape
                              else getattr(model, 'coreset_size', 1024))
            state = dict(updated=time.time(), model='PatchCore-style / ' + MODELS[model_key]['label'],
                         model_key=model_key, model_label=MODELS[model_key]['label'],
                         baseline_ready=model.memory is not None, baseline_frames=model.baseline_frames,
                         monitoring_enabled=monitoring, input_size=model.input_size,
                         roi=selected_roi,
                         inference_threads=getattr(model, 'inference_threads', 1),
                         feature_engine=getattr(model, 'feature_engine', 'torch'),
                         distance_engine=getattr(model, 'distance_engine', 'torch'),
                         score_top_k=getattr(model, 'top_k', 1),
                         coreset_size=memory_vectors,
                         onnx_provider=getattr(model, 'onnx_provider', None),
                         input_width=width, input_height=height,
                         heatmap_width=heat_width, heatmap_height=heat_height,
                         retraining_required=baseline_invalidated,
                         analysis_source=ANALYSIS_SOURCE,
                         last_request=last_request,
                         collected=len(samples), target=sample_count, multiplier=multiplier,
                         analysis_interval_sec=max(2, interval) if learning else interval, confirmation_frames=confirmation_frames,
                         event_count=event_count, state='paused' if model.memory is not None else 'untrained',
                         message='Analysis paused' if model.memory is not None else 'Learn a normal view to begin')
            update_geometry(state)
            if baseline_invalidated:
                state['message'] = 'Model, area or analysis source changed. Learn normal view again before detection.'
            if learning or monitoring:
                # Resize the full source directly in the model, never an upscaled preview.
                fetch_started = time.monotonic()
                with urllib.request.urlopen(preview + '?fresh=1&analysis=1', timeout=5) as response:
                    jpeg = response.read(16 * 1024 * 1024 + 1)
                    token = response.headers.get('X-Camera-Frame-Token')
                    if response.headers.get('X-Camera-Analysis-Source') != ANALYSIS_SOURCE:
                        raise ValueError('Full-frame analysis source is unavailable')
                if (len(jpeg) > 16 * 1024 * 1024 or not jpeg.startswith(b'\xff\xd8')
                        or not jpeg.endswith(b'\xff\xd9')):
                    raise ValueError('Full analysis frame is incomplete')
                state['fetch_ms'] = round((time.monotonic() - fetch_started) * 1000)
                feature_started = time.monotonic()
                features = model.features(jpeg)
                update_geometry(state)
                state['feature_ms'] = round((time.monotonic() - feature_started) * 1000)
                preparation = getattr(model, 'preparation_metrics', {})
                if preparation:
                    state.update(preparation_ms=round(preparation['total_ms']),
                                 jpeg_decode_ms=round(preparation['decode_ms']),
                                 image_transform_ms=round(preparation['transform_ms']),
                                 tensor_ms=round(preparation['tensor_ms']),
                                 encoder_ms=max(0, state['feature_ms'] - round(preparation['total_ms'])),
                                 jpeg_draft=getattr(model, 'jpeg_draft', False),
                                 decoded_width=preparation['decoded_width'],
                                 decoded_height=preparation['decoded_height'])
                if not learning:
                    distance_started = time.monotonic()
                    score, distances = model.score(features)
                    state['distance_ms'] = round((time.monotonic() - distance_started) * 1000)
                if token:
                    # Errors arriving while inference ran invalidate the earlier JPEG too.
                    with urllib.request.urlopen(preview + '?check=' + quote(token, safe=''), timeout=2) as response:
                        if json.loads(response.read(4096)).get('valid') is not True:
                            raise ValueError('Preview frame invalidated')
                if learning:
                    samples.append(features)
                    state.update(state='learning', collected=len(samples), message='Learning current view as normal')
                    if len(samples) >= sample_count:
                        state.update(state='training', message='Building normal patch memory')
                        write_json(DATA / 'status.json', state)
                        done = threading.Event()
                        def training_heartbeat():
                            while not done.wait(2):
                                write_json(DATA / 'status.json', dict(state, updated=time.time()))
                        heartbeat = threading.Thread(target=training_heartbeat, daemon=True)
                        heartbeat.start()
                        try:
                            model.fit(samples)
                        finally:
                            done.set()
                            heartbeat.join()
                        model.save(model_path)
                        learning, monitoring, samples = False, True, []
                        confirmation = Confirmation(required=confirmation_frames)
                        baseline_invalidated = False
                        save_session()
                        control.follow_detection(True, recording_mode)
                        state.update(state='normal', baseline_ready=True, baseline_frames=model.baseline_frames, monitoring_enabled=True, retraining_required=False,
                                     message='Normal view learned; monitoring started')
                        LOG.info('Baseline ready; frames=%s', model.baseline_frames)
                else:
                    threshold = model.threshold * multiplier
                    abnormal, event = confirmation.update(score, threshold, time.monotonic())
                    event_jpeg = jpeg
                    if score > threshold:
                        if candidate_since is None:
                            candidate_since = time.time()
                    else:
                        candidate_since, video_id = None, None
                    if abnormal:
                        if video_id is None:
                            video_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(candidate_since)) + '-' + str(time.time_ns())
                        write_json(DATA / 'video-trigger.json', dict(id=video_id, start=candidate_since, last=time.time()))
                    state.update(state='anomaly' if abnormal else 'suspect' if score > threshold else 'normal',
                                 score=score, threshold=threshold, ratio=score / threshold,
                                 message='Unusual view detected' if abnormal else 'Confirming unusual view' if score > threshold else 'View matches normal baseline')
                    if event:
                        name = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(time.time_ns())
                        # Preserve source dimensions and JPEG quality; retention stays bounded.
                        (DATA / 'events' / (name + '.jpg')).write_bytes(event_jpeg)
                        heatmap = (overlay(event_jpeg, distances, threshold, roi=selected_roi)
                                   if selected_roi is not None else overlay(event_jpeg, distances, threshold))
                        (DATA / 'events' / (name + '-heat.jpg')).write_bytes(heatmap)
                        write_json(DATA / 'events' / (name + '.json'), dict(id=name, timestamp=time.time(), score=score,
                                   threshold=threshold, video_id=video_id, roi=selected_roi,
                                   image=name + '.jpg', heatmap=name + '-heat.jpg'))
                        for expired in sorted((DATA / 'events').glob('*.json'))[:-100]:
                            for suffix in ('.jpg', '-heat.jpg', '.json'):
                                expired.with_name(expired.stem + suffix).unlink(missing_ok=True)
                        event_count += 1
                        state['event_count'] = event_count
                        LOG.info('Unusual view snapshot saved')
                state['inference_ms'] = round((time.monotonic() - started) * 1000)
            state['updated'] = time.time()
            write_json(DATA / 'status.json', state)
        except ROIGeometryChanged:
            learning = monitoring = False
            samples = []
            baseline_invalidated = True
            confirmation = Confirmation(required=confirmation_frames)
            candidate_since, video_id = None, None
            write_json(DATA / 'video-trigger.json', {})
            save_session()
            control.follow_detection(False, recording_mode)
            state.update(state='untrained', updated=time.time(), baseline_ready=False,
                         monitoring_enabled=False, retraining_required=True,
                         message='Source dimensions changed. Check the analysis area and learn it again before detection.')
            for key in ('score', 'threshold', 'ratio'):
                state.pop(key, None)
            update_geometry(state)
            write_json(DATA / 'status.json', state)
        except Exception as error:
            # Never include URLs, credentials or arbitrary exception text in status/logs.
            confirmation = Confirmation(required=confirmation_frames)
            candidate_since, video_id = None, None
            state.update(state='unavailable', updated=time.time(), message='Analysis unavailable; check camera and worker logs')
            state.pop('score', None)
            state.pop('threshold', None)
            state.pop('ratio', None)
            if isinstance(error, urllib.error.HTTPError) and (error.headers or {}).get('X-Camera-Stream-Error') == '1':
                state['message'] = 'Video stream error; damaged frames excluded from analysis and learning'
            write_json(DATA / 'status.json', state)
            LOG.warning('Analysis iteration failed (%s); retrying', type(error).__name__)
        # Keep normal-view collection spread over two minutes, even with faster detection.
        effective_interval = max(2, interval) if learning else interval
        time.sleep(max(0, effective_interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
