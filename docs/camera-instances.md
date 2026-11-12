# Camera instances: design for 3+ cameras

Status: P1-P5 are implemented. The two existing cameras (`cameras`
profile: RTSP/Wi-Fi, `pi-camera` profile: CSI on the host) must keep working unchanged
at every step.

The next target is two or three Raspberry Pi Zero 2 W source nodes and shared
central media services. See [the proposed migration](camera-zero2w-plan.md).
The current implementation below still creates a recorder/dashboard pair per camera.

Decisions (2026-11-11): extra dashboards use ports 8085 and up; extra cameras record
like the existing ones (recorder + dashboard, shared analysis worker); the shared
worker keeps the current budget of 2 CPUs on cores 2-3.

## Why

Measured on the Pi 5 (4 GB, 2026-11-11, after removing PyTorch from the worker):

| Per camera today | Memory |
| --- | --- |
| `*-anomaly` worker | ~150 MB (was ~400 MB with PyTorch) |
| `*-dashboard` | ~85 MB |
| `*-recorder` | ~25 MB |

A third and fourth camera by copy-pasting three Compose services each would add about
260 MB per camera, plus hand-edited nginx routes, `service-controller` groups and
health-page rows. The earlier 88 ms single-camera analysis measurement does not
establish capacity for three simultaneous cameras: CPU time per frame, decoding,
source resolution, frame gating and the shared CPU budget all matter. Live 8 MP
tests on 2026-11-12 showed a growing preview queue at 10 FPS, even with detection
paused. Validate the complete workload at the intended camera settings.

## Where a camera is hard-coded today

| Place | What is fixed |
| --- | --- |
| `docker-compose.yml` | the original cameras' recorder and dashboard services, ports 8082/8083, per-camera env names (`PI_CAMERA_*`) |
| `dashboard/nginx/default.conf` | `/cameras/`, `/cameras/pi/`, `/probe/cameras`, `/probe/pi-camera` |
| `app/svcctl/svcctl_app.py`, `svcctl_docker.py` | `TARGET_MAP` groups and the service-name allow-list |
| `dashboard/simple-dash/health.html` | two camera rows, names, URLs |
| `app/cameras/anomaly_control.py` | module-level `DATA` (26 uses in `anomaly_worker.py`, 7 in `dashboard.py`) |
| `app/cameras/control.py` | module-level `CONTROL` |

Everything else is already per instance through environment variables (`CAMERA_URL`,
`RECORDING_DIR`, `CAMERA_ANOMALY_DIR`, `CAMERA_CONTROL_DIR`, `DASHBOARD_PORT`, ...).

## Design

1. **Registry.** `config/cameras.example.json` (tracked template) is copied to a local,
   ignored `runtime/cameras.json`. One entry per camera: `id`, `label`, `kind`
   (`rtsp` or `pi-csi`), `port`, optional `url_env` (name of the variable in `.env`
   that holds the URL, so credentials never enter the registry), optional preview
   path. The two existing cameras are entries `wifi` and `pi` that map onto the existing
   service names, directories and ports.
2. **Per-instance paths (P1, done).** `AnomalyWorker(data, control_dir, preview_url,
   recording_mode)` takes its camera's directories, preview URL and recording mode as
   arguments; without arguments it reads the container environment exactly as before.
   The functions in `control.py` accept an optional `directory`, so several cameras can
   toggle their own `paused` flag in one process. `tests/test_cameras_instances.py`
   proves two instances in one process keep separate files, commands, flags and preview
   URLs. The dashboard stays one process per camera.
3. **One shared analysis worker.** A new service runs one process with one
   `AnomalyWorker` per camera, with at most one step in flight for each camera. Steps run
   on a stable thread per camera: waiting for HTTP or rebuilding one baseline does not
   stop another camera or the service heartbeat. Each camera keeps its own
   baseline, sessions, status and command files, so no data migration is needed: the
   existing `runtime/cameras/anomaly` and `runtime/pi-camera/anomaly` directories are
   mounted side by side (`/anomaly/wifi`, `/anomaly/pi`). Each model retains its ONNX
   session; the process shares the imported NumPy, ONNX and FAISS libraries.
   - Frame fetching has a five-second timeout in a camera's own step. A failed camera
     reports `unavailable`; other cameras keep running. Pending worker initialization
     is also dispatched outside the scheduler's heartbeat thread.
   - The container keeps the shared 2-CPU quota and cores 2-3. FAISS thread limits are
     applied in the calling thread before each search. Concurrent heavy operations
     still compete for that fixed CPU budget.
   - Per-camera `try/except` isolates failures; the loop never exits on a camera error.
4. **Extra cameras are recorder + dashboard only.** A script
   (`scripts/add-camera.sh`) reads the registry and writes `runtime/docker-compose.cameras.yml`
   (ignored) with `cam-<id>-recorder` and `cam-<id>-dashboard`, ports from 8085 up.
   `COMPOSE` in `scripts/lib/compose-common.sh` appends that file when it exists.
   The existing hand-written services are not generated, so their names stay.
5. **Generated routing.** nginx includes `runtime/nginx-cameras.conf` generated from the
   registry (`/cameras/<id>/`, `/probe/camera-<id>`). The old `/cameras/` and
   `/cameras/pi/` stay as aliases. `service-controller` builds its groups from the
   registry and exposes them at `/api/cameras`; the health page renders camera rows
   from that list instead of two fixed entries.

