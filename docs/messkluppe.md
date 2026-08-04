# Messkluppe Legacy Integration

Messkluppe is a legacy nRF24L01-based clip sensor system found under `~/Documents`.

Source reference:

- Host/Raspberry Pi app: `~/Documents/!FOK/Flask_Messkluppe`
- Older host copy: `~/Documents/MesskluppeMaster-master/_Rpi`
- Node firmware: `~/Documents/MesskluppeMaster-master/*.ino`

The new stack integration should preserve the protocol but avoid importing the old Flask UI, SQLite runtime state, generated CSV files, compiled RF24 artifacts, and Python cache files.

Old SQLite should not be used as the primary database in this stack. The migration target is the existing stack InfluxDB.

Bring-up checklist: see `docs/messkluppe-bringup.md`.

## Protocol

The node sends fixed 32-byte nRF24 payloads. Most node payloads are interpreted as sixteen little-endian `uint16` words. Host commands are encoded as up to eight unsigned 32-bit words, each split to bytes in the same order used by the legacy `translate_to_radio` helper.

The first word is `clip_id * 1000 + task`.

Known task values:

- `0`: idle / ping
- `10`: deep sleep
- `20`: start logging
- `30`: send file list
- `39`: file list finished
- `40`: send file data
- `49`: file download finished
- `50`: delete one file
- `51`: delete all files
- `59`: delete finished
- `60`: live data
- `99`: host acknowledgement / return to idle

File download payloads use a special decode path because `unix_time` and `line_number` are reconstructed from pairs of 16-bit words.

## Integration Direction

The planned service is `messkluppe-collector`:

`Arduino node -> nRF24 binary payloads -> messkluppe-collector -> InfluxDB -> graf-lite/Grafana`

The first runnable version is intentionally host-side only:

- It starts as a Compose profile service, so the default stack is unchanged.
- `MESSKLUPPE_INPUT_MODE=mock` generates synthetic node packets in the same 32-byte legacy binary format and writes decoded records to InfluxDB. This validates the full storage and visualization path before node hardware is ready.
- `MESSKLUPPE_INPUT_MODE=radio` starts the nRF24 RX loop. It configures the radio as PRX, keeps CE high, polls RX FIFO, and forwards any 32-byte payload to the same decoder/Influx path. Without a node it should remain healthy and count empty reads.
- `MESSKLUPPE_FAKE_MODE=1` is still accepted as a compatibility switch and maps to mock input when `MESSKLUPPE_INPUT_MODE` is not set.

Run:

```bash
docker compose --profile messkluppe -f docker-compose.yml -f docker-compose.override.yml up -d messkluppe-collector
```

HTTP endpoints:

- `GET /`: built-in collector status UI.
- `GET /health`: collector health and state.
- `GET /api/status`: collector state.
- `POST /api/mock-node/once`: generate and ingest one mock node packet.
- `POST /api/mock-node/start`: start the mock node packet loop.
- `POST /api/mock-node/stop`: stop the mock node packet loop.
- `POST /api/fake-once`, `POST /api/fake/start`, and `POST /api/fake/stop`: compatibility aliases for the mock node endpoints.
- `POST /api/ingest-hex`: ingest one 32-byte legacy payload as hex, useful for replay tests.
- `GET /api/radio/recent-payloads`: returns the in-memory recent raw radio payload ring buffer as hex.
- `GET /api/radio/recent-commands`: returns the in-memory recent host command payload ring buffer as hex.
- In radio mode, startup begins the RX loop automatically. `GET /api/status` exposes `radio_listening`, `radio_rx_ready`, `radio_rx_packets`, `radio_rx_empty_reads`, `radio_rx_last_at`, `radio_rx_last_payload_hex`, `radio_rx_recent_payloads`, `radio_rx_parse_errors`, and `radio_rx_last_error`.

Legacy-compatible control API skeleton:

- `POST /api/clip/start-logging` with JSON `sample_rate` and `logging_time`.
- `POST /api/clip/stop-logging`.
- `POST /api/clip/reset-mode`.
- `POST /api/clip/deep-sleep/start`.
- `POST /api/clip/deep-sleep/stop`.
- `POST /api/clip/live/start` with JSON `display` (`linearForce` or `raw`).
- `POST /api/clip/live/stop`.
- `GET /api/clip/files`.
- `POST /api/clip/files/download`.
- `POST /api/clip/files/delete`.
- `POST /api/clip/files/delete-all`.

In mock mode these endpoints update collector state and return success. In radio mode they build legacy command payloads and queue them as nRF24 ACK payloads on pipe `1`.

