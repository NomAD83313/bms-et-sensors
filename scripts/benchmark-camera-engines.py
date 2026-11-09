#!/usr/bin/env python3
"""Measure inference engines on a fixed JPEG without changing the live baseline."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cameras.patchcore_lite import PatchCoreLite
from app.cameras.feature_encoder import ENCODER_ID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--size', default='1280x720')
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--providers', nargs='+', default=['cpu'])
    parser.add_argument('--threads', type=int, nargs='+', default=[1, 2, 3, 4])
    args = parser.parse_args()
    if (args.samples < 5 or any(provider not in ('cpu', 'xnnpack') for provider in args.providers)
            or any(threads not in (1, 2, 3, 4) for threads in args.threads)):
        parser.error('Use at least five samples and cpu/xnnpack providers')
    jpeg = args.frame.read_bytes()
    reference = PatchCoreLite(input_size=args.size, threads=1,
                              feature_engine='torch', distance_engine='torch')
    patches = reference.features(jpeg)
    generator = torch.Generator().manual_seed(42)
    # A fixed 1024-patch test bank measures compute cost, not detection quality.
    reference.memory = patches[torch.randperm(len(patches), generator=generator)[:1024]].contiguous() + .1
    reference_score, reference_heatmap = reference.score(patches)
    rows = []
    cases = [('torch', 'torch', 'cpu', 1)]
    cases += [('onnx', 'faiss', provider, threads)
              for provider in args.providers for threads in args.threads]
    metadata = dict(encoder=ENCODER_ID, input_size=args.size, memory_vectors=1024,
                    openblas_threads=os.environ.get('OPENBLAS_NUM_THREADS'),
                    method='Offline fixed-frame compute test; single-frame test bank; no live baseline changed',
                    results=rows)
    for feature_engine, distance_engine, provider, threads in cases:
        os.environ['CAMERA_ANOMALY_ONNX_PROVIDER'] = provider
        model = PatchCoreLite(input_size=args.size, threads=threads,
                              feature_engine=feature_engine, distance_engine=distance_engine)
        model.memory = reference.memory
        for _ in range(3):
            model.score(model.features(jpeg))
        elapsed, features_ms, distances_ms = [], [], []
        cpu_started, wall_started = time.process_time(), time.perf_counter()
        for _ in range(args.samples):
            started = time.perf_counter()
            features = model.features(jpeg)
            middle = time.perf_counter()
            score, heatmap = model.score(features)
            ended = time.perf_counter()
            elapsed.append((ended - started) * 1000)
            features_ms.append((middle - started) * 1000)
            distances_ms.append((ended - middle) * 1000)
        cpu_percent = (time.process_time() - cpu_started) / (time.perf_counter() - wall_started) * 100
        torch.testing.assert_close(features, patches, rtol=1e-4, atol=1e-4)
        torch.testing.assert_close(heatmap, reference_heatmap, rtol=1e-4, atol=1e-4)
        row = dict(feature_engine=feature_engine, distance_engine=distance_engine,
                   provider=model.onnx_provider, threads=threads,
                   median_ms=statistics.median(elapsed), mean_ms=statistics.mean(elapsed),
                   p95_ms=sorted(elapsed)[min(len(elapsed) - 1, round(.95 * (len(elapsed) - 1)))],
                   features_ms=statistics.mean(features_ms), distances_ms=statistics.mean(distances_ms),
                   cpu_percent=cpu_percent, score_delta=abs(score - reference_score), samples_ms=elapsed)
        rows.append(row)
        args.output.write_text(json.dumps(metadata, indent=2) + '\n')
        print(json.dumps({key: value for key, value in row.items() if key != 'samples_ms'}), flush=True)


if __name__ == '__main__':
    main()
