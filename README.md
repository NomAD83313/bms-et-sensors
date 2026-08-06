# BMS ET Sensors Stack

Docker stack for sensor data acquisition and visualization:
- `mscl-collector`: MicroStrain MSCL node configuration + stream ingestion + web API.
- `redlab-collector`: MCC RedLab thermocouple collector.
- `almemo-collector`: ALMEMO serial integration.
- `pyrometer-collector`: unified pyrometer app for thermoMETER CT and Optris CT devices.
- `influxdb`: time-series storage.
- `grafana`: dashboards.
- `graf-lite`: lightweight Python dashboard for low-resource hosts.
- `ap-control`: Raspberry Pi access point operator UI for AP state and connected clients.
- `service-controller`: lightweight control API to start/stop selected service groups from dashboard; guards MSCL, RedLab, and ALMEMO hardware presence.
- `dashboard`: lightweight start page based on `simple-dash`, served by `nginx`.
- `matter-server`: Matter.js Server for controlled Matter diagnostics and collection.
- `matter-collector`: collector that bridges Matter events to InfluxDB.
- `openthread-border-router`: Border Router for Thread mesh (requires OpenThread RCP, USB or network socket).
- `messkluppe-collector`: Messkluppe host-side collector; decodes nRF24 binary payloads and writes InfluxDB records, with fake mode available before node hardware is connected.

The repository includes Matter/Thread stack documentation:

- `docs/openthread.md`: OpenThread Border Router setup and hardware notes.
- `docs/matter-thread-topology.md`: protocol-aligned Matter + OTBR topology model.

ESP32/Matter/Thread node workspace is included in this repository under `nodes`.

## Matter Server status

`matter-server` commissioning in this project supports only two BLE policies: Raspberry Pi internal `hci0`, or no BLE at all.

- Use `MATTER_BLE_MODE=internal ./scripts/restart-matter-server.sh` for BLE commissioning through internal `hci0`.
- Use `MATTER_BLE_MODE=disabled ./scripts/restart-matter-server.sh` for IP-only commissioning of devices that are already reachable on the network.
- Do not use an external USB BLE adapter in this workflow.
- Start Matter services via `./scripts/restart-matter-server.sh` so the container is recreated with the intended `BLUETOOTH_ADAPTER` value.
- Do not restart `matter-server` with raw `docker restart`; that preserves the old `BLUETOOTH_ADAPTER` value after Linux renumbers HCI devices.
- For fresh or factory-reset Thread devices, call `commission_with_code` with the `MT:...` QR payload and without `network_only`.
  `network_only: true` disables BLE discovery and is only for devices already reachable on the IP network.

Known BLE adapter policy:

- Supported: Raspberry Pi internal BLE as `hci0`.
- Supported: no BLE (`MATTER_BLE_MODE=disabled`) for network-only commissioning.
- Unsupported: external USB BLE adapters; this workflow intentionally avoids them.
- Unsupported: MediaTek `0e8d:7961` Wi-Fi/AP combo BLE; it is not suitable for Matter commissioning in this stack.
- Keep Wi-Fi connected during BLE tests; the restart script does not disconnect `wlan0` or `wlan1`.

## AI agent rules

- `AGENTS.md` in the project root is the canonical rule file for Codex.
- Keep Codex-specific project conventions and workflow constraints in `AGENTS.md`.
- Keep the file concise enough to stay within Codex loading limits (current practical target: well below 32 KiB).

## Requirements

- Docker Engine with Docker Compose plugin.
- Linux host with access to sensor USB devices (`/dev` mount is used by containers).
- Optional for local Python-side tests: `python3-venv`

## Fresh Raspberry Pi installation

```bash
git clone https://github.com/NomAD83313/bms-et-sensors.git ~/bms-et-sensors
cd ~/bms-et-sensors
./scripts/rpi-setup.sh
```

The setup creates local runtime files from tracked templates, installs the shared pyrometer USB mappings,
builds the services, and starts the default stack. Runtime data and credentials remain local to each host.

## Quick start

1. Create local env file:

```bash
cp .env.example .env
```

2. Adjust secrets and network values in `.env`.

3. Start stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