## Adding a camera

1. Put the camera's RTSP address (with credentials) into `.env` under a name of your choice,
   for example `CAMERA_GARAGE_URL='rtsp://user:password@10.42.0.50:554/ch0'`. The registry
   only names the variable, so credentials never enter it.
2. Add an entry to `runtime/cameras.json` (created from `config/cameras.example.json` by the
   script; the file is local and ignored by git):

   ```json
   {"cameras": [
     {"id": "garage", "label": "Garage", "url_env": "CAMERA_GARAGE_URL",
      "preview_path": "/ch1", "recording_mode": "anomaly",
      "settings": {"interval_sec": 0.3, "model": {"jpeg_draft": 0, "top_k": 1}}}
   ]}
   ```

   `id` is 1-16 lowercase letters, digits or dashes (not `wifi`, `pi`, `cameras`, `api`...).
   `port` is optional (8085-8199, assigned automatically; 8086 is reserved for InfluxDB).
   Explicit reserved ports are rejected too. `preview_path` is the optional
   low-resolution substream used by the browser preview; `recording_mode` and `settings`
   fall back to the global values. Without a per-camera `recording_mode`, recorder,
   dashboard and analysis worker all inherit `CAMERA_RECORDING_MODE` at container start.
   The generated analysis list leaves that optional field unset instead of forcing
   `continuous`; explicit camera overrides take priority.
3. Run `scripts/cameras-apply.sh`. It validates the registry, writes
   `runtime/docker-compose.cameras.yml` (recorder and dashboard services
   `cam-<id>-recorder` / `cam-<id>-dashboard`, same image and options as the Wi-Fi camera),
   `runtime/camera-registry/analysis-extra.json` (for the shared worker) and the nginx routes,
   starts the new containers, restarts the shared analysis worker (it reads the camera list
   at start) and reloads nginx. The camera
   is then at `/cameras/<id>/` and appears in System Health. Removing an entry and running the
   script again removes that camera's containers; its recordings stay under
   `runtime/cameras-extra/<id>/`.
4. Open the camera page, select the analysis area and learn the normal view as for the other
   cameras.

Every extra camera adds a recorder (about 25 MB), a dashboard (about 85 MB) and a small share
of the shared worker. Recordings, controls and analysis data live under
`runtime/cameras-extra/<id>/` (ignored by git).

## Phases (each shippable on its own)

| Phase | Change | Risk |
| --- | --- | --- |
| P1 | Per-instance paths for the worker and `control.py`, no behaviour change (done) | low: covered by the existing tests plus the new instance tests |
| P2 | Several `AnomalyWorker` instances in one process: `analysis_service.py` and the Compose service `camera-analysis` (done) | medium: new scheduler |
| P3 | Registry, generated Compose, nginx routes, service-controller discovery and one combined Cameras block on the Health page (done) | low: additive |
| P4 | `service-controller` and Health aware of the shared worker; lock hand-over without restart loops (done) | medium: touches shared stack |
| P5 | Both original cameras use the shared worker; the `camera-anomaly` and `pi-camera-anomaly` services, the mode switch and its script are removed (done, 2026-11-11) | medium: rollback is `git revert` |

P2 measurements (2026-11-11, Pi 5, two cameras, MobileNetV3): one shared process used
175 MiB and 20 % of a core for both cameras, against 88 + 153 MiB for two separate
workers; both cameras' status files refreshed every 0.1-0.2 s and the Pi analysis kept
its settings (`jpeg_draft`, `top_k = 5`) and its baseline. The saving is the duplicated
runtime (about 65 MB for two cameras) and grows with every added camera.

Final design (P4/P5, 2026-11-11): there is one analysis worker, `camera-analysis`, and it is
part of every camera Compose profile.

- `service-controller` adds it to every camera group's status. Start on a group starts its
  recorder and dashboard and makes sure the shared worker runs; Stop stops only the group's
  recorder and dashboard, because the shared worker keeps serving the other cameras.
- A lock in each camera's `/anomaly` directory (`.worker.lock`) keeps two workers from
  analysing one camera. A worker started by hand (`anomaly_worker.py`) waits for the lock and
  retries every 10 s; the shared service retries cameras that are owned elsewhere. Whichever
  process holds the lock does the analysis; stopping it hands the camera over.
- An unreachable camera is retried every 2 s and logged once a minute, so a camera whose
  dashboard is not running costs almost nothing.

Frame gating (skip unchanged ROI frames) is independent of this design and lowers the
per-camera cost further; it should land before the third camera.

## Open decisions

- The camera id format (proposal: lowercase letters, digits, dash; at most 16 characters).
- Optional stream hub (go2rtc or MediaMTX) in front of the sources, so each camera is
  pulled once instead of by recorder, preview and analysis separately; it would be one
  more registry field (`url`) pointing at the hub.
- A camera's effective interval is bounded by its own processing time and the shared
  CPU budget; another camera's network timeout does not extend it.
- Camera sources: RTSP (H.264 over Wi-Fi) for remote nodes; JPEG snapshots stay a
  local-CSI-only path.
