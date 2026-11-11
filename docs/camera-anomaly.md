# Camera unusual-view experiment

The optional `cameras` Compose profile includes `camera-anomaly`. Open
`/cameras/` and use **Unusual view detection**. This is a visual appearance
experiment, not a validated production-process alarm. Event-only recording
is available with `CAMERA_RECORDING_MODE=anomaly`.
In anomaly mode, **Start detection & recording** starts analysis and the video
buffer together. **Pause detection & recording** stops both. Continuous recording
keeps its separate recording controls; retention and live preview remain independent.

## Saved normal sessions

The **Analysis image** radio buttons select **Color** (default RGB) or **Grayscale**
independently for each camera. This affects learning and detection only: live preview,
stored session JPEGs, event snapshots, heatmaps and video retain their original color.
Grayscale JPEG decoding requests luminance directly, then crops and resizes one
channel. That channel is expanded to three before the encoder's existing ImageNet
normalization, preserving compatibility with the current RGB ONNX input. The CNN
and nearest-neighbor workloads remain unchanged; compare preparation and total
cycle time to measure the benefit. Color-only anomalies may become less visible.

Changing the analysis color mode pauses detection and event recording, removes the
incompatible baseline and normal sessions, and requires new learning. Selecting the
already active mode is a no-op. The selection persists across worker restarts and
is validated in saved baselines and session configuration. Existing baselines and
session catalogs without this setting are interpreted as RGB.

Both cameras support up to eight independently named normal sessions. Names are
optional and default to `Session 1`, `Session 2`, etc.; an optional comment can
describe any reason for a normal state, including a new permitted object. Sessions
are not associated with a time of day. Thumbnails outline the saved analysis region
and dim the surrounding image; full-frame sessions are labeled **FULL FRAME**.
They use the session's stored ROI, independently of current live selection.
Each card shows a thumbnail, capture time,
frame count and its held-out calibration threshold.

- **Learn new baseline** collects 30–120 frames at the selected 1–4 minute duration
  and replaces the previous saved sessions only after a successful rebuild.
- **Add normal session** retains previous sessions, collects another normal state,
  rebuilds the common bank and starts detection when complete.
- **Replace** collects a replacement for one selected session and retains the rest.
- **Save name / comment** changes metadata without retraining or pausing detection.
- **Delete** rebuilds the remaining sessions and leaves detection paused. Deleting
  the last session clears the baseline. **Rebuild saved sessions** retries training
  without capturing another session and also leaves detection paused.

Capture and rebuilding report separate progress. Rebuilding reprocesses all saved
JPEGs and may take longer as sessions accumulate; inference continues to use one
bounded bank, not one model execution per session. Every fifth frame in each
session is excluded from the memory bank. Calibration uses the maximum of the
per-session 99th-percentile normal scores. These adjacent held-out frames are still
correlated: evaluate independent normal footage and real anomalies after adding
a new state. More accepted appearances can also make similar anomalies less visible.

Sessions must use the same model, input preset, ROI, source dimensions and
preprocessing. Changing ROI, input preset, model, analysis color mode or source dimensions automatically
deletes the incompatible baseline and its normal sessions, including their JPEGs.
Startup configuration mismatches also clear incompatible sessions. New normal-view
learning is then required. Pausing, a temporary camera outage, metadata edits,
threshold changes and confirmation settings retain the saved sessions. Failed
rebuilds retain compatible sessions for retry. A pre-session baseline
continues to work, but has no source training images to extend; create the first
new baseline to enable accumulation. There is no automatic learning of live frames.

Original JPEGs and the catalog live under the existing runtime anomaly mount in
`learning/`; they are never committed. Each camera has its own catalog. Storage is
limited to 4 GiB of published and currently collected JPEGs, with at least 64 MiB
free-space reserve. Interrupted in-progress captures do not replace saved sessions.
Completed sessions and baseline publication use a revision check and a temporary
baseline backup so a power interruption cannot silently pair an unrelated bank
with the saved sessions. A failed rebuild leaves detection paused and the previous
sessions available for retry. History deletion does not delete learning sessions.

## Workflow

