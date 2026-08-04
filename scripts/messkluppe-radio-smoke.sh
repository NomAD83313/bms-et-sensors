#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${MESSKLUPPE_BASE_URL:-http://127.0.0.1/messkluppe}"
TIMEOUT_SEC="${MESSKLUPPE_SMOKE_TIMEOUT_SEC:-5}"

fetch_json() {
    local method="$1"
    local path="$2"
    curl -fsS --max-time "${TIMEOUT_SEC}" -X "${method}" "${BASE_URL}${path}"
}

print_json_summary() {
    local mode="$1"
    local json_data
    json_data="$(cat)"
    JSON_DATA="${json_data}" python - "${mode}" <<'PY'
import json
import os
import sys

mode = sys.argv[1]
data = json.loads(os.environ["JSON_DATA"])

if mode == "health":
    print(f"health.ok={data.get('ok')}")
    print(f"input_mode={data.get('input_mode')}")
    print(f"collector_running={data.get('collector_running')}")
    print(f"influx_configured={data.get('influx_configured')}")
    print(f"last_error={data.get('last_error') or ''}")
elif mode == "status":
    print(f"radio_listening={data.get('radio_listening')}")
    print(f"radio_rx_ready={data.get('radio_rx_ready')}")
    print(f"radio_rx_packets={data.get('radio_rx_packets')}")
    print(f"radio_rx_empty_reads={data.get('radio_rx_empty_reads')}")
    print(f"radio_rx_errors={data.get('radio_rx_errors')}")
    print(f"radio_rx_parse_errors={data.get('radio_rx_parse_errors')}")
    print(f"radio_rx_last_payload_hex={data.get('radio_rx_last_payload_hex') or ''}")
    print(f"radio_rx_last_error={data.get('radio_rx_last_error') or ''}")
elif mode == "diagnose":
    radio = data.get("radio", {})
    checks = radio.get("checks", {})
    details = radio.get("details", {})
    print(f"diagnose.ok={data.get('ok')}")
    print(f"configured={radio.get('configured')}")
    print(f"checks={checks}")
    print(f"details={details}")
    print(f"error={radio.get('error') or ''}")
elif mode == "payloads":
    payloads = data.get("payloads", [])
    print(f"recent_payload_count={data.get('count', len(payloads))}")
    print(f"last_payload_hex={data.get('last_payload_hex') or ''}")
    for idx, item in enumerate(payloads[-3:], start=max(0, len(payloads) - 3)):
        print(f"payload[{idx}] ts={item.get('ts')} ok={item.get('ok')} size={item.get('size')} hex={item.get('payload_hex')} error={item.get('error') or ''}")
else:
    print(json.dumps(data, indent=2, sort_keys=True))
PY
}

section() {
    printf '\n>>> %s\n' "$1"
}

echo "Messkluppe radio smoke"
echo "base_url=${BASE_URL}"

section "Health"
health_json="$(fetch_json GET /health)"
printf '%s' "${health_json}" | print_json_summary health

section "Status"
status_json="$(fetch_json GET /api/status)"
printf '%s' "${status_json}" | print_json_summary status

section "Radio diagnose"
if diagnose_json="$(fetch_json POST /api/radio/diagnose)"; then
    printf '%s' "${diagnose_json}" | print_json_summary diagnose
else
    echo "diagnose.ok=false"
fi

section "Recent payloads"
payloads_json="$(fetch_json GET /api/radio/recent-payloads)"
printf '%s' "${payloads_json}" | print_json_summary payloads

section "Result"
status_for_result="$(cat <<< "${status_json}")"
JSON_DATA="${status_for_result}" python - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["JSON_DATA"])
if data.get("input_mode") != "radio":
    print("WARN: MESSKLUPPE_INPUT_MODE is not radio")
elif not data.get("radio_listening") or not data.get("radio_rx_ready"):
    print("FAIL: radio loop is not ready")
    sys.exit(1)
elif data.get("radio_rx_errors"):
    print("FAIL: radio RX errors are present")
    sys.exit(1)
elif not data.get("radio_rx_packets"):
    print("PASS: radio is listening; no node packets received yet")
else:
    print("PASS: radio is listening and packets were received")
PY
