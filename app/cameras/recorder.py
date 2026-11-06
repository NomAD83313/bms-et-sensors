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
    from .source import input_args
except ImportError:
    from source import input_args


try:
    from . import control, event_recording, snapshot_recording, source
except ImportError:
    import control
    import event_recording
    import snapshot_recording
    import source

LOG = logging.getLogger("bms-cameras")


def prune(directory, max_bytes, max_age, now=None):
    """Remove oldest completed clips; never remove an active partial file."""
    now = time.time() if now is None else now
    clips = sorted(directory.glob("*.mkv"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in clips)
    for path in clips:
        stat = path.stat()
        if now - stat.st_mtime > max_age or total > max_bytes:
            path.unlink()
            path.with_suffix('.json').unlink(missing_ok=True)
            total -= stat.st_size
            LOG.info("Removed expired clip %s", path.name)


def command(url, target, seconds):
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "quiet",
            *input_args(url),
            "-map", "0:v:0", "-an", "-c:v", "copy", "-t", str(seconds),
            "-progress", "pipe:1", "-stats_period", "1",
            "-f", "matroska", "-n", str(target)]


class StreamProgress:
    """Report recording only while FFmpeg's video timestamp advances."""
    def __init__(self):
        self.buffer = b''
        self.timestamp = 0
        self.updated = None

    def feed(self, chunk, now):
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.startswith(b'out_time_us='):
                try:
                    timestamp = int(line.split(b'=', 1)[1])
                except ValueError:
                    continue
                if timestamp > self.timestamp:
                    self.timestamp = timestamp
                    self.updated = now

    def state(self, now):
        return 'recording' if self.updated is not None and now - self.updated < 6 else 'connecting'


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
    seconds = int(os.environ.get("CLIP_SECONDS", "60"))
    max_bytes = int(os.environ.get("MAX_STORAGE_MB", "10240")) * 1024 * 1024
    max_age = float(os.environ.get("MAX_AGE_HOURS", "24")) * 3600
    if min(seconds, max_bytes, max_age) <= 0:
        raise SystemExit("Recording limits must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    # Remove incomplete files left by an interrupted previous run.
    for partial in directory.glob("*.partial"):
        partial.unlink()
    mode = os.environ.get('CAMERA_RECORDING_MODE', 'continuous')
    if mode not in ('continuous', 'anomaly'):
        raise SystemExit('CAMERA_RECORDING_MODE must be continuous or anomaly')
    if mode == 'anomaly':
        LOG.info('Event-only recording armed; normal segments remain in a short buffer')
        while not stop.is_set():
            if source.capture_settings()['capture_mode'] == 'snapshots':
                snapshot_recording.run(directory, stop, max_bytes, max_age, prune, control,
                    Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly')), mode, seconds)
            else:
                event_recording.run(url, directory, stop, max_bytes, max_age, StreamProgress,
                    prune, control, Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly')))
        return
    LOG.info("Recorder started; clip duration=%ss", seconds)
    while not stop.is_set():
        if source.capture_settings()['capture_mode'] == 'snapshots':
            snapshot_recording.run(directory, stop, max_bytes, max_age, prune, control,
                Path(os.environ.get('CAMERA_ANOMALY_DIR', '/anomaly')), mode, seconds)
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
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
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