4. Open services:
- MSCL web UI/API: `http://<host>/mscl/`
- Pyrometer UI/API: `http://<host>/pyrometer/`
- InfluxDB: `http://<host>:8086`
- Grafana: `http://<host>/grafana/`
- Graf App Lite: `http://<host>/graf/`
- Matter + Thread Console: `http://<host>/matter/`
- Matter Graf (5m shortcut): `http://<host>/graf/matter?range=5m`
- Messkluppe Graf (5m shortcut): `http://<host>/graf/messkluppe?range=5m`
- Messkluppe collector (profile `messkluppe`): `http://<host>:3080`
- Messkluppe collector via dashboard proxy: `http://<host>/messkluppe/`
- Dashboard: `http://<host>:80`
- AP Control: `http://<host>/ap/`
- System health page: `http://<host>/health.html`
- Service control API (internal, via dashboard proxy): `http://<host>/api/control/*`
- Optional local DNS alias for AP clients: `http://<ap-local-name>`

## Install-time vs runtime files

Rule for this repository:
- GitHub should contain only install-time files needed to bootstrap the stack.
- Runtime state generated by containers must stay local and must not be committed.

Tracked install-time templates:
- `.env.example`
- `config/pyrometers-devices.example.json`

Local runtime files created from templates when missing:
- `.env`
- `runtime/pyrometers-devices.json`

Other local runtime/state artifacts that stay out of Git:
- `runtime/`

OpenThread note:
- `OTBR_IMAGE` in `.env` / `.env.example` pins the known-good `openthread/otbr:jammy` digest for this stack.
- That pinned image keeps its active Thread dataset under `/var/lib/thread` inside the container, and this stack persists that state under `runtime/openthread/thread/`.

Checklist for any new service or config file:
- If the file is required to bootstrap a fresh install, track a template or default version in Git.
- If the file is changed by a running container or stores local secrets/state, keep it local and ignore it in Git.
- Prefer `*.example` for install-time templates and create the local runtime file from that template in setup/restart scripts.
- Keep runtime databases, logs, backups, caches and generated UI settings out of the repository.
- When adding a new mounted config path in `docker-compose.yml`, decide immediately whether it is install-time or runtime and update `.gitignore` and setup scripts in the same change.

## Start commands

- Full local rebuild + restart (all custom app services):

```bash
./scripts/restart-local.sh
```

- Start Matter & Thread profile:

```bash
./scripts/restart-openthread.sh
./scripts/restart-matter-server.sh
```

Note:
- RedLab recovery lives inside `service-controller`:
  - detects the MCC USB-TC by USB vendor/product ID instead of serial number
  - keeps `redlab-collector` stopped while the USB-TC is absent
  - starts `redlab-collector` only after the USB device is present and stable
  - any compatible `MCC USB-TC` with the configured USB IDs will be picked up automatically
- MSCL recovery also lives inside `service-controller`:
  - keeps `mscl-collector` stopped while the configured MSCL base serial path is absent
  - starts `mscl-collector` only after the MSCL serial path is present and stable
  - uses `MSCL_PORT` first, then falls back to a `WSDA-Base-200` path in `/dev/serial/by-id`
- ALMEMO serial link settings:
  - set `ALMEMO_BAUD=9600` in `.env` (or keep the default from `.env.example`) to pin UART speed explicitly.
  - `ALMEMO_BAUD` controls host-to-device serial transport only.
  - device-side `Conversion Rate` (visible in ALMEMO UI) is a measurement setting and is independent from UART baud.
- Matter commissioning uses either internal `hci0` BLE or `MATTER_BLE_MODE=disabled` for network-only commissioning.
  - External USB BLE is not used by design.
  - Start Matter services via `./scripts/restart-matter-server.sh` so the selected BLE mode is applied before container recreation.

- Build/restart all custom app services:

```bash
./scripts/build-local-all.sh
```

- Build/restart only MSCL:

```bash
./scripts/build-local-mscl.sh
```

- Build/restart only RedLab:

```bash
./scripts/build-local-redlab.sh
```

- Build/restart only Graf App Lite:

```bash
./scripts/build-local-graf-app.sh
```

- Build/restart only AP Control:

```bash
./scripts/build-local-ap-ui.sh
```

Note:
- this script rebuilds `ap-control`, restarts it, and then restarts `dashboard` so the `/ap/` nginx route is reloaded immediately.

