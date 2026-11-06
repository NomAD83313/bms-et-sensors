# Wi-Fi versus Raspberry Pi camera resource test

Run from the repository root on the Pi. The benchmark uses Python's standard
library, Docker Compose and readable cgroup v2 CPU counters. It changes no
recording or detection settings. Results stay in ignored runtime storage.

## What is measured

- Per-component and per-camera CPU use: 100% means one CPU core, up to 400% on Pi 5.
- Mean and peak summed process RSS, using the same metric for both cameras.
  Shared pages can be counted more than once; this is not unique physical memory.
- Pi totals include `bms-pi-camera` and its software H.264 encoder on the host.
- Recording/detection states and preview quality are saved for every sample.

The Wi-Fi camera encodes video on the external device. Its own CPU, RAM and power
are outside this test. This compares resource use on the Raspberry Pi host.
Both camera groups are measured simultaneously; other workloads and competition
for CPU/storage can affect results. Peaks are sampled, not absolute maxima.
The script fails if services disappear or CPU counters reset; discard interrupted
runs and repeat after services stabilize.

## Preparation

1. Ensure both camera applications and the host source are running. Close other
   preview tabs and avoid builds, exports or other heavy tasks during the test.
2. Set BOTH cameras to the same recording mode for a recording comparison.
   `CAMERA_RECORDING_MODE` controls Wi-Fi and, by default, Pi.
   `PI_CAMERA_RECORDING_MODE` optionally overrides the Pi mode.
   Changing these requires recreating the corresponding camera containers.
3. Use both camera pages to choose the same detection state. For active analysis,
   learn a baseline separately for each camera first. Wait for learning to finish;
   do not include training in steady-state measurements.
4. Keep lighting/scenes stable. Record source resolution, frame rate and bitrate.
   Pi defaults to 1280×720 at 15 fps, 2 Mbit/s. If Wi-Fi differs, the test compares
   the deployed configurations; it does not isolate the connection type alone.
5. Use the same storage device and retention limits. Each recording directory has
   its own budget; verify enough free space for two streams.

## Test scenarios

| Scenario | Recording | Detection | Benchmark option |
| --- | --- | --- | --- |
| Idle | Paused on both | Paused on both | No `--preview` |
| Live preview | Paused on both | Paused on both | `--preview` |
| Recording plus preview | Continuous, running on both | Paused on both | `--preview` |
| Event monitoring | Anomaly mode, detection active on both | Trained, monitoring on both | `--preview` |

The Pi host HTTP service remains active in idle mode, but capture and encoding
stop when there are no preview or recording consumers. An active detection worker
requests preview frames itself, so omitting `--preview` does not disable analysis.

For each scenario run three times, using five measured minutes after 30 seconds
of warmup. Use the same command parameters for all repetitions:

```sh
./scripts/benchmark-cameras.py --duration 300 --warmup 30 --interval 5 --preview
```

For idle measurements omit `--preview`. Warmup allows prior preview consumers to
expire; use `--warmup 45` for idle immediately after closing preview tabs.
If backend ports differ, pass `--wifi-url` and `--pi-url`.
Use `--camera pi` or `--camera wifi` to measure only one camera without starting
the other. The default `--camera both` requires both camera groups to be running.
Single-camera and simultaneous runs have different contention conditions;
record this distinction when comparing them.

Each run creates `runtime/camera-benchmarks/<timestamp>/` containing:

- `metadata.json`: initial states, parameters and metric definitions.
- `samples.csv`: per-component measurements, suitable for plotting.
- `states.json`: recording/detection states, preview errors and request success.
- `summary.json`: simultaneous totals for each camera, mean and peak CPU/RSS.

Reject comparisons with learning, connecting/offline states, preview recovery,
failed preview requests, service restarts or mismatched modes. Compare the three
run means and report their spread, source settings and scenario with the result.
The script does not automatically change camera modes or start detection.

## IMX219 resolution/FPS matrix

`./scripts/benchmark-camera-modes.py --duration 60 --warmup 15` measures the live
Pi camera application at five resolutions with 15 fps, plus 1080p30, 1640x1232p42
(experimental), and VGA30. It temporarily writes `/run/bms-pi-camera-mode.json`,
restarts the host source and Pi recorder/dashboard per mode, and restores the
previous override in a `finally` block. It requires passwordless sudo for install,
rm and systemctl, and all three Pi application containers to be running. Wi-Fi
services are not changed. Existing detection continues using its baseline;
changed crops can trigger events. Do not retrain or change controls during the run.

Results are under ignored `runtime/camera-mode-tests/`. Each mode links to full
benchmark samples. Stream FPS is checked from decoded frame timestamp spacing,
not just the encoder's advertised FPS. CPU/RSS and preview failures are recorded.
A short FPS probe does not prove sustained frame cadence for the entire minute.
All modes use the same 2 Mbit/s target; high-resolution image quality is therefore
not comparable at equal per-pixel quality. Preview stays 960 pixels wide / 10 fps,
and model input processing is unchanged. This measures the deployed pipeline.

Graceful interruption restores source settings. After power loss or SIGKILL,
remove `/run/bms-pi-camera-mode.json` and restart `bms-pi-camera` plus the Pi
recorder/dashboard. The override under `/run` also disappears at host reboot.
The setup script installs `app/cameras/pi_source.py` on the host; update that
installed source before using the matrix runner after a code change.
