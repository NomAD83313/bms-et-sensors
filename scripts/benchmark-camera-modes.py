#!/usr/bin/env python3
"""Benchmark IMX219 modes using the deployed Pi camera pipeline, then restore it."""
import argparse
import csv
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MODE = Path('/run/bms-pi-camera-mode.json')
MODES = [(1280, 720, 15), (1920, 1080, 15), (1640, 1232, 15),
         (3280, 2464, 15), (640, 480, 15), (1920, 1080, 30),
         (1640, 1232, 42), (640, 480, 30)]
COMPOSE = ['docker', 'compose', '-f', 'docker-compose.yml', '-f',
           'docker-compose.override.yml', '--profile', 'pi-camera']
spec = importlib.util.spec_from_file_location('camera_benchmark', ROOT / 'scripts/benchmark-cameras.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def set_mode(contents):
    if contents is None:
        run(['sudo', '-n', 'rm', '-f', str(MODE)])
    else:
        with tempfile.NamedTemporaryFile() as temporary:
            temporary.write(contents)
            temporary.flush()
            run(['sudo', '-n', 'install', '-m', '644', temporary.name, str(MODE)])
    run(['sudo', '-n', 'systemctl', 'restart', 'bms-pi-camera'])
    # Reopen consumers with fresh decoder state after changing sensor modes.
    run(COMPOSE + ['restart', 'pi-camera-recorder', 'pi-camera-dashboard'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def probe():
    args = ['ffprobe', '-v', 'error', '-rw_timeout', '10000000',
            '-read_intervals', '%+3', '-count_frames', '-show_frames',
            '-show_entries', 'stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_read_frames:frame=best_effort_timestamp_time',
            '-of', 'json', 'http://127.0.0.1:8084/stream.ts']
    started = time.monotonic()
    result = run(args, capture_output=True, text=True, timeout=25)
    data = json.loads(result.stdout)
    timestamps = [float(frame['best_effort_timestamp_time']) for frame in data.pop('frames', [])
                  if 'best_effort_timestamp_time' in frame]
    if len(timestamps) < 2 or timestamps[-1] <= timestamps[0]:
        raise RuntimeError('Not enough advancing video frames for FPS verification')
    return dict(elapsed_seconds=time.monotonic()-started,
                measured_fps=(len(timestamps)-1)/(timestamps[-1]-timestamps[0]), **data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=60)
    parser.add_argument('--warmup', type=int, default=15)
    args = parser.parse_args()
    if args.duration < 5 or args.warmup < 0:
        parser.error('duration >= 5 and warmup >= 0 required')
    benchmark.discover(['pi'])
    with urlopen('http://127.0.0.1:8084/api/config', timeout=5) as response:
        capture = json.load(response)
    if capture['capture_mode'] != 'video' or capture['shutter_us'] != 0:
        raise SystemExit('Select Video and Auto exposure before running mode benchmarks')
    old = MODE.read_bytes() if MODE.exists() else None
    output = ROOT / 'runtime/camera-mode-tests' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{time.time_ns()}')
    output.mkdir(parents=True)
    (output / 'original-mode.json').write_text(old.decode() if old else '{}\n')
    initial = benchmark.status('http://127.0.0.1:8083')
    (output / 'metadata.json').write_text(json.dumps(dict(duration=args.duration, warmup=args.warmup,
        initial=initial, modes=MODES, bitrate=2000000,
        note='Active application workload; existing detection baseline is retained. Changed crops can cause events. '
             'All modes use 2 Mbit/s; this does not guarantee equal image quality.'), indent=2))
    print('REPORT', output, flush=True)
    results = []
    child = None

    def interrupt(*_):
        if child is not None:
            child.terminate()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        for width, height, fps in MODES:
            name = f'{width}x{height}-{fps}fps'
            case = output / name
            case.mkdir()
            row = dict(mode=name, width=width, height=height, requested_fps=fps)
            print('START', name, flush=True)
            try:
                set_mode(json.dumps(dict(width=width, height=height, fps=fps, bitrate=2000000)).encode())
                row['probe'] = probe()
                row['measured_fps'] = row['probe']['measured_fps']
                stream = row['probe']['streams'][0]
                if (stream['width'], stream['height']) != (width, height):
                    raise RuntimeError('Encoded resolution does not match requested mode')
                before = set((ROOT / 'runtime/camera-benchmarks').iterdir())
                with (case / 'benchmark.log').open('w') as log:
                    child = subprocess.Popen([sys.executable, str(ROOT / 'scripts/benchmark-cameras.py'),
                        '--camera', 'pi', '--duration', str(args.duration), '--warmup', str(args.warmup),
                        '--interval', '5', '--preview'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                    code = child.wait()
                    child = None
                if code:
                    raise RuntimeError(f'Benchmark exited with {code}')
                created = set((ROOT / 'runtime/camera-benchmarks').iterdir()) - before
                if len(created) != 1:
                    raise RuntimeError('Cannot uniquely identify benchmark report')
                report = created.pop()
                row['report'] = str(report.relative_to(ROOT))
                row.update(json.loads((report / 'summary.json').read_text())['pi'])
                states = json.loads((report / 'states.json').read_text())
                meta = json.loads((report / 'metadata.json').read_text())
                row['preview_failures'] = sum(s['preview_requests'].get('pi') is not True for s in states)
                row['bad_preview_samples'] = sum(s['pi'].get('preview_quality', {}).get('state') != 'ok' for s in states)
                row['decoder_error_delta'] = states[-1]['pi']['preview_quality']['decoder_errors'] - meta['initial']['pi']['preview_quality']['decoder_errors']
                row['analysis_states'] = sorted({s['pi'].get('analysis_state', 'unknown') for s in states})
                row['status'] = 'completed'
                print('DONE', name, 'CPU', round(row['cpu_mean_percent'], 1), 'RSS', round(row['rss_mean_mib'], 1), flush=True)
            except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as exc:
                row.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
                print('FAILED', name, row['error'], flush=True)
            results.append(row)
            (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        set_mode(old)
        (output / 'restored.json').write_text(json.dumps(dict(mode_restored=True,
            status=benchmark.status('http://127.0.0.1:8083')), indent=2))
        print('RESTORED original source settings', flush=True)
    fields = ['mode', 'status', 'measured_fps', 'cpu_mean_percent', 'cpu_peak_percent', 'rss_mean_mib', 'rss_peak_mib',
              'preview_failures', 'bad_preview_samples', 'decoder_error_delta']
    with (output / 'summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)


if __name__ == '__main__':
    main()