- Build/restart only ALMEMO:

```bash
./scripts/build-local-almemo.sh
```

- Build/restart only Messkluppe collector:

```bash
./scripts/build-local-messkluppe.sh
```

- Build/restart only thermoMETER CT:

```bash
./scripts/build-local-pyrometers.sh
```

- Configure persistent USB aliases/rules for Micro-Epsilon thermoMETER CT and Optris:

```bash
sudo ./scripts/pyrometers-setup.sh --apply
```

On a fresh host, the tracked `config/pyrometers-devices.example.json` template is copied to the ignored
`runtime/pyrometers-devices.json` registry. The template contains the shared lab-device mapping, so the same
USB adapters can move between Raspberry Pi hosts and USB sockets without changing application settings.
Pyrometer runtime modes are configured per host in `runtime/pyrometers-devices.json`:
- `mode: "poll"` sends `0x01` and reads a two-byte temperature response. This is the safest fallback.
- `mode: "stream"` passively listens for the existing marked binary stream (`AA AA` + 4 words).
- `mode: "burst"` starts configured burst output from the app. Use `burst_command_set: "classic_ct"` with `stream_frame_format: "marked_aaaa"` for CT-style marked streams, or the default Optris CTi command set with `stream_frame_format: "burst_words"` for unmarked word bursts.
- Useful burst fields are `burst_command_set`, `stream_frame_format`, `burst_interval_ms`, and `burst_channels` such as `["target", "head", "box", "target"]` for classic CT marked bursts.
- Advanced devices can override generated burst control with `stream_start_hex` and `stream_stop_hex` arrays of raw hex commands.

- Build/restart only Service Control API:

```bash
./scripts/build-local-svcctl.sh
```

- Build/restart only Matter collector UI/API:

```bash
./scripts/build-local-matter-app.sh
```

This rebuilds `matter-collector` only and does not touch `matter-server`; use
`./scripts/restart-matter-server.sh` for Matter Server so the selected BLE mode
is applied first.

- Fast MSCL restart in dev mode (bind-mounted `./app/mscl`):

```bash
./scripts/restart-mscl-dev.sh
```

- Build and push Docker Hub images for `arm64` only.
  Run on a native `arm64` host, without cross-compilation:

```bash
./scripts/build-push-arm64.sh
```

- Backward-compatible wrapper for the old publish command:

```bash
./scripts/build-push-multiarch.sh
```

- Local native build on `x64` host (no cross-compilation):

```bash
./scripts/build-local-x64-all.sh
```

- Raspberry Pi full setup (with local build):

```bash
./scripts/rpi-setup.sh
```

Optional env flags for full Raspberry Pi setup:
- `RPI_SETUP_ENABLE_PYROMETERS=1` keeps `pyrometer-collector` in the stack
- `RPI_SETUP_RUN_PYROMETERS_SETUP=1` applies the pyrometer registry to udev before build/start (default when pyrometers are enabled)

- Raspberry Pi ordered setup wrappers:

```bash
./scripts/01-rpi-setup.sh
./scripts/02-rpi-nm-ap.sh
```

## Dev workflow (mscl-collector)

- The MSCL container now bind-mounts code from host: `./app/mscl:/app` (see `docker-compose.override.yml`).
- `Flask reloader` is intentionally disabled, so runtime remains stable.
- If you change only MSCL code/assets (`*.py`, `*.js`, `*.html`) in `app/mscl`, rebuild is not required:

```bash
./scripts/restart-mscl-dev.sh
```

- Rebuild `mscl-collector` only when dependencies/image inputs changed:
- `Dockerfile.mscl`
- `app/mscl/requirements.txt`
- system packages / base image assumptions

```bash
./scripts/build-local-mscl.sh
```

## Environment variables

Use `.env.example` as the baseline. Key variables:

### InfluxDB / Grafana
- `INFLUX_ORG`
- `INFLUX_BUCKET`
- `INFLUX_TOKEN`
- `INFLUX_ADMIN_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`
- `GRAF_APP_DEFAULT_RANGE`
- `GRAF_APP_REFRESH_SEC`

