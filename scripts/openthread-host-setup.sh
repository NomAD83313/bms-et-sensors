#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SYSCTL_CONF="${BMS_OTBR_SYSCTL_CONF:-/etc/sysctl.d/99-bms-openthread.conf}"
RCP_BRIDGE_SERVICE="${BMS_OTBR_RCP_BRIDGE_SERVICE:-/etc/systemd/system/bms-otbr-rcp-bridge.service}"
PROC_ROOT="${BMS_OTBR_PROC_ROOT:-/proc}"
SYS_CLASS_NET_ROOT="${BMS_OTBR_SYS_CLASS_NET_ROOT:-/sys/class/net}"
DEV_ROOT="${BMS_OTBR_DEV_ROOT:-/dev}"
ENV_FILE="${ENV_FILE:-.env}"

MODE="check"
if [[ "${1:-}" == "--apply" ]]; then
  MODE="apply"
elif [[ "${1:-}" != "" && "${1:-}" != "--check" ]]; then
  echo "Usage: $0 [--check|--apply]" >&2
  exit 2
fi

cd "${PROJECT_ROOT}"

read_env_var() {
  local key="$1"
  local fallback="$2"
  if [[ ! -f "${ENV_FILE}" ]]; then
    printf '%s\n' "$fallback"
    return
  fi
  local value
  value="$(awk -F= -v k="$key" '$1==k{val=substr($0, index($0,"=")+1); gsub(/^[[:space:]]+|[[:space:]]+$/, "", val); gsub(/^"|"$/, "", val); gsub(/^'\''|'\''$/, "", val); print val; exit}' "${ENV_FILE}")"
  printf '%s\n' "${value:-$fallback}"
}

log_info() {
  echo ">>> $*"
}

log_ok() {
  echo "OK: $*"
}

log_warn() {
  echo "WARN: $*" >&2
}

log_error() {
  echo "ERROR: $*" >&2
}

run_sudo() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

failures=0

check_command() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    log_ok "${name} is available."
    return
  fi
  log_error "${name} is not available."
  failures=$((failures + 1))
}

