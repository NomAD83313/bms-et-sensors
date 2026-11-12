# CLAUDE.md — app/cameras

Camera stack: recording, live dashboard and anomaly detection for RTSP (Wi-Fi) and
Raspberry Pi CSI cameras. Complements the root `AGENTS.md`; the root rules apply here
(answer the user in Russian, code and comments in English, minimal scoped changes, never
commit `runtime/` or secrets).

## Architecture

Per camera: `camera-recorder` (`recorder.py`) + `camera-dashboard` (`dashboard.py`, Flask
UI in `web/`). All cameras share **one** analysis worker, `camera-analysis`
(`analysis_service.py`), which schedules the cameras through `AnomalyWorker`
(`anomaly_worker.py`). Each camera has at most one step in flight on its own pool
thread, kept stable for native allocator reuse; network waits and training do not block
the scheduler heartbeat or other cameras.

| Camera | Compose services | Dashboard | Source |
| --- | --- | --- | --- |
| `wifi` | `camera-recorder`, `camera-dashboard` | 8082 | RTSP URL from `.env` |
| `pi` | `pi-camera-recorder`, `pi-camera-dashboard` | 8083 | host systemd `bms-pi-camera` (`pi_source.py`, rpicam-vid) on 127.0.0.1:8084 |
| extra | `cam-<id>-recorder`, `cam-<id>-dashboard` | 8085–8199 | generated from `runtime/cameras.json` |

Compose profiles: `cameras`, `pi-camera`, `cameras-extra`. Containers have fixed short
`container_name`s equal to the service names. Services use host networking and `cpuset`
(media cores 0-1, analysis cores 2-3).

Design and decisions for multi-camera: `docs/camera-instances.md`. Detection details:
`docs/camera-anomaly.md`.

## Modules

- `patchcore_lite.py` — PatchCore-style detector: NumPy + ONNX Runtime (XNNPACK) + FAISS.
  Baseline is `baseline.npz` (version-tagged; bump on format change). **No PyTorch at
  runtime** — it exists only in the `exporter` and `test` Docker stages.
- `frame_gate.py` — skips analysis of unchanged frames (RGB/grayscale ROI thumbnail block diff,
  including partial edge blocks). Imports
  numpy/PIL lazily so host tests keep running.
- `anomaly_worker.py` — one camera's analysis loop; exclusive `.worker.lock` per data dir
  (`WorkerLocked`), error log throttled to once per minute, legacy baseline migration.
- `analysis_service.py` — shared runner: `parse_cameras`, `load_cameras`, `build_workers`,
  `Scheduler` (round-robin by due time, offline retry 2 s).
- `camera_registry.py` — validates `runtime/cameras.json` and generates
  `runtime/docker-compose.cameras.yml`, `runtime/camera-registry/analysis-extra.json` and
  nginx `cameras.conf`. Reserved ids: wifi, pi, cameras, pi-camera, analysis, api, probe.
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
- Stopping a camera group via service-controller must never stop `camera-analysis`.
- `CAMERA_URL` contains credentials: never print, log or copy it.
- Keep Raspberry Pi 5 resource limits (`mem_limit`, `cpuset`) and low-overhead polling.
- Runtime state lives under `runtime/` (gitignored). Adding a mounted path means
  classifying it and updating `.gitignore` in the same change.
- Add or update tests for behaviour changes, and update the docs above.
- `anomaly_worker.py` imports `control`, `anomaly_control` and `patchcore_lite` by their flat
  names on purpose: the tests load it with `load_isolated_module` and substitute those
  modules. New dependencies there need the `try: from .x … except ImportError` form.
- `pi_source.py` runs on the host with only `capture_settings.py` next to it
  (`scripts/restart-pi-camera.sh`); it must not import other modules of this folder.

## Tests and commands

- `./scripts/test-cameras.sh` builds the `test` image target and runs all camera tests
  (includes the PyTorch reference tests; they are skipped without torch).
- Host: `.venv/bin/python -m unittest tests.test_cameras_registry -q` (worker/registry
  tests run without numpy/torch).
- UI tests: `node tests/cameras_health_ui.js` (and the other `tests/cameras_*_ui.js`).
- Apply registry changes: `./scripts/cameras-apply.sh`.
- Restart: `./scripts/restart-cameras.sh`, `./scripts/restart-pi-camera.sh`.
- Setup (Pi): `./scripts/03-rpi-cameras-setup.sh`; ONNX XNNPACK image:
  `./scripts/build-camera-onnx-xnnpack.sh`.
- Measure: `./scripts/benchmark-cameras.py` (shared worker is measured once).

## Known issues

- Thingino main stream (2304×1296) truncates keyframes (~88 KB) and causes decoder
  errors; the fix is on the camera side (lower resolution/bitrate of stream 0).
- Pi Zero source nodes and a MediaMTX/go2rtc hub are deferred until the hardware is
  ready.
