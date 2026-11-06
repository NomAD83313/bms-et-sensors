#!/usr/bin/env python3
"""Compare experimental ONNX quantization without changing the production model."""
import argparse
from collections import Counter
import io
import json
import os
from pathlib import Path
import statistics
import time

import faiss
import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_dynamic, quantize_static
from onnxruntime.quantization.shape_inference import quant_pre_process
from PIL import Image
import torch
from torchvision.transforms import functional as TF


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--size', default='1280x720')
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.samples < 5 or args.threads not in (1, 2, 3, 4):
        parser.error('Use at least five samples and 1–4 threads')
    frames = sorted(args.frames.glob('*.jpg'))
    if len(frames) < 20:
        parser.error('At least twenty calibration images required')
    width, height = map(int, args.size.split('x'))
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    faiss.omp_set_num_threads(args.threads)
    ort.disable_telemetry_events()

    def tensor(jpeg):
        image = TF.resize(Image.open(io.BytesIO(jpeg)).convert('RGB'), [height, width])
        return TF.normalize(TF.to_tensor(image), [.485, .456, .406], [.229, .224, .225]).unsqueeze(0).numpy()

    class Reader(CalibrationDataReader):
        def __init__(self): self.frames = iter(frames)
        def get_next(self):
            path = next(self.frames, None)
            return None if path is None else {'image': tensor(path.read_bytes())}

    original = Path(os.environ.get('CAMERA_ANOMALY_MODEL_DIR', '/opt/bms-camera/encoders')) / f'resnet18-layer2-v1-{args.size}.onnx'
    prepared = directory / 'prepared.onnx'
    quant_pre_process(str(original), str(prepared), skip_optimization=True)
    results = dict(input_size=args.size, threads=args.threads, calibration_frames=len(frames),
                   method='Experimental compute and feature-drift test; unlabelled calibration frames; no production changes',
                   results=[])
    jpeg = frames[-1].read_bytes()
    reference = None
    index = None
    for mode in ('fp32', 'dynamic-quint8', 'static-qdq-int8'):
        model_path = original if mode == 'fp32' else directory / f'{mode}.onnx'
        try:
            if mode == 'dynamic-quint8':
                quantize_dynamic(str(prepared), str(model_path), weight_type=QuantType.QUInt8,
                                 op_types_to_quantize=['Conv'])
            elif mode == 'static-qdq-int8':
                quantize_static(str(prepared), str(model_path), Reader(),
                                quant_format=QuantFormat.QDQ, activation_type=QuantType.QInt8,
                                weight_type=QuantType.QInt8, per_channel=True,
                                extra_options={'ActivationSymmetric': True, 'WeightSymmetric': True})
            options = ort.SessionOptions()
            options.intra_op_num_threads = args.threads
            options.inter_op_num_threads = 1
            options.add_session_config_entry('session.intra_op.allow_spinning', '0')
            session = ort.InferenceSession(str(model_path), options, providers=['CPUExecutionProvider'])
            for _ in range(3):
                features = session.run(None, {'image': tensor(jpeg)})[0]
            if reference is None:
                reference = features.copy()
                index = faiss.IndexFlatL2(128)
                order = np.random.default_rng(42).permutation(len(reference))[:1024]
                index.add(np.ascontiguousarray(reference[order] + .1))
            times, network_times, distance_times = [], [], []
            cpu_started, wall_started = time.process_time(), time.perf_counter()
            for _ in range(args.samples):
                started = time.perf_counter()
                frame = tensor(jpeg)
                ready = time.perf_counter()
                features = session.run(None, {'image': frame})[0]
                encoded = time.perf_counter()
                index.search(features, 1)
                ended = time.perf_counter()
                times.append((ended - started) * 1000)
                network_times.append((encoded - ready) * 1000)
                distance_times.append((ended - encoded) * 1000)
            row = dict(mode=mode, provider=session.get_providers()[0],
                       median_ms=statistics.median(times), mean_ms=statistics.mean(times),
                       network_mean_ms=statistics.mean(network_times), distance_mean_ms=statistics.mean(distance_times),
                       cpu_percent=(time.process_time()-cpu_started)/(time.perf_counter()-wall_started)*100,
                       feature_mean_absolute_error=float(np.mean(np.abs(features-reference))),
                       feature_relative_rmse=float(np.sqrt(np.mean((features-reference)**2)/max(float(np.mean(reference**2)), 1e-12))),
                       operators=dict(Counter(node.op_type for node in onnx.load(str(model_path)).graph.node)),
                       samples_ms=times)
        except Exception as error:
            row = dict(mode=mode, error_type=type(error).__name__, error=str(error))
        results['results'].append(row)
        (directory / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({key: value for key, value in row.items() if key != 'samples_ms'}), flush=True)


if __name__ == '__main__':
    main()
