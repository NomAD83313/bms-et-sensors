"""Visual anomaly analysis and linked event-recording session control."""
import io
import json
import logging
import os
import time
import urllib.request
import urllib.error
from urllib.parse import quote

from PIL import Image
import control
from anomaly_control import DATA, Confirmation, read_json, write_json, validate_multiplier, command_mode
from patchcore_lite import PatchCoreLite, overlay

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
    model = PatchCoreLite()
    model_path = DATA / 'baseline.pt'
    if model_path.exists():
        model.load(model_path)
    previous = read_json(DATA / 'session.json', {})
    last_request = previous.get('last_request')
    monitoring = previous.get('monitoring', False) and model.memory is not None
    multiplier = previous.get('multiplier', 1.25)
    learning = False
    samples = []
    confirmation = Confirmation(required=confirmation_frames)
    candidate_since, video_id = None, None
    event_count = len(list((DATA / 'events').glob('*.json')))
    while True:
        started = time.monotonic()
        try:
            command = read_json(DATA / 'command.json', {})
            if command.get('id') and command['id'] != last_request:
                action = command.get('action')
                learning, monitoring = command_mode(action, model.memory is not None, learning, monitoring)
                multiplier = validate_multiplier(command.get('multiplier', 1.25))
                last_request = command['id']
                confirmation = Confirmation(required=confirmation_frames)
                candidate_since, video_id = None, None
                if action != 'configure':
                    samples = []
                    # A restarted session must not reuse an earlier event marker.
                    write_json(DATA / 'video-trigger.json', {})
                write_json(DATA / 'session.json', dict(last_request=last_request, monitoring=monitoring, multiplier=multiplier))
            control.follow_detection(monitoring, recording_mode)
            state = dict(updated=time.time(), model='PatchCore-style / ResNet-18',
                         baseline_ready=model.memory is not None, baseline_frames=model.baseline_frames,
                         monitoring_enabled=monitoring,
                         collected=len(samples), target=sample_count, multiplier=multiplier,
                         analysis_interval_sec=max(2, interval) if learning else interval, confirmation_frames=confirmation_frames,
                         event_count=event_count, state='paused' if model.memory is not None else 'untrained',
                         message='Analysis paused' if model.memory is not None else 'Learn a normal view to begin')
            if learning or monitoring:
                # Reuse the dashboard decoder and never consume a stale cached JPEG.
                with urllib.request.urlopen(preview + '?fresh=1', timeout=5) as response:
                    jpeg = response.read(4 * 1024 * 1024)
                    token = response.headers.get('X-Camera-Frame-Token')
                features = model.features(jpeg)
                if not learning:
                    score, distances = model.score(features)
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
                        model.fit(samples)
                        model.save(model_path)
                        learning, monitoring, samples = False, True, []
                        confirmation = Confirmation(required=confirmation_frames)
                        write_json(DATA / 'session.json', dict(last_request=last_request, monitoring=True, multiplier=multiplier))
                        control.follow_detection(True, recording_mode)
                        state.update(state='normal', baseline_ready=True, baseline_frames=model.baseline_frames, monitoring_enabled=True,
                                     message='Normal view learned; monitoring started')
                        LOG.info('Baseline ready; frames=%s', model.baseline_frames)
                else:
                    threshold = model.threshold * multiplier
                    abnormal, event = confirmation.update(score, threshold, time.monotonic())
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
                        # Small bounded snapshots; no video files are deleted or modified.
                        image = Image.open(io.BytesIO(jpeg)).convert('RGB')
                        image.thumbnail((960, 960))
                        image.save(DATA / 'events' / (name + '.jpg'), quality=85)
                        (DATA / 'events' / (name + '-heat.jpg')).write_bytes(overlay(jpeg, distances, threshold))
                        write_json(DATA / 'events' / (name + '.json'), dict(id=name, timestamp=time.time(), score=score,
                                   threshold=threshold, video_id=video_id, image=name + '.jpg', heatmap=name + '-heat.jpg'))
                        for expired in sorted((DATA / 'events').glob('*.json'))[:-100]:
                            for suffix in ('.jpg', '-heat.jpg', '.json'):
                                expired.with_name(expired.stem + suffix).unlink(missing_ok=True)
                        event_count += 1
                        state['event_count'] = event_count
                        LOG.info('Unusual view snapshot saved')
                state['inference_ms'] = round((time.monotonic() - started) * 1000)
            state['updated'] = time.time()
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
        time.sleep(max(.05, effective_interval - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
