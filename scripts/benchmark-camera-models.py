#!/usr/bin/env python3
"""Compare production camera models on saved JPEGs without changing live learning."""
import argparse
import gc
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cameras.model_profiles import MODELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True, type=Path)
    parser.add_argument('--phase', required=True, choices=('prepare', 'isolated', 'capture'))
    parser.add_argument('--size', default='1920x1080')
    parser.add_argument('--threads', type=int, default=2, choices=(1, 2, 3, 4))
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--models', nargs='+', choices=tuple(MODELS), default=list(MODELS))
    args = parser.parse_args()
    if args.samples < 10:
        parser.error('At least ten samples are required')
    from app.cameras.patchcore_lite import PatchCoreLite

    paths = sorted((args.work / 'frames').glob('*.jpg'))
    if len(paths) < 24:
        parser.error('Requires twenty training and four held-out frames')
    frames = [path.read_bytes() for path in paths]
    os.environ['CAMERA_ANOMALY_ONNX_PROVIDER'] = 'xnnpack'

    def model(key):
        return PatchCoreLite(input_size=args.size, threads=args.threads,
                             feature_engine='onnx', distance_engine='faiss', model_key=key)

    if args.phase == 'prepare':
        for key in args.models:
            print('PREPARE', key, flush=True)
            instance = model(key)
            bank = args.work / 'banks' / key
            bank.mkdir(parents=True, exist_ok=True)
            if (bank / 'baseline.npz').exists():
                instance.load(bank / 'baseline.npz')
                print('REUSE', key, flush=True)
                continue
            features = [instance.features(frame) for frame in frames[:20]]
            instance.fit(features)
            del features
            instance.save(bank / 'baseline.npz')
            print('BANK', key, list(instance.memory.shape), flush=True)
            del instance
            gc.collect()
        return

    instances = {}
    for key in args.models:
        instance = model(key)
        instance.load(args.work / 'banks' / key / 'baseline.npz')
        for _ in range(2):
            instance.score(instance.features(frames[-1]))
        instances[key] = instance

    def host_cpu():
        values = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        return sum(values), values[3] + values[4]

    def temperature():
        path = Path('/sys/class/thermal/thermal_zone0/temp')
        return float(path.read_text()) / 1000 if path.exists() else None

    rows = []
    keys = list(args.models)
    for trial, order in enumerate((keys, list(reversed(keys))), 1):
        for key in order:
            instance = instances[key]
            original_image = instance._image
            preparation = []

            def timed_image(jpeg):
                start = time.perf_counter()
                result = original_image(jpeg)
                preparation.append((time.perf_counter() - start) * 1000)
                return result

            instance._image = timed_image
            elapsed, networks, searches = [], [], []
            temp_start = temperature()
            host_start = host_cpu()
            cpu_start, wall_start = time.process_time(), time.perf_counter()
            for index in range(args.samples):
                jpeg = frames[20 + index % 4]
                start = time.perf_counter()
                patches = instance.features(jpeg)
                middle = time.perf_counter()
                score, heatmap = instance.score(patches)
                end = time.perf_counter()
                if not math.isfinite(score):
                    raise ValueError('Non-finite score')
                elapsed.append((end - start) * 1000)
                networks.append((middle - start) * 1000 - preparation[-1])
                searches.append((end - middle) * 1000)
            wall = time.perf_counter() - wall_start
            cpu_percent = (time.process_time() - cpu_start) / wall * 100
            host_end = host_cpu()
            instance._image = original_image
            row = dict(model=key, label=MODELS[key]['label'], trial=trial,
                       mean_ms=statistics.mean(elapsed), median_ms=statistics.median(elapsed),
                       p95_ms=sorted(elapsed)[math.ceil(.95 * len(elapsed)) - 1],
                       preparation_ms=statistics.mean(preparation), network_ms=statistics.mean(networks),
                       search_ms=statistics.mean(searches), cpu_percent=cpu_percent,
                       host_cpu_percent=100 * (1 - (host_end[1]-host_start[1]) / (host_end[0]-host_start[0])),
                       temperature_start=temp_start, temperature_end=temperature(),
                       provider=instance.onnx_provider, memory_vectors=len(instance.memory),
                       heatmap_shape=list(heatmap.shape), samples_ms=elapsed)
            rows.append(row)
            output = dict(phase=args.phase, input_size=args.size, threads=args.threads,
                          jpeg_bytes=[len(frame) for frame in frames[20:24]],
                          method='Production features()+score(); private trained 1024-vector banks; forward/reverse model order',
                          excludes='HTTP acquisition, anomaly confirmation, event image/video export',
                          results=rows)
            (args.work / (args.phase + '.json')).write_text(json.dumps(output, indent=2) + '\n')
            print(json.dumps({k: v for k, v in row.items() if k != 'samples_ms'}), flush=True)
    del instances
    gc.collect()


if __name__ == '__main__':
    main()
