#!/usr/bin/env python3
"""Capture immutable full-source JPEGs for an offline camera-model comparison."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


LOCKED_FIELDS = (
    'input_size', 'roi', 'source_width', 'source_height',
    'effective_input_width', 'effective_input_height', 'inference_threads',
    'multiplier', 'confirmation_frames', 'analysis_interval_sec',
)


def request_json(url):
    with urlopen(Request(url, headers={'Accept': 'application/json'}), timeout=10) as response:
        return json.load(response)


def jpeg_size(data):
    """Read JPEG SOF dimensions without adding a Pillow host dependency."""
    if len(data) < 4 or data[:2] != b'\xff\xd8':
        raise ValueError('Camera response is not a JPEG')
    offset = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
           0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            raise ValueError('Camera returned an incomplete JPEG')
        if marker in sof:
            if length < 7:
                raise ValueError('JPEG SOF segment is incomplete')
            height = int.from_bytes(data[offset + 3:offset + 5], 'big')
            width = int.from_bytes(data[offset + 5:offset + 7], 'big')
            if width <= 0 or height <= 0:
                raise ValueError('JPEG dimensions are invalid')
            return width, height
        offset += length
    raise ValueError('JPEG dimensions were not found')


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--phase', required=True, choices=('normal', 'challenge'))
    parser.add_argument('--url', default='http://127.0.0.1:8082')
    parser.add_argument('--frames', type=int, required=True)
    parser.add_argument('--interval', type=float, default=.5)
    args = parser.parse_args()
    if args.frames < 1 or args.interval < .1:
        parser.error('Frames must be positive and interval must be at least 0.1 seconds')

    args.work = args.work.resolve()
    phase = args.work / args.phase
    if phase.exists() and any(phase.iterdir()):
        parser.error(f'Capture directory is not empty: {phase}')
    phase.mkdir(parents=True, exist_ok=True)

    status = request_json(args.url.rstrip('/') + '/api/anomaly')
    settings = {field: status.get(field) for field in LOCKED_FIELDS}
    settings.update(camera_url=args.url.rstrip('/'), analysis_source=status.get('analysis_source'))
    settings_path = args.work / 'capture-settings.json'
    if settings_path.exists():
        if json.loads(settings_path.read_text()) != settings:
            parser.error('Camera analysis settings changed since the first capture')
    else:
        write_json(settings_path, settings)

    records = []
    started = time.time()
    next_frame = time.monotonic()
    for index in range(args.frames):
        delay = next_frame - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        requested = time.time()
        request = Request(args.url.rstrip('/') + '/api/live.jpg?fresh=1&analysis=1',
                          headers={'Accept': 'image/jpeg'})
        with urlopen(request, timeout=10) as response:
            data = response.read(16 * 1024 * 1024 + 1)
            source = response.headers.get('X-Camera-Analysis-Source')
            token = response.headers.get('X-Camera-Decoder-Generation')
        if len(data) > 16 * 1024 * 1024:
            raise ValueError('Camera JPEG exceeds the 16 MiB analysis limit')
        if source != 'original-v1':
            raise ValueError('Camera did not return an original analysis frame')
        width, height = jpeg_size(data)
        if (width, height) != (settings['source_width'], settings['source_height']):
            raise ValueError('Camera source dimensions changed during capture')
        path = phase / f'{index:04d}.jpg'
        path.write_bytes(data)
        records.append(dict(path=str(path), requested=requested, elapsed=requested - started,
                            width=width, height=height, bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(), decoder_generation=token))
        write_json(phase / 'capture.json', records)
        print(f'{args.phase.upper()} {index + 1}/{args.frames}', flush=True)
        next_frame += args.interval

    if len({row['sha256'] for row in records}) != len(records):
        raise ValueError('Capture contains duplicate JPEG content')
    print(json.dumps(dict(phase=args.phase, frames=len(records), seconds=time.time() - started,
                          directory=str(phase))), flush=True)


if __name__ == '__main__':
    main()