check_path() {
  local label="$1"
  local path="$2"
  local check_path="$path"
  if [[ "$path" == /dev/* && "${DEV_ROOT}" != "/dev" ]]; then
    check_path="${DEV_ROOT}${path#/dev}"
  fi
  if [[ -e "$check_path" ]]; then
    log_ok "${label} exists: ${path}"
    return
  fi
  log_error "${label} is missing: ${path}"
  failures=$((failures + 1))
}

is_network_rcp_endpoint() {
  local value="$1"
  [[ "$value" == *"://"* ]] && return 0
  [[ "$value" =~ ^[^/[:space:]]+:[0-9]+$ ]]
}

parse_network_rcp_endpoint() {
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

check_network_rcp_endpoint() {
  local endpoint="$1"
  local host=""
  local port=""
  if ! read -r host port < <(parse_network_rcp_endpoint "$endpoint"); then
    log_error "OpenThread RCP network endpoint is invalid: ${endpoint}"
    failures=$((failures + 1))
    return
  fi

  if command -v nc >/dev/null 2>&1; then
    if nc -z -w 3 "$host" "$port" >/dev/null 2>&1; then
      log_ok "OpenThread RCP network endpoint is reachable: ${host}:${port}"
      return
    fi
    log_error "OpenThread RCP network endpoint is not reachable: ${host}:${port}"
    failures=$((failures + 1))
    return
  fi

  log_warn "nc is not available; skipping live RCP socket check for ${host}:${port}."
}

install_network_rcp_bridge_service() {
  local endpoint="$1"
  local rcp_device="$2"
  local host=""
  local port=""
  if ! read -r host port < <(parse_network_rcp_endpoint "$endpoint"); then
    return
  fi

  if [[ "${MODE}" != "apply" ]]; then
    if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled bms-otbr-rcp-bridge.service >/dev/null 2>&1; then
      log_ok "Persistent RCP TCP bridge service is enabled: bms-otbr-rcp-bridge.service"
    else
      log_warn "Persistent RCP TCP bridge service is not installed/enabled."
      log_warn "Run $0 --apply to keep /dev/ttyOTBR available after reboot."
    fi
    return
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    log_warn "systemctl is not available; cannot install persistent RCP TCP bridge service."
    return
  fi

  log_info "Installing persistent RCP TCP bridge service: ${RCP_BRIDGE_SERVICE}"
  cat <<EOF | run_sudo tee "${RCP_BRIDGE_SERVICE}" >/dev/null
[Unit]
Description=BMS OpenThread RCP TCP bridge
Documentation=file://${PROJECT_ROOT}/docs/openthread.md
Wants=network-online.target
After=network-online.target
Before=docker.service

[Service]
Type=simple
ExecStartPre=/bin/rm -f ${rcp_device}
ExecStart=/usr/bin/socat -d -d TCP:${host}:${port},forever,interval=5 PTY,raw,echo=0,link=${rcp_device},ignoreeof
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
  run_sudo systemctl daemon-reload
  run_sudo systemctl enable --now bms-otbr-rcp-bridge.service >/dev/null
  log_ok "Persistent RCP TCP bridge service is enabled: bms-otbr-rcp-bridge.service"
}

ensure_tun() {
  local tun_path="${DEV_ROOT}/net/tun"
  if [[ -e "$tun_path" ]]; then
    log_ok "TUN device exists: /dev/net/tun"
    return
  fi

  if [[ "${MODE}" == "apply" ]]; then
    log_info "Loading tun kernel module..."
    run_sudo modprobe tun || true
  fi

  if [[ -e "$tun_path" ]]; then
    log_ok "TUN device exists: /dev/net/tun"
  else
    log_error "TUN device is missing: /dev/net/tun"
    failures=$((failures + 1))
  fi
}

warn_if_not_host_namespace() {
  local init_mnt=""
  local self_mnt=""
  local init_comm=""
  local warn=0

  if [[ -L "${PROC_ROOT}/1/ns/mnt" && -L "${PROC_ROOT}/self/ns/mnt" ]]; then
    init_mnt="$(readlink "${PROC_ROOT}/1/ns/mnt" 2>/dev/null || true)"
    self_mnt="$(readlink "${PROC_ROOT}/self/ns/mnt" 2>/dev/null || true)"
  fi

  if [[ -n "$init_mnt" && -n "$self_mnt" && "$init_mnt" != "$self_mnt" ]]; then
    warn=1
  fi

  if [[ -r "${PROC_ROOT}/1/comm" ]]; then
    init_comm="$(tr -d '\n' < "${PROC_ROOT}/1/comm" 2>/dev/null || true)"
  fi

  case "$init_comm" in
    bwrap|bubblewrap|docker-init|tini)
      warn=1
      ;;
  esac

  if [[ "$warn" == "1" ]]; then
    log_warn "This check appears to be running from an agent/container namespace."
    log_warn "Device and sysctl checks can be false negatives in agent/container shells."
    log_warn "For a live OTBR, verify with: docker exec openthread-border-router ot-ctl state"
  fi
}

ensure_runtime_dir() {
  if [[ "${MODE}" == "apply" ]]; then
    mkdir -p runtime/openthread
  fi

  if [[ -d runtime/openthread ]]; then
    log_ok "Runtime directory exists: runtime/openthread"
  else
    log_warn "Runtime directory is missing: runtime/openthread"
    log_warn "Run $0 --apply to create it."
  fi
}

ensure_ipv6_enabled() {
  local disabled
  disabled="$(sysctl -n net.ipv6.conf.all.disable_ipv6 2>/dev/null || echo 1)"
  if [[ "$disabled" == "0" ]]; then
    log_ok "IPv6 is enabled."
    return
  fi
  log_error "IPv6 is disabled. OpenThread Border Router requires IPv6."
  failures=$((failures + 1))
}

ensure_ipv6_forwarding() {
  local forwarding
  forwarding="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo 0)"
  if [[ "$forwarding" == "1" ]]; then
    log_ok "IPv6 forwarding is enabled."
  elif [[ "${MODE}" == "apply" ]]; then
    log_info "Enabling IPv6 forwarding for the current boot..."
    run_sudo sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null
    forwarding="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo 0)"
    if [[ "$forwarding" == "1" ]]; then
      log_ok "IPv6 forwarding is enabled."
    else
      log_error "IPv6 forwarding is disabled."
      log_error "Run $0 --apply or set net.ipv6.conf.all.forwarding=1 on the Raspberry Pi host."
      failures=$((failures + 1))
    fi
  else
    log_error "IPv6 forwarding is disabled."
    log_error "Run $0 --apply or set net.ipv6.conf.all.forwarding=1 on the Raspberry Pi host."
    failures=$((failures + 1))
  fi

  if [[ "${MODE}" == "apply" ]]; then
    log_info "Writing persistent IPv6 forwarding sysctl: ${SYSCTL_CONF}"
    printf '%s\n' \
      "# Managed by bms-et-sensors OpenThread setup." \
      "net.ipv6.conf.all.forwarding=1" \
      | run_sudo tee "${SYSCTL_CONF}" >/dev/null
  elif [[ -f "${SYSCTL_CONF}" ]] && grep -Eq '^[[:space:]]*net\.ipv6\.conf\.all\.forwarding[[:space:]]*=[[:space:]]*1[[:space:]]*$' "${SYSCTL_CONF}"; then
    log_ok "Persistent IPv6 forwarding sysctl exists: ${SYSCTL_CONF}"
  else
    log_warn "Persistent IPv6 forwarding sysctl is not installed: ${SYSCTL_CONF}"
    log_warn "Run $0 --apply to keep OTBR working after reboot."
  fi
}

RCP_DEVICE="$(read_env_var OTBR_RCP_DEVICE /dev/ttyACM0)"
RCP_TCP_ENDPOINT="$(read_env_var OTBR_RCP_TCP_ENDPOINT "")"
INFRA_IF="$(read_env_var OTBR_INFRA_IF wlan0)"

log_info "OpenThread host check mode: ${MODE}"
log_info "RCP device: ${RCP_DEVICE}"
if [[ -n "${RCP_TCP_ENDPOINT}" ]]; then
  log_info "RCP TCP endpoint: ${RCP_TCP_ENDPOINT}"
fi
log_info "Infrastructure interface: ${INFRA_IF}"

warn_if_not_host_namespace
check_command docker
check_command ip
check_command sysctl
if [[ -n "${RCP_TCP_ENDPOINT}" ]]; then
  check_command socat
  check_network_rcp_endpoint "${RCP_TCP_ENDPOINT}"
  install_network_rcp_bridge_service "${RCP_TCP_ENDPOINT}" "${RCP_DEVICE}"
elif is_network_rcp_endpoint "${RCP_DEVICE}"; then
  log_warn "Direct TCP RCP in OTBR_RCP_DEVICE is not supported by the stock OTBR image."
  log_warn "Set OTBR_RCP_TCP_ENDPOINT=${RCP_DEVICE} and OTBR_RCP_DEVICE=/dev/ttyOTBR instead."
  failures=$((failures + 1))
else
  check_path "OpenThread RCP device" "${RCP_DEVICE}"
fi
check_path "Infrastructure network interface" "${SYS_CLASS_NET_ROOT}/${INFRA_IF}"
ensure_tun
ensure_runtime_dir
ensure_ipv6_enabled
ensure_ipv6_forwarding

if [[ "${failures}" -gt 0 ]]; then
  log_error "OpenThread host checks failed: ${failures}"
  exit 1
fi

log_ok "OpenThread host checks passed."
