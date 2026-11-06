# Camera unusual-view experiment

The optional `cameras` Compose profile includes `camera-anomaly`. Open
`/cameras/` and use **Unusual view detection**. This is a visual appearance
experiment, not a validated production-process alarm. Event-only recording
is available with `CAMERA_RECORDING_MODE=anomaly`.
In anomaly mode, **Start detection & recording** starts analysis and the video
buffer together. **Pause detection & recording** stops both. Continuous recording
keeps its separate recording controls; retention and live preview remain independent.

## Workflow

1. Point the camera at a fixed scene with normal conditions.
2. Select **Learn normal view · 2 min**. Keep the view normal while 60 frames
   are collected, one every two seconds. Successful learning replaces the
   previous baseline and starts detection. In anomaly mode, the recording buffer
   is stopped during learning and starts after learning succeeds. Pause cancels collection.
3. Introduce an obvious temporary scene change and observe the score and badge.
   Two consecutive above-threshold samples confirm an event. Snapshots are
   spaced by at least 30 seconds during a continuing anomaly.
4. Inspect **Snapshot** and **Heatmap**. The heatmap highlights patch novelty;
   it is not an explanation or segmentation ground truth.
5. Adjust **Threshold multiplier** and select **Apply threshold** (or press Enter) to apply it.
   Values from 0.1 to 20 are accepted; decimals may use a dot or comma. The default is 1.25 times the calibration threshold. Higher values reduce
   sensitivity. Pausing detection also stops event recording in anomaly mode;
   preview remains available. Continuous recording is unaffected by detection controls.

The latest 100 events (JPEG, heatmap and JSON metadata) are retained. Completed event videos use the ordinary video retention policy. The recorder
discards ordinary temporary buffer segments only in event-only mode.
Event-only mode produces pre/post-event MKV recordings as described below.
Save important video separately before normal retention expires.

## Implementation

This is a small **PatchCore-style variant**, not the original benchmark model:

- Frozen ImageNet ResNet-18 through **layer2** only, with locally averaged
  128-dimensional patch features. Model Input selects 768 × 432, 1024 × 576,
  1280 × 720, 1536 × 864 or 1920 × 1080; it does not change capture resolution.
- Up to 8,000 candidate patches and a 1,024-patch greedy coreset
  selected in a seeded 32-dimensional projection.
- Exact FAISS `IndexFlatL2` nearest-neighbor search. Squared distances are converted
  to Euclidean distances before scoring and rendering heatmaps. No original
  PatchCore score reweighting is applied.
- Fixed-shape ONNX models are exported for all five input sizes during image build.
  ONNX Runtime uses XNNPACK if the installed runtime provides it; otherwise it uses
  CPUExecutionProvider. PyTorch remains available as a reference backend, using
  inference mode and channels-last convolution weights. Status reports the actual
  feature engine, distance engine, ONNX provider, feature time and distance time.
- Baselines identify the encoder as well as input size. Moving from the earlier
  layer2/layer3 model to layer2 requires new learning. An incompatible baseline
  pauses monitoring, clears the active recording trigger and preserves input size,
  threshold multiplier and confirmation settings. The previous baseline file stays
  on disk until successful new learning replaces it.
- Every fifth baseline frame is held out entirely for calibration. The base
  threshold is the 99th percentile of those held-out frame scores. The UI margin
  scales it. Adjacent window frames are correlated; this is an initial calibration,
  not a measured false-alarm probability.
- A separate CPU worker reuses the existing preview decoder over local HTTP.
  It requires a JPEG less than 0.2 seconds old and reports unavailable on errors.
  Missing status heartbeats are reported offline instead of normal.
- Preview decoding uses FFmpeg's `discardcorrupt` and strict `err_detect=explode`.
  Recognised decoder/transport errors clear the cached JPEG and suppress preview
  frames until one second without another recognised error. A one-frame admission
  delay (about 100 ms at 10 FPS) lets diagnostics invalidate queued images.
  Analysis validates the frame's decoder token after inference, before adding a
  learning sample, updating confirmation, or creating an event marker.
  Errors reset the confirmation streak. Existing confirmed video windows remain
  subject to the normal post-window; the original stream-copy recorder is unchanged.
