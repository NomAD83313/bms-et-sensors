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

- Frozen ImageNet ResNet-18 layer2/layer3, 224 × 224 full-frame input;
  channels-last convolution layout for faster ARM CPU inference. This preserves
  the feature definition and existing learned baselines.
- Locally averaged, concatenated patch features with a seeded projection to 128
  dimensions; up to 8,000 candidate patches and a 1,024-patch greedy coreset
  selected in a seeded 32-dimensional projection.
- Maximum nearest-memory patch distance; no original PatchCore score reweighting.
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

The CPU-only PyTorch model and weights are installed during Docker build. Camera
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
