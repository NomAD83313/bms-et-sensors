# ANTIGRAVITY.md — Agent Reference & Architecture Guide

This document is a technical cheatsheet and architectural reference for the Antigravity agent operating in the `bms-et-sensors` repository. It complements `AGENTS.md` (mandatory coding rules) and `README.md` (runtime documentation).

---

## 1. Project Overview & System Topology

**BMS ET Sensors Stack** is a Docker-based acquisition, processing, visualization, and monitoring stack running on Linux (primarily Raspberry Pi 4/5 on Debian / Raspberry Pi OS `bookworm` arm64, as well as x86_64 hosts).

### Core Data Flow:
```
[Sensors / Hardware] ──> [Collector Services] ──> [InfluxDB 2.7] ──> [Grafana / Graf-Lite]
                                │                         │
                                └──> [Nginx Reverse Proxy] ◄── [User / Web Browser]
```

### Compose Profiles & Services
| Profile | Service Name | Container Image | Host / Internal Port | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| *(default)* | `influxdb` | `influxdb:2.7` | `8086:8086` | Time-series database |
| *(default)* | `grafana` | `grafana/grafana-oss` | Subpath `/grafana/` | Rich metrics dashboards |
| *(default)* | `graf-lite` | `bms-graf-app:local` | `3010` (`/graf/`) | Lightweight mobile/low-resource UI |
| *(default)* | `dashboard` | `bms-dashboard:local` | `80:80` | Nginx reverse proxy + Simple-Dash UI |
| *(default)* | `service-controller` | `bms-svcctl:local` | `3020` (`/api/control/`) | Start/stop service groups with safety guards |
| *(default)* | `ap-control` | `bms-ap-ui:local` | `3070` (`/ap/`) | Raspberry Pi Wi-Fi AP & client status |
| *(default)* | `mscl-collector` | `nomad375/bms-mscl-collector` | `5000` (`/mscl/`) | MicroStrain wireless sensor stream & API |
| *(default)* | `redlab-collector` | `nomad375/bms-redlab-collector` | `8090` (`/redlab/`) | MCC RedLab thermocouple DAQ |
| *(default)* | `almemo-collector` | `nomad375/bms-almemo-collector` | `3040` (`/almemo/`) | Ahlborn ALMEMO serial logger |
| *(default)* | `pyrometer-collector` | `nomad375/bms-pyrometer-collector`| `3050` (`/pyrometer/`) | Optris / thermoMETER CT serial pyrometers |
| `cameras` | `camera-media` | `bms-cameras:local` | `8082` (`/cameras/`) | Camera registry, preview proxy & H.264 recording |
| `cameras` | `camera-analysis` | `bms-camera-analysis:local` | (internal) | PatchCore-lite visual anomaly detection |
| `camera-hub` | `camera-hub` | `bluenviron/mediamtx` | `8554` (RTSP) | Optional RTSP relay for Zero 2 W cameras |
| `vibration` | `vibration-collector` | `nomad375/bms-vibration-collector` | `3090` (`/vibration/`) | WitMotion WTVB01-485 Modbus RTU sensors |
| `messkluppe` | `messkluppe-collector`| `nomad375/bms-messkluppe-collector`| `3080` (`/messkluppe/`) | nRF24 caliper payload ingestion |
| `thread` | `openthread-border-router` | `openthread/otbr:jammy` | (host networking) | Thread Border Router (SonoffE RCP dongle) |
| `matter` | `matter-server` | `ghcr.io/matter-js/matterjs-server`| `5580` (host networking)| Matter.js Server |
| `matter` | `matter-collector` | `nomad375/bms-matter-collector` | `3060` (`/matter/`) | Matter WebSocket bridge to InfluxDB |
| `tunnel` | `cloudflared` | `cloudflare/cloudflared` | (outbound tunnel) | Cloudflare Tunnel for remote access |

---

## 2. Directory Structure & Key Files