When the dashboard is running, nginx exposes the UI at `/messkluppe/` and the health probe at `/probe/messkluppe`.

Relevant environment variables:

- `MESSKLUPPE_APP_PORT` default `3080`.
- `MESSKLUPPE_INPUT_MODE` default `mock`; allowed values are `mock`, `radio`, and `disabled`.
- `MESSKLUPPE_FAKE_MODE` default `1`.
- `MESSKLUPPE_FAKE_INTERVAL_SEC` default `5.0`.
- `MESSKLUPPE_RADIO_POLL_SEC` default `0.05`.
- `MESSKLUPPE_RADIO_RECENT_PAYLOADS` default `10`.
- `MESSKLUPPE_RADIO_COMMAND_REPEAT_SEC` default `0.25`.
- `MESSKLUPPE_RADIO_LIVE_REPEAT_MODE` default `after_rx`; allowed values are `after_rx`, `timer`, and `off`.
- `MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE` default `0`; set `1` to pad ACK payloads to `MESSKLUPPE_RADIO_PAYLOAD_SIZE`.
- `MESSKLUPPE_CLIP_ID` default `1`.
- `MESSKLUPPE_INFLUX_MEASUREMENT` default `messkluppe_sensor`.
- `MESSKLUPPE_SOURCE_TAG` default `messkluppe`.
- `MESSKLUPPE_RADIO_SPI_BUS` default `0`.
- `MESSKLUPPE_RADIO_SPI_DEVICE` default `0`.
- `MESSKLUPPE_RADIO_CE_GPIO` default `25` for host wiring where nRF24 CE is connected to Raspberry Pi physical pin 22. Legacy host code used BCM GPIO `22` / physical pin 15.
- `MESSKLUPPE_RADIO_CHANNEL` default `111`.
- `MESSKLUPPE_RADIO_PAYLOAD_SIZE` default `32`.
- `MESSKLUPPE_RADIO_SPI_SPEED_HZ` default `4000000`.

Radio diagnostics:

- `POST /api/radio/diagnose`: checks SPI open, CE GPIO setup, and nRF24 register access.
- The collector UI exposes the same check in the `Radio Diagnostics` card.

Radio RX telemetry:

- `radio_listening`: the RX loop has configured nRF24 and is polling.
- `radio_rx_ready`: radio object is open and CE is asserted for PRX.
- `radio_rx_packets`: payloads read from nRF24 RX FIFO.
- `radio_rx_empty_reads`: polls where RX FIFO was empty.
- `radio_rx_last_at`: last payload timestamp.
- `radio_rx_last_payload_hex`: last raw 32-byte payload as hex.
- `radio_rx_recent_payloads`: recent raw payload ring buffer kept in memory only.
- `radio_rx_parse_errors`: radio payloads that reached the decoder but failed parsing.
- `radio_rx_last_error`: last runtime RX error, if any.

Radio TX telemetry:

- `radio_tx_commands`: host commands converted to legacy radio payloads.
- `radio_tx_last_action`: last command action name.
- `radio_tx_last_payload_hex`: last host command payload as hex.
- `radio_tx_last_result`: nRF24 ACK payload queue result when running in radio mode.
- `radio_tx_recent_commands`: recent command ring buffer kept in memory only.
- `radio_tx_errors`: command payload build failures.
- `radio_tx_auto_repeats`: live-mode ACK payload repeats queued by the RX loop.

The TX layer builds and records legacy command payloads. In radio mode it queues those commands as nRF24 ACK payloads on pipe `1`, matching the legacy host `writeAckPayload(1, ...)` pattern. By default ACK payloads keep their natural dynamic length, matching the legacy 8-byte live command. The node receives the command on one of its regular writes if its firmware checks ACK payloads.
While `live_mode` is active, the RX loop refreshes the `1060` live command after received packets by default. `MESSKLUPPE_RADIO_LIVE_REPEAT_MODE=timer` restores the earlier timed repeat cadence.

## v8.0.1 Radio Bring-up Results

Status as of the v8.0.1 checkpoint:

- Host wiring is validated with the nRF24 module on Raspberry Pi SPI0 and CE on BCM GPIO `25` / physical pin `22`.
- Local runtime uses `MESSKLUPPE_INPUT_MODE=radio` and `MESSKLUPPE_FAKE_MODE=0` in `.env`; `.env` remains local runtime state and is not committed.
- The host receives real radio packets from the available test node and writes them to InfluxDB.
- Live mode works through the legacy ACK payload flow. `POST /messkluppe/api/clip/live/start` queues task `60` and the RX loop repeats the command while live mode is active.
- The test node can stream force fields, accelerometer fields, IMU temperature, and yaw/orientation fields without an SD card installed.
- `start_logging` / SD-file workflows were not validated with this test node because it is not the target production node and lacks the expected sensor/interrupt setup.

