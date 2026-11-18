#!/usr/bin/env python3
"""Compare model CPU threads on one fixed JPEG and a read-only learned baseline."""
import argparse
import gc
import json
import os
from pathlib import Path
import statistics
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cameras.model_profiles import INPUT_SIZES, MODELS
from app.cameras.roi import validate_roi


def parse_roi(value):
    try:
        return validate_roi(json.loads(value))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, round((len(values)-1)*fraction))]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--size', default='768x432', choices=INPUT_SIZES)
    parser.add_argument('--model', choices=tuple(MODELS), default='mobilenetv3')
    parser.add_argument('--roi', type=parse_roi, default=None, help='Normalized ROI as a JSON rectangle')
    parser.add_argument('--jpeg-draft', type=int, choices=(0, 1), default=0)
    parser.add_argument('--top-k', type=int, choices=range(1, 21), default=1)
    parser.add_argument('--threads', type=int, choices=(1, 2, 3, 4), nargs='+', default=[1, 2],
                        help='Thread counts, tested in forward and reverse order (default: 1 2)')
    parser.add_argument('--samples', default=20, type=int)
    args = parser.parse_args(argv)
    if args.samples < 5:
        parser.error('At least 5 samples required')
    if len(set(args.threads)) != len(args.threads):
        parser.error('Thread counts must be distinct')
    return args


def main():
    args = parse_args()
    import numpy as np
    from app.cameras.patchcore_lite import PatchCoreLite
    jpeg = args.frame.read_bytes()

    def load(threads):
        instance = PatchCoreLite(input_size=args.size, threads=threads, model_key=args.model,
                                 jpeg_draft=bool(args.jpeg_draft), top_k=args.top_k,
                                 feature_engine='onnx', distance_engine='faiss')
        instance.set_roi(args.roi)
        instance.load(args.baseline)
        return instance

    model = load(1)
    reference = model.features(jpeg)
    reference_score, reference_heat = model.score(reference)
    del model
    gc.collect()
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for threads in [*args.threads, *reversed(args.threads)]:
        model = load(threads)
        for _ in range(5):
            model.score(model.features(jpeg))
        elapsed, features_ms, score_ms = [], [], []
        cpu_started, wall_started = time.process_time(), time.perf_counter()
        for _ in range(args.samples):
            start = time.perf_counter()
            features = model.features(jpeg)
            middle = time.perf_counter()
            score, heat = model.score(features)
            end = time.perf_counter()
            features_ms.append((middle-start)*1000)
            score_ms.append((end-middle)*1000)
            elapsed.append((end-start)*1000)
        cpu = (time.process_time()-cpu_started)/(time.perf_counter()-wall_started)*100
        np.testing.assert_allclose(features, reference, rtol=1e-4, atol=1e-4, equal_nan=False)
        np.testing.assert_allclose(heat, reference_heat, rtol=1e-4, atol=1e-4, equal_nan=False)
        np.testing.assert_allclose(score, reference_score, rtol=1e-4, atol=1e-4, equal_nan=False)
        result = dict(threads=threads, mean_ms=statistics.mean(elapsed), median_ms=statistics.median(elapsed),
                      p95_ms=percentile(elapsed, .95), features_mean_ms=statistics.mean(features_ms),
                      score_mean_ms=statistics.mean(score_ms), cpu_percent=cpu,
                      score_delta=abs(score-reference_score), samples_ms=elapsed)
        result.update(model=args.model, input_size=args.size, roi=args.roi,
                      jpeg_draft=model.jpeg_draft, score_top_k=model.top_k,
                      provider=model.onnx_provider, source_size=list(model.source_size),
                      effective_input_size=list(model.effective_input_size),
                      cpu_affinity=sorted(os.sched_getaffinity(0)))
        results.append(result)
        args.output.write_text(json.dumps(results, indent=2)+'\n')
        print({k:v for k,v in result.items() if k!='samples_ms'}, flush=True)
        del model
        gc.collect()


if __name__ == '__main__':
    main()