- `app/` — Application source code for collectors, UIs, and controllers:
  - `mscl/` — MicroStrain MSCL integration, Flask API, base-station lock (`/tmp/mscl/base.lock`).
  - `redlab/` — MCC RedLab USB DAQ, USB re-enumeration debounce & recovery logic.
  - `almemo/` — Ahlborn ALMEMO V6/V7 protocol handler, flow control (`XON/XOFF`), session switching.
  - `pyrometers/` — Optris & Micro-Epsilon thermoMETER CT serial driver and polling loop.
  - `messkluppe/` — nRF24 wireless receiver (SPI, CE pin 25, channel 111) and mock node generator.
  - `vibration/` — WitMotion WTVB01-485 Modbus RTU collector, FFT computation from raw acceleration FIFO.
  - `cameras/` — Camera stack: `camera_manager.py` (process supervisor), `recorder.py`, `analysis_service.py`, `patchcore_lite.py` (anomaly detection), `frame_gate.py`.
  - `ap/` — Raspberry Pi AP monitor (`NetworkManager`, `iw`, DHCP leases).
  - `graf/` — Graf App Lite (direct InfluxDB client and dashboard).
  - `matter/` — Matter collector connecting via WebSocket to `matter-server`.
  - `svcctl/` — Service controller API with hardware safeguards.
  - `shared/` — Common helpers (logging, influx writer).
- `config/` — Install-time templates (`*.example.json`, systemd unit templates).
- `dashboard/` — Nginx configuration (`nginx/default.conf`) and Simple-Dash static frontend.
- `grafana/` — Provisioned data sources and dashboards (`grafana/provisioning/`).
- `nodes/` — ESP32 firmware projects (Matter over Thread / Wi-Fi) with separate `nodes/AGENTS.md`.
- `scripts/` — System management, setup, build, restart, and maintenance scripts:
  - `scripts/lib/` — Shared shell helpers.
- `tests/` — Comprehensive unittest suite (900+ test cases).
- `docs/` — Hardware manuals, protocol specifications, setup guides.
- `runtime/` — Local runtime state, logs, caches, databases (**NEVER COMMIT**).

---

## 3. Critical Hardware & Architectural Rules

### Serial Port Conflict Prevention
Multiple USB-to-serial adapters share the host. Auto-detection collisions will crash collectors or freeze hardware:
1. **USB Vendor/Product Exclusions**:
   - `10c4:834b` (Silicon Labs CP210x) and `1a86:7523` (QinHeng CH340) must remain in the exclusion lists (`MSCL_EXCLUDE_USB_IDS`, `ALMEMO_EXCLUDE_USB_IDS`).
2. **Vibration Sensors (RS485/Modbus)**:
   - Uses a CH340 adapter (`1a86:7523`).
   - **NEVER** auto-detect the vibration serial port. The port is explicitly configured by the user via UI and stored in `runtime/vibration/config.json`.
   - Modbus register writes follow strict sequence: `unlock (0x69)` -> `write` -> `save (0x00)`.
3. **Pyrometers**:
   - Use fixed udev rules installed by `scripts/pyrometers-setup.sh`:
     - `/dev/ttyMICROEPS1`
     - `/dev/ttyOPTRIS1`
   - Registry is stored in `runtime/pyrometers-devices.json`.
4. **ALMEMO Flow Control**:
   - Uses software flow control (`XON/XOFF`).
   - Command channels and live streaming require clean session switching; avoid rapid fire serial commands.

### Camera Stack & Anomaly Detection
- **No PyTorch at runtime**: Containers run ONNX Runtime with XNNPACK + FAISS for feature search. PyTorch is used only in export/test stages.
- **CPU Partitioning**:
  - `camera-media`: Cores 0-1 (`cpuset: ${CAMERA_MEDIA_CPUSET}`).
  - `camera-analysis`: Cores 2-3 (`cpuset: ${CAMERA_ANOMALY_CPUSET}`).
- **Memory Limits**: Max 768 MB per container; requires memory cgroup on Raspberry Pi (`cgroup_enable=memory cgroup_memory=1` in `/boot/firmware/cmdline.txt`).
- **Privacy & Security**: RTSP credentials in `runtime/camera-manager/cameras.json` (chmod 600) must **never** be exposed in public logs or API responses.