Current default RedLab USB identity:
- vendor `09db`
- product `0090`
- device family `MCC USB-TC`
- `service-controller` starts `redlab-collector` only after USB presence is stable
- `redlab-collector` applies a short startup warm-up and bootstrap review after reconnect
- `service-controller` starts `mscl-collector` only after the MSCL base serial path is present and stable
- only channels selected in Graf App Lite are written to Influx

### MSCL app (optional advanced tuning)
The app also supports runtime tuning via env variables (batch sizes, queue limits, stream cadence, offsets). Defaults are defined in `app/mscl/mscl_settings.py`.

Shared source tags (used by both `mscl-collector` and `graf-lite`):
- `mscl_sensors`
- `mscl_config_stream`
- `mscl_node_export`

Shared InfluxDB tag style:
- `device` is the primary device identity tag for all sensor measurements.
- `channel` is used only for multi-channel devices.
- `source` is used only when one device/measurement has multiple producer streams.
- Additional tags such as `mode`, `unit`, and `sensor` are measurement-specific metadata.

### Raspberry Pi AP helper
- `AP_NAME`
- `AP_SSID`
- `AP_PASSWORD`
- `AP_BAND`
- `AP_CHANNEL`
- `AP_LOCAL_DNS_ENABLE`
- `AP_LOCAL_DNS_NAME`

## Containers and addresses

| Container | Local address | Docker Hub | GitHub |
|---|---|---|---|
| `dashboard` | `http://<host>:80` | `https://hub.docker.com/_/nginx` | `https://github.com/nginx/nginx` |
| `grafana` | `http://<host>/grafana/` | `https://hub.docker.com/r/grafana/grafana-oss` | `https://github.com/grafana/grafana` |
| `graf-lite` | `http://<host>/graf/` | `https://hub.docker.com/r/nomad375/bms-graf-lite` | `https://github.com/NomAD83313/bms-et-sensors` |
| `ap-control` | `http://<host>/ap/` | `https://hub.docker.com/r/nomad375/bms-ap-control` | `https://github.com/NomAD83313/bms-et-sensors` |
| `service-controller` | internal only (`/api/control/*` via dashboard) | `https://hub.docker.com/r/nomad375/bms-service-controller` | `https://github.com/NomAD83313/bms-et-sensors` |
| `influxdb` | `http://<host>:8086` | `https://hub.docker.com/_/influxdb` | `https://github.com/influxdata/influxdb` |
| `mscl-collector` | `http://<host>/mscl/` | `https://hub.docker.com/r/nomad375/bms-mscl-collector` | `https://github.com/NomAD83313/bms-et-sensors` |
| `redlab-collector` | no host port (internal writer) | `https://hub.docker.com/r/nomad375/bms-redlab-collector` | `https://github.com/NomAD83313/bms-et-sensors` |
| `almemo-collector` | `http://<host>/almemo/` | `https://hub.docker.com/r/nomad375/bms-almemo-collector` | `https://github.com/NomAD83313/bms-et-sensors` |
| `pyrometer-collector` | `http://<host>/pyrometer/` | `https://hub.docker.com/r/nomad375/bms-pyrometer-collector` | `https://github.com/NomAD83313/bms-et-sensors` |
| `matter-collector` | `http://<host>/matter/` | `https://hub.docker.com/r/nomad375/bms-matter-collector` | `https://github.com/NomAD83313/bms-et-sensors` |

## Logs and diagnostics

- Follow all container logs:

```bash
./scripts/logs.sh
```

- Follow one service:

```bash
./scripts/logs.sh mscl-collector
```

- MSCL API health:

```bash
curl -s http://localhost:5000/api/health
```

- MSCL API metrics:

```bash
curl -s http://localhost:5000/api/metrics
```

### Raspberry Pi AP local DNS alias

- `scripts/rpi-nm-ap.sh` configures `NetworkManager + dnsmasq` alias on AP subnet.
- By default, the built-in Wi-Fi is the AP on `10.42.0.1/24` and physical `eth0` is a shared Ethernet AP on `10.42.1.1/24`.
- Set `AP_SECONDARY_INTERFACE` to override the secondary AP interface.
- Numbered wrapper: `scripts/02-rpi-nm-ap.sh`
- Alias is controlled by env vars:
- `AP_LOCAL_DNS_ENABLE=true`
- `AP_LOCAL_DNS_NAME=rpi5.internal`
- AP clients can open:
- `http://rpi5.internal`
- Apply/reapply:
```bash
./scripts/rpi-nm-ap.sh
```

