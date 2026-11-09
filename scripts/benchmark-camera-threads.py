#!/usr/bin/env python3
"""Compare model CPU threads on one fixed JPEG and a read-only learned baseline."""
import argparse
import json
from pathlib import Path
import statistics
import time
import sys

import torch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cameras.patchcore_lite import PatchCoreLite


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, round((len(values)-1)*fraction))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame', required=True, type=Path)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--size', default='768x432')
    parser.add_argument('--samples', default=20, type=int)
    args = parser.parse_args()
    if args.samples < 5:
        parser.error('At least 5 samples required')
    model = PatchCoreLite(input_size=args.size)
    model.load(args.baseline)
    jpeg = args.frame.read_bytes()
    torch.set_num_threads(1)
    reference = model.features(jpeg)
    reference_score, reference_heat = model.score(reference)
    results = []
    for threads in (1, 2, 2, 1):
        torch.set_num_threads(threads)
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
        torch.testing.assert_close(features, reference, rtol=1e-4, atol=1e-4)
        torch.testing.assert_close(heat, reference_heat, rtol=1e-4, atol=1e-4)
        result = dict(threads=threads, mean_ms=statistics.mean(elapsed), median_ms=statistics.median(elapsed),
                      p95_ms=percentile(elapsed, .95), features_mean_ms=statistics.mean(features_ms),
                      score_mean_ms=statistics.mean(score_ms), cpu_percent=cpu,
                      score_delta=abs(score-reference_score), samples_ms=elapsed)
        results.append(result)
        args.output.write_text(json.dumps(results, indent=2)+'\n')
        print({k:v for k,v in result.items() if k!='samples_ms'}, flush=True)


if __name__ == '__main__':
    main()
