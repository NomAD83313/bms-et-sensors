#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

parse_network_endpoint() {
  local value="$1"
  value="${value#*://}"
  value="${value%%\?*}"
  value="${value%/}"
  if [[ "$value" =~ ^\[([^]]+)\]:([0-9]+)$ ]]; then
    printf '%s %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  if [[ "$value" =~ ^([^:/[:space:]]+):([0-9]+)$ ]]; then
    printf '%s %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return 0
  fi
  return 1
}

start_rcp_tcp_bridge() {
  local endpoint
  endpoint="$(read_env_var OTBR_RCP_TCP_ENDPOINT || true)"
  if [[ -z "${endpoint}" ]]; then
    return
  fi

  local rcp_device
  rcp_device="$(read_env_var OTBR_RCP_DEVICE || true)"
  rcp_device="${rcp_device:-/dev/ttyOTBR}"

  local host=""
  local port=""
  if ! read -r host port < <(parse_network_endpoint "${endpoint}"); then
    echo "ERROR: invalid OTBR_RCP_TCP_ENDPOINT: ${endpoint}" >&2
    exit 1
  fi

  if ! command -v socat >/dev/null 2>&1; then
    echo "ERROR: socat is required for OTBR_RCP_TCP_ENDPOINT=${endpoint}" >&2
    echo "       Install it with: sudo apt-get install -y socat" >&2
    exit 1
  fi

  wait_for_rcp_tcp_bridge() {
    local wait_host="$1"
    local wait_port="$2"
    local wait_device="$3"
    for attempt in {1..60}; do
      if [[ -e "${wait_device}" ]]; then
        if ! command -v ss >/dev/null 2>&1 || ss -tn state established 2>/dev/null | grep -Fq "${wait_host}:${wait_port}"; then
          echo ">>> RCP TCP bridge is ready: ${wait_device}"
          return 0
        fi
      fi
      sleep 0.5
    done
    return 1
  }

  if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled bms-otbr-rcp-bridge.service >/dev/null 2>&1; then
    echo ">>> Starting persistent RCP TCP bridge service: ${endpoint} -> ${rcp_device}"
    sudo systemctl restart bms-otbr-rcp-bridge.service
    if wait_for_rcp_tcp_bridge "${host}" "${port}" "${rcp_device}"; then
      return
    fi
    echo "ERROR: RCP TCP bridge service did not connect and create ${rcp_device}" >&2
    sudo systemctl status bms-otbr-rcp-bridge.service --no-pager -l >&2 || true
    exit 1
  fi

  local pid_file="/tmp/bms-otbr-rcp-socat.pid"
  local log_file="/tmp/bms-otbr-rcp-socat.log"
  if [[ -s "${pid_file}" ]]; then
    local old_pid
    old_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" >/dev/null 2>&1; then
      echo ">>> RCP TCP bridge already running: ${endpoint} -> ${rcp_device} (pid ${old_pid})"
      return
    fi
  fi

  echo ">>> Starting RCP TCP bridge: ${endpoint} -> ${rcp_device}"
  if [[ "${rcp_device}" == /dev/* ]]; then
    sudo rm -f "${rcp_device}"
  else
    rm -f "${rcp_device}"
  fi
  : >"${log_file}"
  sudo socat -d -d \
    "TCP:${host}:${port},forever,interval=5" \
    "PTY,raw,echo=0,link=${rcp_device},ignoreeof" \
    >>"${log_file}" 2>&1 &
  local bridge_pid=$!
  echo "${bridge_pid}" >"${pid_file}"

  if wait_for_rcp_tcp_bridge "${host}" "${port}" "${rcp_device}"; then
    return
  fi

  echo "ERROR: RCP TCP bridge did not connect and create ${rcp_device}" >&2
  tail -40 "${log_file}" >&2 || true
  exit 1
}

echo ">>> Checking OpenThread host prerequisites..."
"${SCRIPT_DIR}/openthread-host-setup.sh" --check

start_rcp_tcp_bridge

echo ">>> Starting OpenThread Border Router service..."
"${COMPOSE[@]}" --profile thread up -d openthread-border-router
bms_verify_services_created openthread-border-router

otbr_ready() {
  timeout 6 docker exec openthread-border-router ot-ctl state >/dev/null 2>&1
}

restart_otbr_agent() {
  timeout 10 docker exec openthread-border-router service otbr-agent restart >/dev/null 2>&1 || true
}

echo ">>> Verifying otbr-agent readiness..."
for attempt in {1..8}; do
  if otbr_ready; then
    echo ">>> OTBR agent is ready."
    break
  fi
  if [[ "${attempt}" -eq 3 ]]; then
    echo ">>> otbr-agent did not respond yet; restarting agent inside container..."
    restart_otbr_agent
  fi
  sleep 2
  if [[ "${attempt}" -eq 8 ]]; then
    echo ">>> otbr-agent is still not responding; recreating OTBR container once..."
    "${COMPOSE[@]}" --profile thread up -d --force-recreate openthread-border-router
    bms_verify_services_created openthread-border-router
    for recreate_attempt in {1..8}; do
      if otbr_ready; then
        echo ">>> OTBR agent is ready after container recreate."
        break 2
      fi
      if [[ "${recreate_attempt}" -eq 3 ]]; then
        echo ">>> otbr-agent is still settling after recreate; restarting agent inside container..."
        restart_otbr_agent
      fi
      sleep 2
      if [[ "${recreate_attempt}" -eq 8 ]]; then
        echo ">>> WARNING: otbr-agent is still not responding after recreate; inspect with:"
        echo "    docker exec openthread-border-router service otbr-agent status"
        echo "    docker exec openthread-border-router ot-ctl state"
      fi
    done
  fi
done

echo ">>> Done (openthread-border-router)."