1. Point the camera at a fixed scene with normal conditions. Choose a model,
   maximum input size and optionally an analysis area (ROI). Apply any changes
   before learning.
2. Select a **Learning time** from 1–4 minutes, then select **Learn new baseline**.
   Keep the view normal while 30–120 frames are collected, one every two seconds.
   Successful learning replaces the
   previous baseline and saved normal sessions and starts detection. Use **Add normal session**
   to include another normal state while retaining the existing sessions. In anomaly mode, the recording buffer
   is stopped during learning and starts after learning succeeds. Pause cancels collection.
3. Introduce an obvious temporary scene change and observe the score and badge.
   The Confirmation menu selects 1–5 consecutive above-threshold samples to
   confirm an event (initial default: 2). Snapshots are
   spaced by at least 30 seconds during a continuing anomaly.
4. Inspect **Snapshot** and **Heatmap**. The heatmap highlights patch novelty;
   it is not an explanation or segmentation ground truth.
5. Adjust **Threshold multiplier** and select **Apply threshold** (or press Enter) to apply it.
   Values from 0.1 to 20 are accepted; decimals may use a dot or comma. The default is 1.25 times the calibration threshold. Higher values reduce
   sensitivity. Pausing detection also stops event recording in anomaly mode;
   preview remains available. Continuous recording is unaffected by detection controls.

Event snapshots preserve the full-size JPEG corresponding to the analyzed frame
without further resizing or recompression. Heatmap overlays use its full width and height;
their effective detail still comes from the selected model's patch grid.
Pi snapshot mode uses the selected capture resolution (including 3280x2464).
The browser receives a separate 960x540 preview. Frames with other aspect ratios
fit inside that canvas with black bars, preserving the complete field of view.
Native Pi snapshot previews are resized lazily and cached; analysis and recording
retain the source JPEG. Pi video uses its full-size decoder output for analysis
and resizes it only when serving the browser. A WiFi camera can use its main
stream for analysis and a separate substream for the browser.

Learning and detection request
`/api/live.jpg?analysis=1`. The dashboard returns the full source JPEG with
an `X-Camera-Analysis-Source: original-v1` header and decoder-generation token.
The model decodes this original, crops the selected ROI if enabled, and resizes
directly to the effective model input. The worker
rejects a missing source marker, incomplete JPEG, a response above 16 MiB, or a
decoder token invalidated during inference. Event snapshots and overlays use the
same full-size analysis JPEG; no later snapshot is requested for an event.
Full-size video JPEG encoding adds CPU work, and decoding a full JPEG before
model resizing costs more than reading the browser preview. It avoids enlarging
an already reduced image.

The latest 100 events (JPEG, heatmap and JSON metadata) are retained. Completed event videos use the ordinary video retention policy. The recorder
discards ordinary temporary buffer segments only in event-only mode.
Event-only mode produces pre/post-event MKV recordings as described below.
Save important video separately before normal retention expires.

## Implementation

This is a small **PatchCore-style variant**, not the original benchmark model:

- The default frozen ImageNet ResNet-18 runs through **layer2** only, with locally
  averaged 128-dimensional patch features. MobileNetV3-Small uses its first nine
  feature stages, with 48-dimensional stride-sixteen features. Maximum input selects
  bounds of 768 × 432, 1024 × 576, 1280 × 720, 1536 × 864 or 1920 × 1080;
  an enabled ROI is fitted within those bounds while preserving its proportions.
  Analysis settings do not change capture resolution.
- Up to 8,000 candidate patches and a 1,024-patch greedy coreset
  selected in a seeded 32-dimensional projection.
- Exact FAISS `IndexFlatL2` nearest-neighbor search. Squared distances are converted
  to Euclidean distances before scoring and rendering heatmaps. No original
  PatchCore score reweighting is applied.
- Fixed-shape ONNX models are exported for all five full-frame input sizes during
  image build. Additional dynamic-shape encoders support ROI aspect ratios.
  ONNX Runtime uses XNNPACK if the installed runtime provides it; otherwise it uses
  CPUExecutionProvider. PyTorch remains available as a reference backend, using
  inference mode and channels-last convolution weights. Status reports the actual
  feature engine, distance engine, ONNX provider, feature time and distance time.
