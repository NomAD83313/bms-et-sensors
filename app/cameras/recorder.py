"""Bounded RTSP recording using FFmpeg stream copy."""
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

try:
    from . import control, environment, event_recording, snapshot_recording, source
    from .clips import StreamProgress, prune
    from .source import FFMPEG, input_args, stop_process
except ImportError:
    import control
    import environment
    import event_recording
    import snapshot_recording
    import source
    from clips import StreamProgress, prune
    from source import FFMPEG, input_args, stop_process

LOG = logging.getLogger("bms-cameras")
__all__ = ['StreamProgress', 'command', 'main', 'prune']


def command(url, target, seconds):
    return [*FFMPEG, "-loglevel", "quiet",
            *input_args(url),
            "-map", "0:v:0", "-an", "-c:v", "copy", "-t", str(seconds),
            "-progress", "pipe:1", "-stats_period", "1",
            "-f", "matroska", "-n", str(target)]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = os.environ.get("CAMERA_URL", "")
    try:
        input_args(url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not shutil.which("ffmpeg"):
        raise SystemExit("FFmpeg is required")
    directory = Path(os.environ.get("RECORDING_DIR", "runtime/recordings"))
    seconds = environment.clip_seconds()
    max_bytes = environment.max_storage_bytes()
    max_age = environment.max_age_hours() * 3600
    if min(seconds, max_bytes, max_age) <= 0:
        raise SystemExit("Recording limits must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Remove incomplete files left by an interrupted previous run.
    for partial in directory.glob("*.partial"):
        partial.unlink()
    mode = environment.recording_mode()
    anomaly = Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly'))
    if mode not in ('continuous', 'anomaly'):
        raise SystemExit('CAMERA_RECORDING_MODE must be continuous or anomaly')
    if mode == 'anomaly':
        LOG.info('Event-only recording armed; normal segments remain in a short buffer')
        while not stop.is_set():
            if source.capture_settings()['capture_mode'] == 'snapshots':
                snapshot_recording.run(directory, stop, max_bytes, max_age, anomaly, mode, seconds)
            else:
                event_recording.run(url, directory, stop, max_bytes, max_age, anomaly)
        return
    LOG.info("Recorder started; clip duration=%ss", seconds)
    while not stop.is_set():
        if source.capture_settings()['capture_mode'] == 'snapshots':
            snapshot_recording.run(directory, stop, max_bytes, max_age, anomaly, mode, seconds)
            continue
        if not control.enabled():
            control.heartbeat('paused')
            stop.wait(1)
            continue
        control.heartbeat('connecting')
        prune(directory, max_bytes, max_age)
        target = directory / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{time.time_ns()}.partial")
        started = time.monotonic()
        process = subprocess.Popen(command(url, target, seconds), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(process.stdout.fileno(), False)
        progress = StreamProgress()
        while process.poll() is None:
            try:
                progress.feed(os.read(process.stdout.fileno(), 65536), time.monotonic())
            except BlockingIOError:
                pass
            control.heartbeat(progress.state(time.monotonic()))
            if stop.wait(1) or not control.enabled() or source.capture_settings()['capture_mode'] == 'snapshots' or time.monotonic() - started > seconds + 45:
                stop_process(process, timeout=10)
                break
        process.stdout.close()
        if process.returncode == 0 and target.exists() and target.stat().st_size:
            target.rename(target.with_suffix(".mkv"))
            LOG.info("Saved clip")
            prune(directory, max_bytes, max_age)
        else:
            target.unlink(missing_ok=True)
            if not stop.is_set():
                control.heartbeat('connecting')
                LOG.warning("Stream interrupted; retrying in 5 seconds")
                stop.wait(5)
    control.heartbeat('offline')
    LOG.info("Recorder stopped")


if __name__ == "__main__":
    main()
