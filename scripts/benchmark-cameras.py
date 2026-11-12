#!/usr/bin/env python3
"""Measure camera workloads without changing recording or detection settings."""
import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CGROUP = Path('/sys/fs/cgroup')
# Every camera runs inside camera-media and is analysed by camera-analysis, so the camera
# stack is measured as a whole; camera-hub is included when it runs.
SERVICES = ['camera-media', 'camera-analysis']
OPTIONAL_SERVICES = ['camera-hub']
MEDIA_URL = 'http://127.0.0.1:8082'
FIELDS = ('service', 'recording_mode', 'recording_enabled', 'analysis_state',
          'monitoring_enabled', 'camera_resolution', 'preview_quality')


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def cgroup_of(pid):
    return CGROUP / next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines()
                         if line.startswith('0::')).lstrip('/')


def discover(include_pi_source):
    """Return (component, cgroup) for the running camera containers and the Pi host source."""
    targets = []
    for service in SERVICES + OPTIONAL_SERVICES:
        result = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}} {{.State.Pid}}', service],
                                cwd=ROOT, capture_output=True, text=True)
        running, _, pid = result.stdout.strip().partition(' ')
        if running == 'true':
            targets.append((service, cgroup_of(int(pid))))
        elif service in SERVICES:
            raise RuntimeError(f'{service} is not running; start it with ./scripts/restart-cameras.sh')
    if include_pi_source:
        host_group = run('systemctl', 'show', 'bms-pi-camera', '--property=ControlGroup', '--value')
        if not host_group or host_group == '/':
            raise RuntimeError('bms-pi-camera host source is not running')
        targets.append(('pi-host-source', CGROUP / host_group.lstrip('/')))
    return targets


def cameras():
    """Camera IDs and whether one is the local Pi camera, from camera-media."""
    with urlopen(MEDIA_URL + '/api/cameras', timeout=4) as response:
        listed = json.load(response)['cameras']
    return [camera['id'] for camera in listed if camera['enabled']], any(
        camera['source_kind'] == 'local' and camera['enabled'] for camera in listed)


def cpu_usage(group):
    values = dict(line.split() for line in (group / 'cpu.stat').read_text().splitlines())
    return int(values['usage_usec'])


def rss_bytes(group):
    """Use the same summed process RSS metric even without memory cgroups."""
    pids = set()
    for path in group.rglob('cgroup.procs'):
        pids.update(path.read_text().split())
    if not pids:
        raise RuntimeError(f'No processes in {group}')
    total = 0
    for pid in pids:
        try:
            lines = Path(f'/proc/{pid}/status').read_text().splitlines()
            total += next((int(line.split()[1]) * 1024 for line in lines if line.startswith('VmRSS:')), 0)
        except FileNotFoundError:
            pass
    return total


def status(base):
    try:
        with urlopen(base + '/api/status', timeout=4) as response:
            data = json.load(response)
        return {key: data.get(key) for key in FIELDS}
    except (OSError, ValueError) as exc:
        return {'error': type(exc).__name__}


def request_preview(base):
    try:
        with urlopen(base + '/api/live.jpg', timeout=3) as response:
            response.read(4 * 1024 * 1024)
        return True
    except OSError:
        return False


def summarize(rows):
    """Mean and peak of the per-sample totals over all components (100% = one core)."""
    totals = {}
    for row in rows:
        pair = totals.setdefault(row['sample'], [0, 0])
        pair[0] += row['cpu_percent']
        pair[1] += row['rss_mib']
    values = list(totals.values())
    return dict(cpu_mean_percent=statistics.mean(v[0] for v in values),
                cpu_peak_percent=max(v[0] for v in values),
                rss_mean_mib=statistics.mean(v[1] for v in values),
                rss_peak_mib=max(v[1] for v in values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=float, default=300, help='Measurement seconds (default: 300)')
    parser.add_argument('--interval', type=float, default=5, help='Sample interval (default: 5)')
    parser.add_argument('--warmup', type=float, default=30, help='Warmup seconds (default: 30)')
    parser.add_argument('--preview', action='store_true', help='Keep the live preview of every camera active')
    args = parser.parse_args()
    if args.interval <= 0 or args.duration < args.interval or args.warmup < 0:
        parser.error('Require duration >= interval > 0 and warmup >= 0')
    if args.preview and args.interval > 10:
        parser.error('Preview tests require interval <= 10 seconds')
    identifiers, local_camera = cameras()
    targets = discover(local_camera)
    urls = {identifier: f'{MEDIA_URL}/{identifier}' for identifier in identifiers}
    output = ROOT / 'runtime' / 'camera-benchmarks' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{time.time_ns()}')
    output.mkdir(parents=True)
    metadata = dict(parameters=vars(args), cpu_count=os.cpu_count(), cameras=identifiers,
                    components=[component for component, _ in targets],
                    cpu_units='100% equals one CPU core',
                    memory_units='Summed process RSS; shared pages may be counted more than once',
                    initial={camera: status(url) for camera, url in urls.items()})
    (output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Report: {output}', flush=True)
    print(f'Cameras: {", ".join(identifiers) or "none"}; settings remain unchanged.', flush=True)
    warmup_end = time.monotonic() + args.warmup
    while time.monotonic() < warmup_end:
        if args.preview:
            for url in urls.values():
                request_preview(url)
        time.sleep(min(args.interval, max(0, warmup_end - time.monotonic())))
    previous = {component: (cpu_usage(group), time.monotonic()) for component, group in targets}
    started = time.monotonic()
    rows, states = [], []
    with (output / 'samples.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['sample', 'elapsed_seconds', 'component', 'cpu_percent', 'rss_mib'])
        writer.writeheader()
        sample = 0
        while time.monotonic() - started < args.duration:
            preview = {camera: request_preview(url) for camera, url in urls.items()} if args.preview else None
            deadline = min(started + args.duration, started + (sample + 1) * args.interval)
            time.sleep(max(0, deadline - time.monotonic()))
            for component, group in targets:
                usage, now = cpu_usage(group), time.monotonic()
                old_usage, old_time = previous[component]
                if usage < old_usage:
                    raise RuntimeError(f'{component} restarted; repeat the test')
                row = dict(sample=sample, elapsed_seconds=round(now - started, 3), component=component,
                           cpu_percent=100 * (usage - old_usage) / 1e6 / (now - old_time),
                           rss_mib=rss_bytes(group) / 1024**2)
                writer.writerow(row)
                rows.append(row)
                previous[component] = usage, now
            stream.flush()
            states.append(dict(sample=sample, preview_requests=preview,
                               **{camera: status(url) for camera, url in urls.items()}))
            (output / 'states.json').write_text(json.dumps(states, indent=2) + '\n')
            sample += 1
    summary = dict(total=summarize(rows), **{
        component: summarize([row for row in rows if row['component'] == component]) for component, _ in targets})
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    for name, values in summary.items():
        print(f"{name}: CPU mean {values['cpu_mean_percent']:.1f}%, peak {values['cpu_peak_percent']:.1f}%; "
              f"RSS mean {values['rss_mean_mib']:.1f} MiB, peak {values['rss_peak_mib']:.1f} MiB")
    print('Check states.json for workload differences and preview failures before comparing results.')


if __name__ == '__main__':
    main()