- Baselines use one strict schema that records the encoder, source geometry, ROI,
  preprocessing, input size, score aggregation and coreset size. Any mismatch pauses
  monitoring and requires a new normal-view learning run.
- Every fifth frame in each normal session is held out entirely for calibration.
  The base threshold is the largest per-session 99th percentile of held-out scores. The UI margin
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
socket. Compose defaults to one CPU and requests a 768 MiB memory ceiling; local
CPU settings can override the default, and effective limits depend on the host
kernel/cgroup support. The workers are off in the default stack because they
belong to the optional `cameras` and `pi-camera` profiles.

## Runtime files and operations

All learned data is local under `runtime/cameras/anomaly/`, ignored by Git:
`baseline.pt`, `status.json`, `session.json`, `command.json`, `events/`, and `learning/`.
Pi uses the same structure under `runtime/pi-camera/anomaly/`. Neither directory
belongs in Git.
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
- `CAMERA_ANOMALY_NORMAL_FRAMES=60`: startup default, clamped to 20–120 successful
  frames. The 1–4 minute UI selection stores 30, 60, 90 or 120 frames for later runs.
- `CAMERA_DASHBOARD_PORT=8082`: also configures the worker's internal preview URL.
- `CAMERA_ANOMALY_FEATURE_ENGINE=onnx`: `onnx` or `torch`.
- `CAMERA_ANOMALY_DISTANCE_ENGINE=faiss`: `faiss` or `torch`.
- `CAMERA_ANOMALY_THREADS=1`: 1–4 worker threads. Pi can override this with
  `PI_CAMERA_ANOMALY_THREADS`; its CPU quota is controlled separately by
  `PI_CAMERA_ANOMALY_CPUS`. More threads do not guarantee lower latency.
- `CAMERA_ANOMALY_CPUSET=`: optional allowed CPUs for analysis containers.
  `CAMERA_MEDIA_CPUSET=` applies to camera dashboards and recorders. Empty values
  allow all available CPUs; Pi-specific overrides use the `PI_CAMERA_` prefix.
- `CAMERA_ANOMALY_ONNX_PROVIDER=auto`: `auto`, `cpu`, or `xnnpack`. An explicitly
  requested unavailable XNNPACK provider fails instead of silently using CPU.
- `CAMERA_ANOMALY_JPEG_DRAFT=0`: set to `1` to let Pillow decode a reduced JPEG
  before applying the ROI and model resize. This lowers preparation time for large
  source frames. Changing it invalidates the current baseline.
- `CAMERA_ANOMALY_TOP_K=1`: number of largest patch distances averaged into the
  frame score, from 1 to 20. Changing it invalidates the current baseline.
- `CAMERA_ANOMALY_CORESET_SIZE=1024`: maximum normal-memory vectors, from 256 to
  4096. Pi-only overrides use the `PI_CAMERA_ANOMALY_` prefix. A newly configured
  coreset size takes effect at the next learning run.
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