- Cameras and System Health show preview quality, decoder-error count and excluded
  preview frames. Counters reset with the camera dashboard. Logs are rate-limited
  and contain only fixed categories and counters, never raw FFmpeg diagnostics
  that may contain RTSP credentials. A normally encoded visual defect produced
  by the camera itself cannot be identified by this decoder-error filter.

References: [PatchCore source](https://github.com/amazon-science/patchcore-inspection),
[ResNet-18 weights](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.resnet18.html).

The CPU inference libraries, exported models and weights are installed during Docker build. Camera
images are processed locally. Analysis does not receive the RTSP URL or Docker
socket. Compose requests one CPU and a 768 MiB memory ceiling; effective resource
limits depend on the host kernel/cgroup support. The worker is off in the default
stack because it belongs to the optional `cameras` profile.

## Runtime files and operations

All learned data is local under `runtime/cameras/anomaly/`, ignored by Git:
`baseline.pt`, `status.json`, `session.json`, `command.json`, and `events/`.
The pretrained backbone is in the image; normal-view memory is runtime state.
A learned baseline and monitoring/paused selection survive worker restarts.
Interrupted collection is cancelled rather than silently resuming with mixed views.

```sh
./scripts/restart-cameras.sh
./scripts/logs.sh camera-anomaly
docker compose --profile cameras stop camera-anomaly
python3 -m unittest discover -s tests -p 'test_cameras_*.py' -q
```

Settings in `.env.example`:

- `CAMERA_ANOMALY_INTERVAL_SEC=0.2`: detection interval, minimum 0.2 seconds; effective rate is bounded
  by inference and decoder availability.
- `CAMERA_ANOMALY_CONFIRM_FRAMES=2`: consecutive suspicious frames to confirm, clamped to 1–5.
- `CAMERA_ANOMALY_NORMAL_FRAMES=60`: clamped to 20–120 successful frames.
- `CAMERA_DASHBOARD_PORT=8082`: also configures the worker's internal preview URL.
- `CAMERA_ANOMALY_FEATURE_ENGINE=onnx`: `onnx` or `torch`.
- `CAMERA_ANOMALY_DISTANCE_ENGINE=faiss`: `faiss` or `torch`.
- `CAMERA_ANOMALY_THREADS=1`: 1–4 worker threads. Pi can override this with
  `PI_CAMERA_ANOMALY_THREADS`; its CPU quota is controlled separately by
  `PI_CAMERA_ANOMALY_CPUS`. More threads do not guarantee lower latency.
- `CAMERA_ANOMALY_ONNX_PROVIDER=auto`: `auto`, `cpu`, or `xnnpack`. An explicitly
  requested unavailable XNNPACK provider fails instead of silently using CPU.
- `CAMERA_ONNX_RUNTIME_IMAGE=onnx-cpu`: standard ONNX Runtime wheel. For the optional
  Linux XNNPACK build, first build the wheel image below, then set this value to
  `bms-onnx-xnnpack:local` and rebuild `pi-camera-anomaly`. The same image can also
  be used by `camera-anomaly`. Source compilation is resource intensive on Pi.

```sh
docker build -f Dockerfile.camera-onnx-xnnpack -t bms-onnx-xnnpack:local .
CAMERA_ONNX_RUNTIME_IMAGE=bms-onnx-xnnpack:local docker compose build pi-camera-anomaly
```

Ordinary `CPUExecutionProvider` is not XNNPACK. See the
[ONNX Runtime XNNPACK provider documentation](https://onnxruntime.ai/docs/execution-providers/Xnnpack-ExecutionProvider.html).
Measure latency on the target device; neither engine selection nor thread count
guarantees 5–10 FPS at 1280 × 720. Changing the feature encoder can change detection
sensitivity, so relearn and validate both normal scenes and representative anomalies.

To repeat a fixed-frame compute comparison, pause detection and other CPU-heavy
jobs first. Keep capture and preview running. The script uses a private 1,024-patch
test bank, three warm-up iterations and ten measured iterations per engine/thread
setting; it does not load or replace the live baseline. Results exclude HTTP frame
acquisition, decoder-token validation, event rendering and video recording.

```sh
TASK_TEST_DIR="$PWD/runtime/camera-inference-test"
mkdir -p "$TASK_TEST_DIR"
curl -fsS 'http://127.0.0.1:8083/api/live.jpg?fresh=1' -o "$TASK_TEST_DIR/frame.jpg"
docker run --rm --network none --cpus 4 \
  -v "$PWD:/work:ro" -v "$TASK_TEST_DIR:/bench" -w /work \
  bms-camera-anomaly:local python scripts/benchmark-camera-engines.py \
  --frame /bench/frame.jpg --output /bench/results.json --size 1280x720
```

Add `--providers cpu xnnpack` when using the XNNPACK wheel. The separate
`scripts/benchmark-camera-quantization.py` accepts at least twenty local JPEGs via
`--frames` and an experiment directory via `--output`. It compares FP32, dynamic
QUInt8 and static per-channel QDQ INT8, reporting feature drift as well as timing.
Quantized models remain experimental outputs and are not installed in the worker.
Their calibration frames and feature-error measurements do not establish anomaly
sensitivity. Deployment would require a distinct encoder identifier, new normal
memory and representative defect validation.

A two-minute window baseline covers only the current conditions. Clouds, daylight,
reflections, curtains and a moved camera can trigger novelty. Relearn when changing
camera position or scene. For a production line, collect representative normal
cycles and separate validation footage, choose an ROI, check missed anomalies and
false alerts, and measure temporal behavior before enabling event-only retention.

Initial window trial (2026-11-05): learned 60 frames. Reflections near the lower
right window triggered alerts at margin 1.25 and 1.75; the local trial was raised
to 3.0. This is manual exploratory tuning, not a validated operating threshold.
A synthetic black image exceeded the learned threshold. A subsequent physical
hand test produced two saved events with scores 3.48 and 3.75 against threshold
1.43; visual inspection confirmed the hand in both snapshots and highlighted
on the heatmap. After removal, the detector returned to normal. Longer normal
scene validation and representative production anomalies are still required.

## Event-only video recording

Set `CAMERA_RECORDING_MODE=anomaly`, `CAMERA_EVENT_PRE_SEC=7` and
`CAMERA_EVENT_POST_SEC=10` in the private `.env`, then run
`./scripts/restart-cameras.sh`. Continuous mode remains available as
`CAMERA_RECORDING_MODE=continuous`.

The recorder uses one continuous RTSP connection and FFmpeg's segment muxer
with a five-second target, without audio or transcoding. Completed segments
are kept temporarily under `runtime/cameras/recordings/.buffer/`. Camera
keyframes determine actual segment boundaries and event videos may therefore
include a few extra seconds. Reconnection and startup can still leave gaps;
pre-event context is limited to footage already present in the buffer.

Confirmed detections send a recording marker every analysis sample, independent
of the 30-second snapshot cooldown. The event begins 7 seconds before the
first suspicious sample in the confirmed streak and ends 10 seconds after the
last confirmed sample. Overlapping windows merge and continuing anomalies
extend the window. Events longer than five minutes are split to bound staging
space. The recorder retains pending event state and protected segments across
container restarts, then remuxes selected segments into one `event-*.mkv`.

Normal segments expire from the bounded buffer after the pre-window plus an
analysis-delay allowance. Completed event files have the existing
`CAMERA_MAX_AGE_HOURS` and `CAMERA_MAX_STORAGE_MB` retention limits. Buffer,
staging and the active segment are additional to that completed-video budget.
The original camera stream is preserved; 10 FPS applies only to preview.

If analysis is offline, stale, paused, learning or otherwise unable to classify
frames, unchecked video is not saved. During a camera/analysis error in a running
session, ordinary buffered video expires; an already confirmed event still keeps
its post-window when video is available. Explicitly pausing detection or starting
learning stops the event buffer and finalizes any available active event without
waiting for its remaining post-window. Live preview remains available.
Starting again fills a new pre-event buffer; the requested seven-second context
is available only after enough video has arrived. The worker restores linked
recording from the saved monitoring selection on restart. Threshold changes do
not start or stop the session. Legacy recording start/stop API routes also
request monitor/pause in anomaly mode.

The library's **Events only** filter is selected by default in anomaly mode.
It shows event video and any historical unchecked video; uncheck it to see historical continuous
recordings. Switching modes does not retroactively classify or delete old
continuous recordings. Video links appear on snapshot cards once remuxing
finishes after the post-window. A hand test shorter than the detector's
two-sample confirmation may not create an event.

## Deleting saved history

Use **Delete snapshot** on a card to remove its JPEG, heatmap and metadata.
Associated videos remain because a merged video can cover multiple snapshots.
Use **Delete** on a recording row to remove that completed MKV and its recording
metadata; associated snapshots remain.

The library's **Delete saved…** menu offers all snapshots, all completed video
recordings, or both. All-recordings deletion includes historical continuous and
unchecked recordings, even when hidden by the Events only filter. Each action
requires browser confirmation. Clearing saved history keeps the learned model,
threshold settings, live buffer and any recording in progress. New snapshots
and completed pending videos can therefore appear afterwards. In anomaly mode,
pause detection and wait for the recorder to stop before clearing history if
no new history should be created.

The dashboard has write access to the recordings mount for these bounded
deletions. API routes require the existing custom control header and accept only
validated event IDs or top-level MKV filenames. They do not traverse directories
or accept symlink media. Deletion does not invoke shell commands or Docker.

Fast detection uses a 0.2-second sampling target and two consecutive suspicious
frames. With approximately 130 ms model cycles and a stable preview decoder,
confirmation is expected in roughly 0.35–0.7 seconds after a change reaches the
preview, not guaranteed hard real time. Camera/RTSP latency is additional.
The UI polls analysis every 0.25 seconds. Normal-view learning still samples
one frame every two seconds to preserve the two-minute baseline coverage.
The 30-second snapshot cooldown applies within a continuing anomaly; a new
confirmed anomaly after a normal sample can save a new snapshot immediately.

### Model input size

Both camera pages offer only **16:9 Model input** presets:

| Input (width x height) | Heatmap grid (width x height) |
| --- | --- |
| 768x432 (default) | 96x54 |
| 1024x576 | 128x72 |
| 1280x720 | 160x90 |
| 1536x864 | 192x108 |
| 1920x1080 | 240x135 |

Select a value and press **Apply size**, then **Learn normal view**. Changing input
size stops detection and event-only recording until learning completes. Settings
are isolated per camera and persist across worker restarts. Continuous recording
and live preview remain available independently.

The full analysis image is resized to the selected width and height. A 16:9
source keeps its proportions, without cropping or square stretching. Camera
capture/recording resolution remains independent. Video analysis currently uses
the 960-wide preview; snapshots use the source JPEG. Inputs larger than that image
upscale it without adding detail. Higher presets increase CPU, memory and latency.
Pairwise patch distances are computed in bounded batches. Training selects its
8000 candidates without concatenating all full-resolution training frames; the
60 collected feature frames can still consume substantial memory at the largest
presets. Learning never uses fewer frames automatically.

Square inputs are no longer accepted by the UI or API. On upgrade, an old square
session migrates to 768x432, pauses detection and clears the old event marker.
Its baseline is retained on disk but not used. A new learning run is required.
Rectangular baselines use format version2 and record the complete input size;
a baseline from another size cannot enable detection. Threshold multiplier and
confirmation settings survive migration.

### Consecutive-frame confirmation

The **Confirmation** dropdown sets 1–5 consecutive analyzed frames above the
threshold. It applies immediately and does not require new baseline learning.
One frame reacts faster to short changes and is more prone to false alerts.
Higher counts filter isolated spikes but can miss brief events. These are analyzed
frames, not the camera's video FPS; inference speed determines the real delay.

The applied value is acknowledged by the worker and saved independently for each
camera in its runtime session. This overrides `CAMERA_ANOMALY_CONFIRM_FRAMES`,
which supplies the initial default (2). The current confirmation streak resets
when the setting changes; baseline, threshold multiplier and monitoring state
are retained. Input-size, threshold and confirmation requests are serialized by
the UI while awaiting worker acknowledgment.

### CPU threads and limits

`CAMERA_ANOMALY_THREADS` selects 1–4 PyTorch intra-operation CPU threads (default 1).
`CAMERA_ANOMALY_CPUS` sets the worker's Docker CPU quota (default 1.0).
`PI_CAMERA_ANOMALY_THREADS` and `PI_CAMERA_ANOMALY_CPUS` override only the Pi worker.
Keep the CPU quota consistent with the thread count when benchmarking. More threads
can add overhead and do not guarantee lower latency, especially with both cameras
or other host workloads active. These settings do not require baseline learning.
Worker status reports `inference_threads` alongside input size and cycle time.

`scripts/benchmark-camera-threads.py` compares 1/2/2/1 thread blocks on a fixed JPEG
and read-only baseline inside the anomaly image. It checks score/heatmap equivalence.
`scripts/benchmark-camera-live-threads.py --output runtime/camera-thread-tests/<run>`
compares live 768x432 detection in 30-second blocks, changing only the Pi anomaly
container's threads and CPU quota. It requires the updated image already built,
an active learned 768x432 baseline, and collects measured worker cycle time, FPS,
CPU and RSS. Normal Compose settings are restored in a finally block. Do not run
builds/tests or change camera controls during performance trials. These short
trials are indicative and should be repeated for deployment-wide capacity planning.

### Pi snapshot capture and exposure

The Pi camera page has **Pi camera capture** controls. Choose Video (the existing
H.264 source) or Snapshots. Video shows resolution and FPS controls and applies
automatic exposure. The IMX219 video presets are 640x480, 1280x720 and 1920x1080
(up to 30 FPS), 1640x1232 (up to 42 FPS), and 3280x2464 (up to 15 FPS).
The FPS menu offers 10/15/30/42 and disables values exceeding the selected preset.
Video preview remains capped at 10 FPS. Resolution is selectable in both modes.
Snapshot mode shows resolution, a 100/200/500/1000 ms interval and Auto or a fixed exposure from 1/5000 s (0.2 ms) to 1/25 s (40 ms), including 1/2000 s (0.5 ms).
The range applies to manual exposure; Auto remains available. Shutter values are
passed to rpicam in microseconds. Fixed exposure must
not exceed the selected frame interval. The UI shows requested exposure, not a
measurement of sensor metadata. Gain/white balance remain automatic. Resolution
is a shared capture setting: it can be changed directly in either mode. Saved
snapshot videos use the selected snapshot resolution. No settings change until
Apply is clicked.

Snapshot mode opens the camera once with `rpicam-vid --codec mjpeg`; it produces
independent JPEGs without continuously encoding H.264. The dashboard and analysis
consume fresh JPEGs directly, bypassing FFmpeg preview decoding. A frame sequence
and host receive timestamp prevent duplicates/stale images entering the recorder.
The capture device stays open while consumers are active, rather than reopening
it for each photograph. Snapshots use the source's configured capture resolution.

The recorder keeps a time-bounded disk ring under `.snapshot-buffer/` and protects
pre/post-event JPEGs with hard links. At event completion, a background single
FFmpeg worker encodes H.264 MKV, using timestamps to preserve elapsed time and the
selected nominal snapshot FPS. There are at most two queued exports plus the
current event; long events split at 300 seconds. Normal frames expire after
pre-window + post-window + 10 seconds. Protected JPEGs and metadata survive an
interrupted export and are retried at recorder restart; failed exports retain raw
frames for diagnosis and stop accepting new event windows until recorder restart,
with an explicit export-failed status to avoid unbounded failed-event storage. These temporary/protected frames are additional to the
completed-video storage budget. Event exports can temporarily increase CPU use.
Continuous recording in snapshot mode similarly collects JPEG clips then encodes
them at the configured clip duration. Existing playback/download/history routes
continue to use H.264 MKV recordings.

Applying capture settings restarts the source and pauses detection (and event-only
recording). Check the image and learn the normal view again if exposure/capture
changes affect appearance. Existing baselines are retained. Source settings are
local runtime state in `/var/lib/bms-pi-camera/config.json`; the installer creates
it through systemd StateDirectory. The app-side mirror is runtime state at
`runtime/pi-camera/control/capture.json`. Neither file belongs in Git. The existing
`/run/bms-pi-camera-mode.json` remains an ephemeral benchmark override. Resolution
matrix tests require Video mode and Auto exposure.

Wi-Fi capture mode and exposure are unchanged; these controls are only shown for
the native Pi source. The source control API listens only on loopback; the camera
dashboard validates and forwards capture changes with the usual control header.

After an unclean shutdown, snapshot exports resume from protected event frames.
If every JPEG referenced by an event is empty or missing, no video can be recovered.
The recorder moves that event to `recordings/.snapshot-recovery/` with its original
metadata and a `recovery-error.json` explanation, then continues recording. These
runtime files are retained for inspection and are not retried on every restart.
Other export failures retain protected frames and report `Export failed`.
