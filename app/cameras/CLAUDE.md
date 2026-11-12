# CLAUDE.md — app/cameras

Camera stack: recording, live dashboard and anomaly detection for RTSP (Wi-Fi) and
Raspberry Pi CSI cameras. Complements the root `AGENTS.md`; the root rules apply here
(answer the user in Russian, code and comments in English, minimal scoped changes, never
commit `runtime/` or secrets).

## Architecture

Two containers in the Compose profile `cameras` (started by `scripts/restart-cameras.sh`):
`camera-media` (`camera_manager.py`) and `camera-analysis` (`analysis_service.py`).
The manager serves one UI/API on port 8082 (nginx `/cameras/`) and supervises an
independent `recorder.py` + `dashboard.py` process group per camera. Per-camera dashboards
listen on ephemeral loopback ports; only the manager is exposed. Individual processes
remain isolated; the container does not share their Python heaps.

The private registry is `runtime/camera-manager/cameras.json` (mode 600, RTSP credentials);
never expose its URLs in public status or logs. The manager writes the analysis list
`runtime/camera-manager/analysis.json`; registry updates add or remove analysis workers
without restarting the others. Per-camera `camera-stopped` markers prevent analysis after
Stop. State directories are identical in both containers: `/data/pi` (built-in CSI camera,
host `runtime/pi-camera`) and `/data/extra/<id>` (every other camera, host
`runtime/cameras-extra/<id>`).

`camera-analysis` runs one `AnomalyWorker` (`anomaly_worker.py`) per camera. Each camera has
at most one step in flight on its own pool thread, kept stable for native allocator reuse;
network waits and training do not block the scheduler heartbeat or other cameras.

service-controller: the `cameras` target starts/stops the containers (hub, media, analysis);
`cam-<id>` targets are forwarded to camera-media and stop only that camera's processes.

Optional `docker-compose.camera-hub.yml` adds one MediaMTX relay (`camera-hub`),
enabled by `scripts/restart-camera-hub.sh` (marker `runtime/camera-hub/enabled`).
`stream_hub.py` writes the private runtime configuration atomically; MediaMTX watches the
mounted directory and reloads paths. The registry retains original RTSP URLs; both media
consumers use the local relay URL. CSI sources bypass it. Paths are pulled on demand, once
per camera regardless of reader count. Never publish the hub config or source credentials.

The Pi CSI camera's source runs on the host: systemd `bms-pi-camera` (`pi_source.py`,
rpicam-vid) on 127.0.0.1:8084, installed by `scripts/pi-camera-host-setup.sh`.
Containers use host networking and `cpuset` (media cores 0-1, analysis cores 2-3).
Detection details: `docs/camera-anomaly.md`; plan for remote nodes: `docs/camera-zero2w-plan.md`.

## Modules

- `patchcore_lite.py` — PatchCore-style detector: NumPy + ONNX Runtime (XNNPACK) + FAISS.
  Baseline is `baseline.npz` (version-tagged; bump on format change). **No PyTorch at
  runtime** — it exists only in the `exporter` and `test` Docker stages.
- `frame_gate.py` — skips analysis of unchanged frames (RGB ROI thumbnail block diff,
  including partial edge blocks). Imports
  numpy/PIL lazily so host tests keep running.
- `anomaly_worker.py` — one camera's analysis loop; exclusive `.worker.lock` per data dir
  (`WorkerLocked`), error log throttled to once per minute, legacy baseline migration.
- `analysis_service.py` — shared runner: `parse_cameras`, `load_cameras`, `build_workers`,
  `Scheduler` (round-robin by due time, offline retry 2 s).
- `camera_manager.py`, `web/fleet.*` — registry (`validate_camera`, `CAMERA_ID`,
  `MODEL_OPTIONS`), public UI/API, process-group supervision and streaming proxy. HTTP streaming preserves ranges and frame tokens.
  Stop persists before process shutdown and pauses analysis while preserving learning.
- `source.py`, `preview_quality.py` — FFmpeg input options, `FFMPEG` prefix, `stop_process`,
  capture settings; `DecodeGuard` drops frames after decoder errors (1 s cooldown).
- `clips.py` — clip retention (`prune`) and FFmpeg progress parsing shared by the recorder modes.
- `files.py`, `environment.py` — atomic JSON/text writes; recording settings read from the
  environment with one default each (`recording_mode`, `event_windows`, limits).
- `control.py`, `anomaly_control.py` — file-based control/heartbeat between dashboard and
  worker (functions take an optional `directory`). `anomaly_control.MODE_AFTER_ACTION` is the
  single table of analysis actions; the dashboard derives its routes from it.
- `learning_sessions.py`, `roi.py`, `capture_settings.py`, `model_profiles.py`,
  `event_recording.py`, `snapshot_recording.py`, `history.py` — learning, ROI, settings,
  clips and history.

## Rules

- Keep the worker torch-free. Never import torch/torchvision/timm outside the exporter
  and tests.
- Settings, control files and logs are per camera (`name`); never share state between
  cameras, and never start two workers on one data directory.
- Restarting `camera-analysis` aborts any learning session in progress. Check the
  camera `status.json` state (`learning`/`training`) before recreating it.
- One camera's Start/Stop must never stop a shared container.
- Registry and hub files contain RTSP credentials: never print, log or copy them.
- Keep Raspberry Pi 5 resource limits (`mem_limit`, `cpuset`) and low-overhead polling.
- Runtime state lives under `runtime/` (gitignored). Adding a mounted path means
  classifying it and updating `.gitignore` in the same change.
- Add or update tests for behaviour changes, and update the docs above.
- `anomaly_worker.py` imports `control`, `anomaly_control` and `patchcore_lite` by their flat
  names on purpose: the tests load it with `load_isolated_module` and substitute those
  modules. New dependencies there need the `try: from .x … except ImportError` form.
- `pi_source.py` runs on the host with only `capture_settings.py` next to it
  (`scripts/pi-camera-host-setup.sh`); it must not import other modules of this folder.

## Tests and commands

- `./scripts/test-cameras.sh` builds the `test` image target and runs all camera tests
  (includes the PyTorch reference tests; they are skipped without torch).
- Host: `.venv/bin/python -m unittest tests.test_cameras_manager tests.test_svcctl_cameras -q`
  (manager and controller tests run without numpy/torch).
- UI tests: `node tests/cameras_health_ui.js` (and the other `tests/cameras_*_ui.js`).
- Restart (and rebuild) the cameras: `./scripts/restart-cameras.sh`; it refuses while a
  camera is learning. Pi host source alone: `./scripts/pi-camera-host-setup.sh`.
- Setup (Pi): `./scripts/03-rpi-cameras-setup.sh`; ONNX XNNPACK image:
  `./scripts/build-camera-onnx-xnnpack.sh`.
- Measure: `./scripts/benchmark-cameras.py` (whole camera stack, per component).

## Known issues

- Thingino main stream (2304×1296) truncates keyframes (~88 KB) and causes decoder
  errors; the fix is on the camera side (lower resolution/bitrate of stream 0).
- Pi Zero source nodes are deferred until the hardware is ready.
