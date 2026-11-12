#!/usr/bin/env python3
"""Evaluate production encoders on a frozen, disjoint labelled scene dataset."""
import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def quality_metrics(predictions):
    positive = [p for p in predictions if p['kind'] == 'hand']
    negative = [p for p in predictions if p['kind'] == 'normal']
    if not positive or not negative:
        raise ValueError('Both positive and negative labels are required')
    episodes = {p['episode'] for p in positive}
    detected = {p['episode'] for p in positive if p['alarm']}
    tp = sum(p['alarm'] for p in positive)
    fp = sum(p['alarm'] for p in negative)
    auc = sum((a['score'] > b['score']) + .5 * (a['score'] == b['score'])
              for a in positive for b in negative) / (len(positive) * len(negative))
    return dict(events_detected=len(detected), events_total=len(episodes),
                hand_frames_detected=tp, hand_frames_total=len(positive),
                false_positives=fp, normal_frames=len(negative),
                recall=tp / len(positive), false_positive_rate=fp / len(negative),
                balanced_accuracy=.5 * (tp / len(positive) + 1 - fp / len(negative)),
                auroc=auc)


def validate_partitions(policy):
    groups = [policy['training'], policy['calibration'], [p['path'] for p in policy['test']]]
    hashes = [{hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in group} for group in groups]
    if any(a & b for i, a in enumerate(hashes) for b in hashes[i+1:]):
        raise ValueError('Training, calibration and test images must not overlap')