Quick check after AP connect:

- On Raspberry Pi:
```bash
cat /etc/NetworkManager/conf.d/99-bms-dns.conf
cat /etc/NetworkManager/dnsmasq.d/99-bms-ap-local.conf
getent hosts rpi5.internal
```

- On Linux client:
```bash
getent hosts rpi5.internal
ping -c 3 rpi5.internal
```

- On Windows client (PowerShell):
```powershell
Resolve-DnsName rpi5.internal
ping rpi5.internal
```

### Dashboard (simple-dash + nginx)

- Service: `dashboard` (`nginx:latest`)
- Files served from: `dashboard/simple-dash`
- Primary config file: `dashboard/simple-dash/config.json`
- Port mapping: `${DASHBOARD_PORT:-80}:80`
- `{{host}}` placeholder is supported in links and resolves to `window.location.hostname`
- Health page can control service groups (`redlab`, `mscl`, `almemo`, `pyrometer`, `messkluppe`, `grafana`) through `/api/control/*`
- Control does not require a token; access is currently gated only by network reachability to the dashboard

`simple-dash` upstream:
- https://github.com/wiesner-philipp/simple-dash

## Safe cleanup

Cleanup script is project-scoped and does not remove unrelated Docker resources.

- Default stack cleanup:

```bash
./scripts/clean-docker.sh
```

- Docker Hub tag cleanup (keep only selected tags, default is `latest`):

```bash
DOCKERHUB_USER=<user> DOCKERHUB_PASS=<token_or_password> DOCKERHUB_NS=nomad375 DRY_RUN=1 ./scripts/cleanup-dockerhub-tags.sh
```

Then run with `DRY_RUN=0` after reviewing the printed delete plan.

## Recovery procedures

### 1) Base station or node stopped responding
1. Check mscl logs: `./scripts/logs.sh mscl-collector`
2. Trigger reconnect:
```bash
curl -X POST http://localhost:5000/api/reconnect
```
3. If needed, restart mscl container:
```bash
docker compose restart mscl-collector
```

### 2) InfluxDB write problems
1. Verify Influx container is healthy:
```bash
docker compose ps
```
2. Check token/org/bucket values in `.env`.
3. Restart writer containers:
```bash
docker compose restart mscl-collector redlab-collector
```

## Testing

Project tests are `unittest`-based:

```bash
python -m unittest discover -s tests -q
```

For easier local test runs outside containers, bootstrap a local virtualenv once:

```bash
./scripts/setup-local-python.sh
```

Then run tests through it:

```bash
./scripts/test-local.sh
```

Or run a single test module:

```bash
./scripts/test-local.sh tests.test_matter_thread_topology
```

Note:
- `requirements-dev.txt` intentionally covers the common Python test/import dependencies used by local Flask services.
- Hardware/vendor-specific runtime packages such as `uldaq` are not included there, because they are typically validated through container/service flows on the target host.

## Sampling Validation Snapshot

Validation run on February 13, 2026:
- Run artifacts: `tests/runs/20260213-193644/`
- Node: `16904` (`TC-Link-200`)
- Observed LPF options from node read: `294 Hz` only

### Validated LPF × Sample Rate combinations

| Low Pass Filter | Sample Rate | Test | Result |
|---|---|---|---|
| 294 Hz | 1 Hz (`113`) | start/stop, 90s | PASS |
| 294 Hz | 2 Hz (`112`) | start/stop, 90s | PASS |
| 294 Hz | 4 Hz (`111`) | start/stop, 90s | PASS |
| 294 Hz | 8 Hz (`110`) | start/stop, 90s | PASS |
| 294 Hz | 16 Hz (`109`) | start/stop, 90s | PASS |
| 294 Hz | 64 Hz (`107`) | stability run, 300s | PASS |

Additional idle/read/health checks:
- Idle stability run: 150s
- API errors: none
- Read failures: none

Notes:
- Results above are node/firmware-specific and should be treated as a validated snapshot.
- If firmware exposes additional LPF values later, repeat the same validation procedure before using new combinations in production.