### Matter & OpenThread
- **Profiles**: Gated by Compose profiles `thread` and `matter`.
- **Bluetooth Adapter Policy**:
  - Only internal Raspberry Pi BLE (`hci0`) via `MATTER_BLE_MODE=internal`.
  - Or disable BLE via `MATTER_BLE_MODE=disabled` for network-only commissioning.
  - External USB BLE adapters are strictly unsupported.
- **Thread Security**: Never log or print the output of `ot-ctl dataset active -x` (contains network credentials).

### Remote Access & Cloudflare Tunnel
- Profile `tunnel` runs `cloudflared`.
- Nginx security: Rejects external requests missing a valid Cloudflare Access JWT assertion (`$http_cf_access_jwt_assertion`) to prevent unauthenticated internet exposure.

---

## 4. Install-Time vs Runtime Policy

| Category | Tracked in Git | Local Runtime Only (Git-Ignored) |
| :--- | :--- | :--- |
| **Environment** | `.env.example` | `.env` |
| **Pyrometers** | `config/pyrometers-devices.example.json` | `runtime/pyrometers-devices.json` |
| **Cameras** | `config/cameras.example.json`, `config/camera-hub.example.json` | `runtime/camera-manager/cameras.json`, `runtime/camera-hub/` |
| **Vibration** | (Defaults in code / protocol) | `runtime/vibration/config.json` |
| **Thread / Matter** | `config/otbr-web.default` | `runtime/openthread/`, `runtime/matter-server/`, `runtime/matter-collector/` |
| **Storage & Logs** | None | `runtime/` (*all subfolders*) |

*Rule*: If introducing a new configuration, track `config/<name>.example.json` and bootstrap it in scripts. Never commit active credentials or state.

---

## 5. Standard Commands & Cheat Sheet

### Running Tests
Always run unit tests before and after making changes:
```bash
# Full test suite (sandboxed)
python3 -m unittest discover -s tests -q

# Targeted test examples
python3 -m unittest tests/test_vibration_protocol.py
python3 -m unittest tests/test_cameras_patchcore.py
python3 -m unittest tests/test_almemo_protocol.py
python3 -m unittest tests/test_mscl_read_service.py
```

### Stack Lifecycle
```bash
# Restart default stack (rebuilds local images)
./scripts/restart-local.sh

# Rebuild all local service images
./scripts/build-local-all.sh

# Rebuild specific service
./scripts/build-local-mscl.sh
./scripts/build-local-redlab.sh
./scripts/build-local-almemo.sh
./scripts/build-local-pyrometers.sh
./scripts/build-local-vibration.sh
./scripts/build-local-graf-app.sh
./scripts/build-local-svcctl.sh

# Start optional profiles
./scripts/restart-cameras.sh          # Profile `cameras`
./scripts/restart-camera-hub.sh       # Profile `camera-hub`
./scripts/restart-openthread.sh       # Profile `thread`
./scripts/restart-matter-server.sh    # Profile `matter`
./scripts/restart-tunnel.sh           # Profile `tunnel`

# Diagnostics & Logs
./scripts/logs.sh                     # Follow all logs
./scripts/logs.sh <service-name>      # Follow specific service
```

---

## 6. Antigravity Agent Working Checklist

1. **Language Requirement**:
   - Communicate with the user in **Russian**.
   - Write all code, comments, documentation, commit messages, and variable names in **English**.
2. **Pre-flight Check**:
   - Check existing repository state with `git status --short`.
   - Ensure you are working on a clean working tree.
3. **Smallest Coherent Patch**:
   - Touch only files directly relevant to the prompt.
   - Do not refactor unrelated modules or rewrite working configurations.
   - Preserve existing Compose wiring and service naming.
4. **Verification**:
   - Run relevant unit tests (`python3 -m unittest ...`).
   - If UI or assets changed, remind the user about browser hard refresh.
5. **Post-flight Review**:
   - Verify `git status --short` to ensure no temporary, secret, or runtime files under `runtime/` were staged or added.
   - Summarize changes, reasoning, and any residual risks clearly.
