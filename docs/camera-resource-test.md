# Wi-Fi versus Raspberry Pi camera resource test

Run from the repository root on the Pi. The benchmark uses Python's standard
library, Docker Compose and readable cgroup v2 CPU counters. It changes no
recording or detection settings. Results stay in ignored runtime storage.

## What is measured

- Per-component and per-camera CPU use: 100% means one CPU core, up to 400% on Pi 5.
- Mean and peak summed process RSS, using the same metric for both cameras.
  Shared pages can be counted more than once; this is not unique physical memory.
- Pi totals include `bms-pi-camera` and capture on the host: software H.264
  encoding in Video mode or JPEG capture in Snapshot mode.
- Recording/detection states and preview quality are saved for every sample.

The Wi-Fi camera encodes video on the external device. Its own CPU, RAM and power
are outside this test. This compares resource use on the Raspberry Pi host.
By default both camera groups are measured simultaneously; `--camera pi` or
`--camera wifi` selects one group. Other workloads and competition for CPU/storage
can affect results. Peaks are sampled, not absolute maxima.
The script fails if services disappear or CPU counters reset; discard interrupted
runs and repeat after services stabilize.

## Preparation

1. Ensure the selected camera application(s) are running, including the host source
   when testing Pi Camera. For isolated comparisons, stop the other application;
   stop the Pi host source too while measuring Wi-Fi alone. Close other
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
6. Record CPU affinity as well as analysis threads and quota. The optional
   `CAMERA_ANOMALY_CPUSET` and `CAMERA_MEDIA_CPUSET` settings separate detector
   CPUs from dashboard/recorder CPUs; Pi-specific values can override them.
   Empty defaults allow all CPUs. A Pi 5 experiment can compare unrestricted
   affinity with detector `2-3` and media `0-1`, keeping quotas, threads, capture,
   detection and recording unchanged. The native `bms-pi-camera.service` source
   requires separate systemd affinity; Compose does not constrain it. See
   [CPU threads and limits](camera-anomaly.md#cpu-threads-and-limits).

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

## Isolated Pi 5 measurement (2026-11-06)

Each camera was measured alone for 60 seconds after 15 seconds of warmup, with
the other camera application stopped. The Pi host source was also stopped during
the Wi-Fi measurement. Both workers used ResNet-18 layer2, ONNX Runtime XNNPACK,
FAISS, Model Input 1024x576, four threads and a four-CPU quota. These are the
tested deployment settings; the installation template defaults to a standard
CPU wheel and one thread/one CPU per worker.

| Metric | Pi Camera | Wi-Fi |
| --- | ---: | ---: |
| Mean analysis cycle | 397 ms | 427 ms |
| Analysis cycle p95 | 466 ms | 528 ms |
| Actual processed frames | 2.24 FPS | 2.10 FPS |
| Whole-host CPU (100% = all four cores) | 77.0% | 79.9% |
| Camera components CPU (100% = one core) | 261.4% | 275.2% |
| Camera components summed RSS | 773 MiB | 738 MiB |

Sources differed: Pi used JPEG snapshots at 1920x1080 with a 100 ms interval
(10 FPS); Wi-Fi supplied H.264 at 2304x1296. Wi-Fi stream metadata reported
`r_frame_rate=25/1` and `avg_frame_rate=20/1`; these are not an independent
measurement of delivered frames. All analyzed frames were normal, so event-video
encoding was not included. No throttling was reported. This was one sequential
pass, not repeated trials with matched capture settings; it compares the deployed
pipelines rather than isolating the camera connection or sensor alone.

Local raw samples and the detailed report are stored under the ignored directory
`runtime/camera-benchmarks/isolated-20261106T191415/`. Both applications and the
Pi source were restored to their initial states after the test.

## IMX219 resolution/FPS matrix

`./scripts/benchmark-camera-modes.py --duration 60 --warmup 15` measures the live
Pi camera application at five output resolutions with 15 FPS. All outputs now
use the same full-sensor field of view, with side bars for wide formats. Earlier
30/42 FPS cases selected different sensor crops and are no longer part of this
matrix. It temporarily writes `/run/bms-pi-camera-mode.json`,
restarts the host source and Pi recorder/dashboard per mode, and restores the
previous override in a `finally` block. It requires passwordless sudo for install,
rm and systemctl, and all three Pi application containers to be running. Wi-Fi
services are not changed. Existing detection continues using its baseline;
resizing and side bars can still change scores and trigger events. Do not retrain or change controls during the run.

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

## Production model speed comparison

`scripts/benchmark-camera-models.py` measures the actual `PatchCoreLite.features()`
and `score()` implementations for the production model profiles. Use `--models`
to select ResNet FP32/static INT8, MobileNetV3 FP32/static INT8, ShuffleNetV2
×0.5/×1.0 or MobileOne-S0.
Run it inside the deployed anomaly image with a private, writable `--work`
directory. Supply 24 distinct JPEGs in `frames/`. INT8 encoders calibrate on
private training frames during preparation; existing private `banks/<model>/`
are validated and reused. Production learning is never replaced.

Run `--phase prepare` once to create private banks from the first twenty frames.
Then run `--phase isolated` with capture stopped, and `--phase capture` while
capture and preview are active. Service shutdown/restoration belongs to the
caller; this script does not manage services. Keep recording and live analysis
stopped during measurements to avoid overlapping inference or event exports.
Use the same container CPU quota and `--threads` for every model.

Each measurement phase uses the four held-out frames, two warmup calls per model,
and two passes in opposite model order. The default is twenty samples per pass.
Results split JPEG preparation, neural-network execution and FAISS search, and
include mean/median/p95, process CPU, host CPU and temperature. All banks contain
1024 vectors. Reciprocal processing time describes compute throughput; it excludes
HTTP acquisition, confirmation delays and event image/video export. This is a
speed comparison and does not establish anomaly-detection quality.

## Labelled model quality comparison

`scripts/benchmark-camera-quality.py --work /bench --policy fresh-policy.json
--prefix fresh` evaluates all seven profiles on a frozen JSON dataset manifest.
Run it in the anomaly image, mounting the repository at its original absolute
path read-only and the private work directory at `/bench` read-write. Keep only
one benchmark running, with the same CPU quota and capture load for every model.
Service shutdown and restoration remain the caller's responsibility.

The manifest contains `input_size`, `threads`, `multiplier`, `shortlist_count`,
`training` and `calibration` lists of absolute JPEG paths, and a `test` list.
Each test record has `path`, `kind` (`normal` or `hand`), and an `episode` string
for positive frames from the same hand pass. Annotate frames visually before
examining predictions. Training, calibration and test JPEG content must be
disjoint; duplicate content across partitions causes an error.

Each profile learns a private 1024-vector bank; INT8 calibration uses only
training images. The operating threshold is the 99th percentile of scores on
separate normal calibration frames, multiplied by the manifest's fixed
multiplier. Do not tune this multiplier against the anomaly test frames.
Reports include frame recall, false-positive counts, event recall on annotated
frames, AUROC, and JPEG-preparation-plus-feature-plus-search timings. A detected
event means at least one labelled frame exceeded the threshold. This excludes
online frame skipping, consecutive-frame confirmation and video export latency.
A hand pass absent from captured frames cannot be counted as a model miss.

The shortlist requires at least 80% annotated event recall and at most 5%
false-positive frames, then excludes models dominated in recall, false alerts
and latency. These are selection criteria, not deployment acceptance guarantees.
Small, correlated single-scene samples cannot establish general defect accuracy.
Completed result files are reused; use a new `--prefix` whenever the manifest,
images or benchmark settings change. Banks and reports stay under ignored
`runtime/` and never replace either camera's production learning.

For an ROI test, include the normalized `roi` object and an explicit `models`
list in the policy. The benchmark crops the same full-source pixels for every
model and preserves the ROI proportions inside `input_size`. Its report records
the resulting effective input dimensions. Capture immutable source frames with
`scripts/capture-camera-model-dataset.py`; the first phase locks the current ROI,
maximum input, source geometry, threads, multiplier and confirmation setting.
Use separate `normal` and `challenge` phases. Visually annotate challenge frames
before running any model, and keep training, threshold calibration and evaluation
frames disjoint. Capturing does not replace the live baseline.
Set `threshold_mode` to `production-held-out` and leave `calibration` empty to
match normal two-minute learning: every fifth frame in the training set calibrates
the threshold and does not enter the memory bank. The default `separate-p99`
mode retains the older benchmark protocol and requires a separate calibration set.