def rank_models(results, count=3):
    """Require 80% event recall and at most 5% false alarms before shortlisting."""
    ranked = sorted(results, key=lambda r: (
        -r['events_detected'], r['false_positives'], -r['hand_frames_detected'], r['mean_ms']))
    def dominates(a, b):
        left = (-a['events_detected'], a['false_positives'], -a['hand_frames_detected'], a['mean_ms'])
        right = (-b['events_detected'], b['false_positives'], -b['hand_frames_detected'], b['mean_ms'])
        return all(x <= y for x, y in zip(left, right)) and any(x < y for x, y in zip(left, right))
    eligible = [row for row in ranked if row['events_detected'] / row['events_total'] >= .8
                and row['false_positives'] / row['normal_frames'] <= .05]
    frontier = [row for row in eligible if not any(dominates(other, row) for other in eligible)]
    return ranked, [row['model'] for row in frontier[:count]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--policy', default='quality-policy.json',
                        help='Frozen dataset manifest, relative to --work')
    parser.add_argument('--prefix', default='quality',
                        help='Separate result and bank namespace for each scene')
    args = parser.parse_args()
    policy = json.loads((args.work / args.policy).read_text())
    validate_partitions(policy)
    summary_file = args.work / (args.prefix + '-results.json')
    if summary_file.exists():
        previous_policy = json.loads(summary_file.read_text())['policy']
        for field in ('input_size', 'threads', 'multiplier', 'models', 'roi', 'threshold_mode',
                      'training', 'calibration', 'test'):
            if field not in policy and field not in previous_policy:
                continue
            if previous_policy.get(field) != policy.get(field):
                parser.error('Dataset or settings changed; choose a new --prefix')
    import os
    os.environ['CAMERA_ANOMALY_ONNX_PROVIDER'] = 'xnnpack'
    from app.cameras.patchcore_lite import PatchCoreLite, calibrated_threshold
    from app.cameras.model_profiles import MODELS, validate_model
    from app.cameras.roi import validate_roi
    roi = validate_roi(policy.get('roi'))
    threshold_mode = policy.get('threshold_mode', 'separate-p99')
    if threshold_mode not in ('separate-p99', 'production-held-out'):
        parser.error('Policy threshold_mode must be separate-p99 or production-held-out')
    training = [Path(p).read_bytes() for p in policy['training']]
    calibration = [Path(p).read_bytes() for p in policy['calibration']]
    if threshold_mode == 'separate-p99' and not calibration:
        parser.error('Separate P99 threshold mode requires calibration frames')
    if threshold_mode == 'production-held-out' and calibration:
        parser.error('Production held-out threshold mode must not add calibration frames')
    test = list(policy['test'])
    random.Random(42).shuffle(test)
    output = dict(policy=policy, results=[])
    keys = policy.get('models', list(MODELS))
    if not isinstance(keys, list) or not keys or len(keys) != len(set(keys)):
        parser.error('Policy models must be a non-empty list without duplicates')
    try:
        keys = [validate_model(key) for key in keys]
    except ValueError as error:
        parser.error(str(error))
    for key in keys:
        result_file = args.work / (args.prefix + '-' + key + '.json')
        if result_file.exists():
            output['results'].append(json.loads(result_file.read_text()))
            print('REUSE', key, flush=True)
            continue
        print('PREPARE', key, flush=True)
        model = PatchCoreLite(model_key=key, input_size=policy['input_size'],
                              threads=policy['threads'], feature_engine='onnx', distance_engine='faiss')
        model.set_roi(roi)
        directory = args.work / (args.prefix + '-banks') / key
        directory.mkdir(parents=True, exist_ok=True)
        os.environ['CAMERA_ANOMALY_DIR'] = str(directory)
        features = [model.features(jpeg) for jpeg in training]
        model.fit(features)
        del features
        if threshold_mode == 'separate-p99':
            model.threshold = calibrated_threshold([model.score(model.features(jpeg))[0] for jpeg in calibration])
        model.save(directory / 'baseline.npz')
        threshold = model.threshold * policy['multiplier']
        print('EVALUATE', key, 'threshold', threshold, flush=True)
        warmup = calibration[0] if calibration else training[0]
        for _ in range(3):
            model.score(model.features(warmup))
        predictions, elapsed, preparation, neural, searches = [], [], [], [], []
        geometry, decoding, transforms, tensors = [], [], [], []
        original_image = model._image

        def timed_image(jpeg):
            started = time.perf_counter()
            result = original_image(jpeg)
            preparation.append((time.perf_counter() - started) * 1000)
            metrics = model.preparation_metrics
            geometry.append(metrics['geometry_ms'])
            decoding.append(metrics['decode_ms'])
            transforms.append(metrics['transform_ms'])
            tensors.append(metrics['tensor_ms'])
            return result

        model._image = timed_image
        cpu_start, wall_start = time.process_time(), time.perf_counter()
        for index, record in enumerate(test):
            jpeg = Path(record['path']).read_bytes()
            start = time.perf_counter()
            patches = model.features(jpeg)
            middle = time.perf_counter()
            score, _ = model.score(patches)
            end = time.perf_counter()
            if not math.isfinite(score):
                raise ValueError('Non-finite score')
            elapsed.append((end-start)*1000)
            neural.append((middle-start)*1000 - preparation[-1])
            searches.append((end-middle)*1000)
            predictions.append(dict(record, score=score, ratio=score/threshold, alarm=score > threshold))
            if (index+1) % 20 == 0:
                print('PROGRESS', key, index+1, '/', len(test), flush=True)
        row = dict(model=key, label=MODELS[key]['label'], **quality_metrics(predictions),
                   mean_ms=statistics.mean(elapsed), median_ms=statistics.median(elapsed),
                   p95_ms=sorted(elapsed)[math.ceil(.95*len(elapsed))-1],
                   preparation_ms=statistics.mean(preparation), network_ms=statistics.mean(neural),
                   geometry_ms=statistics.mean(geometry), decode_ms=statistics.mean(decoding),
                   transform_ms=statistics.mean(transforms), tensor_ms=statistics.mean(tensors),
                   features_with_preparation_ms=statistics.mean([left + right for left, right in
                                                                  zip(preparation, neural)]),
                   search_ms=statistics.mean(searches), roi=roi,
                   source_size=list(model.source_size) if model.source_size else None,
                   effective_input_size=list(model.effective_input_size),
                   threshold=threshold, base_threshold=model.threshold, multiplier=policy['multiplier'],
                   threshold_mode=threshold_mode,
                   cpu_percent=(time.process_time()-cpu_start)/(time.perf_counter()-wall_start)*100,
                   provider=model.onnx_provider, jpeg_draft=model.jpeg_draft, score_top_k=model.top_k,
                   configured_coreset_size=model.coreset_size, memory_vectors=len(model.memory),
                   decoded_size=[model.preparation_metrics['decoded_width'],
                                 model.preparation_metrics['decoded_height']],
                   predictions=predictions, samples_ms=elapsed)
        result_file.write_text(json.dumps(row, indent=2)+'\n')
        output['results'].append(row)
        (args.work / (args.prefix + '-results.json')).write_text(json.dumps(output, indent=2)+'\n')
        print('RESULT', json.dumps({k:v for k,v in row.items() if k not in ('predictions','samples_ms')}), flush=True)
        del model, patches
        gc.collect()
    output['ranking'], output['shortlist'] = rank_models(output['results'], policy['shortlist_count'])
    (args.work / (args.prefix + '-results.json')).write_text(json.dumps(output, indent=2)+'\n')
    print('SHORTLIST', output['shortlist'], flush=True)


if __name__ == '__main__':
    main()
