#!/usr/bin/env python3
"""Run 1/2/2/1 CPU-thread trials on the live Pi worker and restore Compose config."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
import signal
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '-f', str(ROOT / 'docker-compose.yml'),
           '-f', str(ROOT / 'docker-compose.override.yml'), '--profile', 'pi-camera']
STATUS = ROOT / 'runtime/pi-camera/anomaly/status.json'
BASELINE = ROOT / 'runtime/pi-camera/anomaly/baseline.pt'
spec = importlib.util.spec_from_file_location('camera_benchmark', ROOT / 'scripts/benchmark-cameras.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def apply(override=None):
    args = COMPOSE + (['-f', str(override)] if override else [])
    subprocess.run(args + ['up', '-d', '--no-deps', '--no-build', 'pi-camera-anomaly'], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def state():
    return json.loads(STATUS.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=int, default=30)
    args = parser.parse_args()
    if args.duration < 10:
        parser.error('Duration must be at least 10 seconds')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    initial = state()
    if initial.get('input_size') != '768x432' or initial.get('state') not in ('normal', 'suspect', 'anomaly'):
        raise SystemExit('Requires trained, active detection at 768x432')
    initial_digest = hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    (output / 'live-initial.json').write_text(json.dumps(initial, indent=2))
    results = []
    override = output / 'trial-compose.json'
    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        for threads in (1, 2, 2, 1):
            override.write_text(json.dumps({'services': {'pi-camera-anomaly': {
                'cpus': float(threads), 'environment': {'CAMERA_ANOMALY_THREADS': str(threads)}}}}))
            print('START', threads, 'threads', flush=True)
            old_updated = state().get('updated')
            apply(override)
            deadline = time.monotonic() + 45
            while True:
                current = state()
                if (current.get('updated') != old_updated and current.get('inference_threads') == threads
                        and current.get('state') in ('normal', 'suspect', 'anomaly')
                        and current.get('inference_ms') is not None):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Worker did not become ready')
                time.sleep(.2)
            time.sleep(5)
            pid = int(subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Pid}}',
                'bms-et-sensors-pi-camera-anomaly-1'], text=True))
            group_path = next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::'))
            group = Path('/sys/fs/cgroup') / group_path.lstrip('/')
            expected_request = state().get('last_request')
            cpu_start, start = benchmark.cpu_usage(group), time.monotonic()
            samples, last, memories = [], None, []
            while time.monotonic() - start < args.duration:
                current = state()
                if current.get('last_request') != expected_request or current.get('state') in ('learning', 'training'):
                    raise RuntimeError('Camera controls changed during measurement; repeat after learning')
                if current.get('updated') != last:
                    last = current.get('updated')
                    if current.get('inference_ms') is not None:
                        samples.append({key: current.get(key) for key in
                            ('updated', 'inference_ms', 'state', 'inference_threads', 'input_size')})
                    memories.append(benchmark.rss_bytes(group) / 1024**2)
                time.sleep(.05)
            elapsed = time.monotonic()-start
            valid = [s for s in samples if s['state'] in ('normal', 'suspect', 'anomaly')]
            timings = [s['inference_ms'] for s in valid]
            if len(valid) < 10:
                raise RuntimeError('Too few inference samples')
            result = dict(threads=threads, cpu_limit=float(threads), samples=samples,
                mean_ms=statistics.mean(timings), median_ms=statistics.median(timings),
                p95_ms=sorted(timings)[min(len(timings)-1, round(.95*(len(timings)-1)))],
                actual_fps=(len(valid)-1)/(valid[-1]['updated']-valid[0]['updated']),
                worker_cpu_percent=(benchmark.cpu_usage(group)-cpu_start)/1e6/elapsed*100,
                rss_mean_mib=statistics.mean(memories), invalid_samples=len(samples)-len(valid))
            results.append(result)
            (output / 'live.json').write_text(json.dumps(results, indent=2)+'\n')
            assert hashlib.sha256(BASELINE.read_bytes()).hexdigest() == initial_digest, 'Baseline changed during trials'
            print({key:value for key,value in result.items() if key!='samples'}, flush=True)
    finally:
        deadline = time.monotonic() + 240
        if state().get('state') in ('learning', 'training'):
            print('Waiting for active learning before restoring worker settings', flush=True)
        while state().get('state') in ('learning', 'training') and time.monotonic() < deadline:
            time.sleep(.5)
        apply()
        print('RESTORED normal Compose settings', flush=True)
    (output / 'baseline-unchanged.txt').write_text(initial_digest+'\n')


if __name__ == '__main__':
    main()