The installation template uses the standard CPU wheel and one thread per worker.
The isolated Pi 5 measurement on 2026-11-06 used a locally built XNNPACK wheel,
`CAMERA_ONNX_RUNTIME_IMAGE=bms-onnx-xnnpack:local`, and four threads/four CPUs per
worker. These are deployment settings, not installation defaults. See
[the measured results and source differences](camera-resource-test.md#isolated-pi-5-measurement-2026-11-06).
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
curl -fsS 'http://127.0.0.1:8083/api/live.jpg?analysis=1' -o "$TASK_TEST_DIR/frame.jpg"
docker run --rm --network none --cpus 4 \
  -v "$PWD:/work:ro" -v "$TASK_TEST_DIR:/bench" -w /work \
  bms-camera-anomaly:local python scripts/benchmark-camera-engines.py \
  --frame /bench/frame.jpg --output /bench/results.json --size 1280x720
```

Add `--providers cpu xnnpack` when using the XNNPACK wheel.

A 1–4 minute baseline covers only the conditions present during that run. Clouds, daylight,
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
not start or stop the session.

The library's **Events only** filter is selected by default in anomaly mode.
Uncheck it to see continuous recordings. Video links appear on snapshot cards once remuxing
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

Detection uses a default minimum cycle interval of 0.2 seconds and configurable
confirmation across 1–5 analyzed frames. Actual latency depends on JPEG preparation,
model inference, distance search and frame availability; the sampling target does
not guarantee 5 FPS. Camera/RTSP latency is additional.
The worker waits only for the unused part of the target interval. If processing
already exceeds that interval, it starts the next iteration without an extra pause.
The UI polls analysis every 0.25 seconds. Normal-view learning samples one frame
every two seconds for the selected 1–4 minute duration.
The 30-second snapshot cooldown applies within a continuing anomaly; a new
confirmed anomaly after a normal sample can save a new snapshot immediately.

### Analysis model selection

Both camera pages offer the five backend models below. No retired model identifiers
or hidden menu entries are retained.

| Model | Features | Heatmap stride |
| --- | --- | --- |
| MobileNetV3 | MobileNetV3-Small first nine feature stages, FP32, 48 channels | 16 |
| PP-LCNet ×0.5 | FP32, 128 channels | 16 |
| MobileNetV4 ConvSmall ×0.5 | FP32, 48 channels | 16 |
| ResNet FP32 | ResNet-18 layer2, FP32, 128 channels | 8 |
| MobileOne-S0 | Fused stages 0–2, FP32, 128 channels | 8 |

PP-LCNet uses the pretrained timm `lcnet_050.ra2_in1k` weights. MobileNetV4 uses
`mobilenetv4_conv_small_050.e3000_r224_in1k`. MobileOne uses Apple's published
fused S0 checkpoint; its license is retained under `app/cameras/licenses/`.
All encoder files are exported during the image build, so live inference does not
download weights.

Switching models pauses detection and event recording and requires **Learn normal
view** again. Scores, thresholds and learned patch memory are specific to the selected
model. Validate representative normal conditions and real anomalies in the target
scene before relying on event retention.

### Analysis area (ROI)

Both camera pages use the same ROI controls. Select **Select area**, then drag a
rectangle over the live preview. The draft rectangle does not affect analysis
until **Apply area** is selected. **Cancel** discards the draft. **Full frame · apply**
removes an applied ROI and restores full-frame analysis. The browser preview,
camera capture settings and video recording field of view remain unchanged.

The selection is stored as normalized coordinates of the original source frame,
not as pixels of the 960x540 browser preview. Preview padding and responsive display
scaling are removed when mapping the selection back to the source. Each side must
cover at least 1% of the source, remain inside the frame, and produce an effective
model input with both sides at least 32 pixels. Very narrow rectangles may therefore
need a larger maximum input or a wider selection.

With an ROI, **Maximum input** selects the bounding dimensions of the model tensor.
The crop keeps its own aspect ratio rather than being stretched to 16:9. For example,
the 768x432 maximum reduces larger crops to 576x432 for 4:3, 432x432 for a square,
or approximately 346x432 for 4:5. Smaller crops retain their native pixel dimensions:
a 1722x1051 crop stays 1722x1051 with a 1920x1080 maximum. ROI pixels are never
upscaled. The status shows the actual input dimensions
and heatmap grid. Rounding to whole pixels can introduce a small aspect difference.
Without an ROI, the selected full-frame preset dimensions are used directly.

Apply the final area and maximum input, then select **Learn normal view**. Applying
a different ROI pauses detection and event-only recording, clears the confirmation
streak and requires new normal-view learning. Changing source dimensions also
invalidates the baseline. No new learning starts automatically.
Baselines saved with enlarged ROI dimensions are rejected and require new learning.

All baselines use format version 6 and record the normalized ROI (or full frame),
`roi-original-aspect-v1` preprocessing revision, source dimensions and effective
model dimensions. A bank for another region, input, encoder or source geometry
cannot enable detection.

Event snapshots still save the complete original JPEG. The heatmap is mapped back
only into the selected region of the complete image; pixels outside that region
are not highlighted. Event video still includes the complete camera frame.

An ROI can exclude irrelevant background motion and give useful objects more of
the model's input pixels. It does not guarantee fewer false alerts or better defect
detection: lighting inside the area and objects crossing its edges still need
validation. Cropping alone at the same effective input dimensions does not reduce
the convolution or nearest-neighbor workload. For speed, combine a tighter ROI
with a smaller maximum input. For a 16:9 source, an area covering half its width
and height at 768x432 can retain roughly the same scene detail as the complete
frame at 1536x864 while using one quarter of the model input pixels. The full JPEG
is still transferred and decoded, so complete-cycle latency will not necessarily
improve by the same factor.

### Model input size

Both camera pages offer five **Maximum input** presets. They are fixed 16:9 model
dimensions for full-frame analysis and maximum bounds for an enabled ROI. The
table lists full-frame heatmap grids; an ROI's grid follows its effective dimensions.

| Input (width x height) | ResNet / MobileOne grid | MobileNetV3 / PP-LCNet / MobileNetV4 grid |
| --- | --- | --- |
| 768x432 (default) | 96x54 | 48x27 |
| 1024x576 | 128x72 | 64x36 |
| 1280x720 | 160x90 | 80x45 |
| 1536x864 | 192x108 | 96x54 |
| 1920x1080 | 240x135 | 120x68 |

Select a value and press **Apply size**, then **Learn normal view**. Changing input
size stops detection and event-only recording until learning completes. Settings
are isolated per camera and persist across worker restarts. Continuous recording
and live preview remain available independently.

Without an ROI, the full analysis image is resized to the selected width and height.
A 16:9 source keeps its proportions; other full-frame aspect ratios retain the
existing resize behavior. An enabled ROI is cropped first and fitted to the
selected bounds while preserving its own proportions, without upscaling. Camera
capture/recording resolution remains independent. Both video and snapshot analysis use
the full source JPEG directly. Full-frame inputs larger than the source image still
upscale it without adding detail; an enabled ROI never does. Event snapshots and overlays use the matched
full-size source frame, independently of this analysis-input limit.
Higher presets increase CPU, memory and latency.
Pairwise patch distances are computed in bounded batches. Rebuilding saved sessions
processes JPEGs one at a time and selects at most 8000 candidate vectors, sharing
the candidate budget equally between sessions. Full-resolution feature frames are
not accumulated in RAM. The final coreset remains bounded by the configured size.
Learning never uses fewer frames automatically.

Baselines use strict format version 6. A baseline from another model, input size,
source geometry, ROI, preprocessing mode, Top-K setting or JPEG Draft setting cannot
enable detection. Unsupported session values reset to current defaults and require
new normal-view learning.

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

### Offline model comparison

Use `scripts/benchmark-camera-models.py` for timing all supported models on a fixed
JPEG set. Use `scripts/benchmark-camera-quality.py` with a frozen policy manifest
for labelled normal/anomaly evaluation. Both scripts use the same current model
registry as the worker and never modify live baselines.

Timing alone does not establish anomaly quality. Keep training and test frames
disjoint, lock the threshold policy before evaluation, and validate real defects
and lighting changes from the target scene.

### CPU threads and limits

`CAMERA_ANOMALY_THREADS` selects 1–4 analysis threads (default 1), configuring
PyTorch, FAISS and the selected ONNX provider. XNNPACK uses its own thread pool;
the ONNX intra-operation pool stays at one thread when XNNPACK is selected.
`CAMERA_ANOMALY_CPUS` sets the worker's Docker CPU quota (default 1.0).
`PI_CAMERA_ANOMALY_THREADS` and `PI_CAMERA_ANOMALY_CPUS` override only the Pi worker.
Keep the CPU quota consistent with the thread count when benchmarking. More threads
can add overhead and do not guarantee lower latency, especially with both cameras
or other host workloads active. These settings do not require baseline learning.
Worker status reports `inference_threads` alongside input size and cycle time.

CPU affinity is separate from the CPU quota and library thread count.
`CAMERA_ANOMALY_CPUSET` selects allowed CPUs for the Wi-Fi analysis worker;
`CAMERA_MEDIA_CPUSET` selects allowed CPUs for its dashboard (including the live
FFmpeg decoder) and recorder. The Pi services inherit these values unless
`PI_CAMERA_ANOMALY_CPUSET` or `PI_CAMERA_MEDIA_CPUSET` is set. Defaults are empty
to allow every available CPU, including on hosts with fewer cores.
An unset or empty Pi override inherits the common mask. To bypass a nonempty
common restriction for Pi only, explicitly list that host's available CPUs.

For a four-core Pi 5, an optional starting point in the local `.env` is:

```dotenv
CAMERA_ANOMALY_CPUSET=2-3
CAMERA_MEDIA_CPUSET=0-1
```

This allows both detectors on cores 2–3 and both camera media pipelines on cores
0–1. Both detectors still compete with each other. The masks do not reserve
exclusive cores: other host services and kernel work can still use them. Choose
thread counts and quotas deliberately, then compare complete detection cycles
under the same recording load; a split can slow media processing or analysis.
Recreate the affected camera containers after changing affinity; no image rebuild
or new baseline learning is needed.

The native `bms-pi-camera.service` source and its `rpicam` children run outside
Docker and are not constrained by these Compose settings. On a cgroup v2 host
with the `cpuset` controller enabled, include that source in the media partition
without restarting capture:

```sh
sudo systemctl set-property bms-pi-camera.service AllowedCPUs=0-1
systemctl show bms-pi-camera.service -p AllowedCPUs -p EffectiveCPUs
```

Check that `EffectiveCPUs=0-1`; parent cgroups can further restrict the requested
mask. Without `--runtime`, systemd persists this property across reboots. It also
survives `scripts/restart-pi-camera.sh`, which replaces the base unit but leaves
systemd's property drop-ins intact. To clear this service-specific restriction,
run `sudo systemctl set-property bms-pi-camera.service AllowedCPUs=` and check
`EffectiveCPUs` again; the service then inherits the CPUs allowed by its parent.

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
automatic exposure. The IMX219 output presets are 640x480, 1280x720, 1920x1080,
1640x1232 and 3280x2464. All use the same full-sensor readout to preserve the field
of view. Video offers 10/15 FPS; older higher-rate video profiles are clamped to
15 FPS. Cropped high-speed sensor modes are intentionally excluded.
Video preview remains capped at 10 FPS. Resolution is selectable in both modes.
Snapshot mode shows resolution, a 100/200/500/1000 ms interval and Auto or a fixed exposure from 1/5000 s (0.2 ms) to 1/25 s (40 ms), including 1/2000 s (0.5 ms).
The range applies to manual exposure; Auto remains available. Shutter values are
passed to rpicam in microseconds. Fixed exposure must
not exceed the selected frame interval. The UI shows requested exposure, not a
measurement of sensor metadata. Gain/white balance remain automatic. Resolution
is a shared capture setting: it can be changed directly in either mode. Saved
snapshot videos use the selected snapshot resolution. No settings change until
Apply is clicked.

The host fixes `rpicam-vid --mode 3280:2464:10:P --roi 0,0,1,1` for the installed
IMX219, independently of output dimensions. To use another sensor, set
`PI_CAMERA_SENSOR_MODE` in a systemd service override to its full-view mode.
The ISP resizes the full image, with even-pixel rounding. Wide outputs contain
black side bars to preserve aspect ratio. Direct full-aspect snapshots use native
MJPEG; padded outputs pass uncompressed YUV in NUT through FFmpeg and encode once
as JPEG (snapshots) or H.264 (video). NUT carries dimensions and timestamps and
removes hardware row padding; bare YUV output must not be interpreted as tightly
packed pixels. The host therefore requires `ffmpeg` as well as `rpicam-vid`.
Snapshots produce independent JPEGs without continuously encoding H.264. The dashboard and analysis
consume fresh JPEGs directly, bypassing FFmpeg preview decoding. A frame sequence
and host receive timestamp prevent duplicates/stale images entering the recorder.
The capture device stays open while consumers are active, rather than reopening
it for each photograph. Snapshots use the source's configured capture resolution.

The recorder keeps a time-bounded disk ring under `.snapshot-buffer/` and protects
pre/post-event JPEGs with hard links. At event completion, a background single
FFmpeg worker encodes H.264 MKV, using timestamps to preserve elapsed time and the
selected nominal snapshot FPS. The export timeout scales with frame count, up to
one hour for long, high-resolution events. There are at most two queued exports plus the
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