Observed live record shape:

- `packet_task=60`
- `file_id=radio`
- force: `force_x_raw`, `force_y_raw`, `force_z_raw`
- acceleration: `accel_x_raw`, `accel_y_raw`
- orientation: `yaw_raw`, `yaw_deg`
- diagnostics: `node_millis`, `imu_temperature_raw`, `imu_temperature_c`, `clip_temperature_raw`, `battery_raw`

Orientation handling:

- Node firmware sends yaw as a raw value scaled by `10`.
- The host keeps `yaw_raw` unchanged for diagnostics.
- The host stores `yaw_deg` normalized into one turn (`0 <= yaw_deg < 360`) so firmware wrap/offset artifacts such as raw `4076` become `47.6` degrees.
- During rotation tests, occasional one-sample yaw drops were observed while packet framing, packet size, task id, force, and acceleration fields stayed valid. This points more toward IMU/DMP/node-side yaw samples than toward radio payload corruption.
- Graf App Lite keeps the raw `yaw_deg` series available and adds a client-side `Filter spikes` checkbox on the `Messkluppe Orientation` panel. The checkbox only changes visualization; it does not rewrite InfluxDB data.

Next validation with the real node:

- Re-test live mode after reboot and verify that `packet_task=60` records continue in InfluxDB.
- Validate target-node sensor layout, interrupt behavior, and SD/file commands separately.
- Compare raw and filtered orientation during controlled rotations.
- Decide whether a persisted `yaw_deg_filtered` field is useful, or whether filtering should remain UI-only.

## Visualization

The collector writes decoded force packets to InfluxDB measurement `messkluppe_sensor`.

Influx schema:

- Measurement: `messkluppe_sensor` by default.
- Tags: `source`, `clip_id`, `packet_task`, optional `file_id`.
- Force fields used by the current dashboards: `force_x_raw`, `force_y_raw`, `force_z_raw`.
- Additional decoded fields include `sensor_ms`, `line`, `unix_time`, `accel_x_raw`, `accel_y_raw`, `yaw_raw`, `yaw_deg`, `imu_temperature_raw`, `imu_temperature_c`, `clip_temperature_raw`, and `battery_raw`.
- Mock node samples use `file_id=mock-node`; real downloaded files should use the legacy file timestamp or another stable file identifier.

Graf App Lite exposes the data in two places:

- `/graf/`: the All view includes Messkluppe Force and Orientation panels.
- `/graf/messkluppe`: a dedicated Messkluppe window for force fields and `yaw_deg` orientation.
- The `Messkluppe Orientation` panel has a `Filter spikes` checkbox for client-side one-sample yaw spike suppression.

The dedicated view also provides CSV export through `/graf/api/export/messkluppe.csv`.

Grafana provisioning includes a `Messkluppe Force` panel in the main dashboard. Restart `grafana` after changing provisioning files so the mounted dashboard JSON is reloaded.

Initial code should stay hardware-independent:

- `app/messkluppe/messkluppe_protocol.py`: byte order, task ids, packet parsing, command encoding.
- `app/messkluppe/messkluppe_mock_node.py`: deterministic mock node payload generation for host-only pipeline testing.
- `app/messkluppe/messkluppe_records.py`: decoded packet to Influx-ready records and line protocol.
- `app/messkluppe/messkluppe_app.py`: minimal Flask collector app with mock ingest, radio diagnostics/RX telemetry, and Influx writing.
- `app/messkluppe/messkluppe_radio_rx.py`: nRF24 PRX setup and fixed payload reads.
- Unit tests under `tests/` using synthetic payloads.

Hardware-facing nRF24/RPi GPIO RX code is isolated in `messkluppe_radio_rx.py`. The current loop can listen before the node is ready; command TX and higher-level request/response behavior still need real-node validation.

## Legacy Host Transfer

The current legacy host source has been copied to `app/messkluppe/legacy_host/` as a reference and migration starting point.

Tracked files include:

- Flask app, radio helpers, OLED display script, templates, static UI assets, and legacy systemd unit files.
- `db.schema.sql`, generated from the old `db.db` schema and default settings.
- `static/_csv/.gitkeep`, preserving the runtime CSV directory without committing historical measurements.

Excluded runtime files:

- `db.db`
- `static/_csv/*.csv`
- Python caches and compiled files

The legacy app still contains hardcoded Raspberry Pi paths and old dependencies. It should be treated as imported source, not as a finished Docker service.
