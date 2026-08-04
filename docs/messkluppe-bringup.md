# Messkluppe Radio Bring-Up

This checklist is for the first host/node test after the nRF24 module is wired to the host.

## Host Wiring

Current host wiring uses Raspberry Pi physical pins:

- Physical 19: SPI0 MOSI / BCM GPIO10
- Physical 21: SPI0 MISO / BCM GPIO9
- Physical 22: nRF24 CE / BCM GPIO25
- Physical 23: SPI0 SCLK / BCM GPIO11
- Physical 24: SPI0 CE0 / BCM GPIO8 / CSN

The stack default is therefore `MESSKLUPPE_RADIO_CE_GPIO=25`. The legacy host code used BCM GPIO `22`, which is Raspberry Pi physical pin 15.

## Start Radio Mode

Use radio mode when the host nRF24 module is connected:

```bash
MESSKLUPPE_INPUT_MODE=radio MESSKLUPPE_FAKE_MODE=0 ./scripts/build-local-messkluppe.sh
```

For normal compose startup, set these values in the environment before starting the profile:

```bash
export MESSKLUPPE_INPUT_MODE=radio
export MESSKLUPPE_FAKE_MODE=0
docker compose --profile messkluppe -f docker-compose.yml -f docker-compose.override.yml up -d messkluppe-collector
```

## One-Command Smoke Test

Run:

```bash
./scripts/messkluppe-radio-smoke.sh
```

Expected before the node is powered:

- `input_mode=radio`
- `collector_running=True`
- `radio_listening=True`
- `radio_rx_ready=True`
- `radio_rx_packets=0`
- `radio_rx_empty_reads` increases over time
- `radio_rx_errors=0`
- result line: `PASS: radio is listening; no node packets received yet`

Expected after the node starts sending:

- `radio_rx_packets` increases
- `radio_rx_last_payload_hex` is populated
- `/messkluppe/api/radio/recent-payloads` contains recent raw payloads
- Graf Lite `/graf/messkluppe` shows Messkluppe force series after successful decode/write

## Manual API Checks

Health and status:

```bash
curl -fsS http://127.0.0.1/messkluppe/health | python -m json.tool
curl -fsS http://127.0.0.1/messkluppe/api/status | python -m json.tool
```

Radio diagnostics:

```bash
curl -fsS -X POST http://127.0.0.1/messkluppe/api/radio/diagnose | python -m json.tool
```

When the RX loop is active, diagnostics returns the runtime RX status instead of trying to claim CE GPIO a second time.

Recent raw payloads:

```bash
curl -fsS http://127.0.0.1/messkluppe/api/radio/recent-payloads | python -m json.tool
```

Recent host command payloads:

```bash
curl -fsS http://127.0.0.1/messkluppe/api/radio/recent-commands | python -m json.tool
```

The command TX layer currently builds and records legacy command payload hex. Hardware TX is still pending until the node is available for request/response validation.

Replay one captured payload:

```bash
curl -fsS -X POST http://127.0.0.1/messkluppe/api/ingest-hex \
  -H 'Content-Type: application/json' \
  -d '{"payload_hex":"PASTE_HEX_HERE","file_id":"replay"}' | python -m json.tool
```

## Troubleshooting

If `radio_listening=false` or `radio_rx_ready=false`:

- Check `/dev/spidev0.0` exists on the host.
- Check SPI is enabled on the Raspberry Pi.
- Check the container is running privileged and has `/dev` mounted.
- Check CE wiring and `MESSKLUPPE_RADIO_CE_GPIO`.
- Run `curl -fsS -X POST http://127.0.0.1/messkluppe/api/radio/diagnose | python -m json.tool`.

If `radio_rx_empty_reads` increases but `radio_rx_packets=0`:

- Host nRF24 is listening, but no valid payload is reaching RX FIFO.
- Check node power and node firmware radio channel.
- Confirm both sides use channel `111`, payload size `32`, and RX pipe address `0xABCDABCD71`.
- Check CE/CSN are not swapped on the node.
- Move node closer to host for the first test.

If `radio_rx_packets` increases but `packets_received` does not:

- Radio payloads are arriving but decode is failing.
- Open `/messkluppe/api/radio/recent-payloads` and copy the newest `payload_hex`.
- Replay it through `/api/ingest-hex`.
- Check `radio_rx_parse_errors` and `last_error`.

If `packets_received` increases but `records_written` does not:

- Decode works, but Influx writing is failing.
- Check `influx_configured`, `write_errors`, and `last_error` in `/api/status`.
- Confirm `INFLUX_TOKEN`, `INFLUX_ORG`, and `INFLUX_BUCKET` are set for `messkluppe-collector`.

## Return To Mock Mode

Use mock mode to verify Influx and dashboards without node hardware:

```bash
MESSKLUPPE_INPUT_MODE=mock MESSKLUPPE_FAKE_MODE=1 ./scripts/build-local-messkluppe.sh
```

Then open:

- `/messkluppe/`
- `/graf/messkluppe`
- Grafana panel `Messkluppe Force`
