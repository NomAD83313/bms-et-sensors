#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"

bms_cd_project

ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"
load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

configure_matter_server_backend() {
  local backend="${MATTER_SERVER_BACKEND:-matterjs}"

  case "${backend}" in
    matterjs)
      export MATTER_SERVER_BACKEND="matterjs"
      export MATTER_SERVER_IMAGE="${MATTER_SERVER_IMAGE:-ghcr.io/matter-js/matterjs-server:stable}"
      export MATTER_SERVER_DATA_DIR="${MATTER_SERVER_DATA_DIR:-./runtime/matterjs-server}"
      export MATTER_SERVER_USER="${MATTER_SERVER_USER:-0:0}"
      export MATTER_SERVER_PRODUCTION_MODE="${MATTER_SERVER_PRODUCTION_MODE:-true}"
      ;;
    *)
      echo "ERROR: Unsupported MATTER_SERVER_BACKEND=${backend}. This lab branch supports matterjs only." >&2
      exit 1
      ;;
  esac
}

hci0_is_soft_blocked() {
  rfkill list bluetooth 2>/dev/null | awk '
    /^[0-9]+: hci0: Bluetooth/ { in_hci0=1; next }
    /^[0-9]+:/ { in_hci0=0 }
    in_hci0 && /Soft blocked:/ { print $3; exit }
  '
}

prepare_internal_ble_adapter() {
  local prepare="${MATTER_INTERNAL_BLE_PREPARE:-1}"
  if [[ "${prepare}" != "1" ]]; then
    return 0
  fi

  echo ">>> Preparing internal BLE adapter hci0 without touching Wi-Fi interfaces..."
  bluetoothctl scan off >/dev/null 2>&1 || true
  bluetoothctl power on >/dev/null 2>&1 || true

  if sudo -n true >/dev/null 2>&1; then
    sudo -n rfkill unblock bluetooth
    sudo -n hciconfig hci0 up
    return 0
  fi

  rfkill unblock bluetooth >/dev/null 2>&1 || true
  hciconfig hci0 up >/dev/null 2>&1 || true
}

load_env
configure_matter_server_backend

MATTER_BLE_MODE="${MATTER_BLE_MODE:-internal}"

case "${MATTER_BLE_MODE}" in
  internal)
    if [[ ! -d /sys/class/bluetooth/hci0 ]]; then
      echo "ERROR: Internal Bluetooth adapter hci0 was not found." >&2
      exit 1
    fi

    prepare_internal_ble_adapter

    if [[ "$(hci0_is_soft_blocked)" == "yes" ]]; then
      echo "ERROR: hci0 is soft-blocked. Run this once from a local shell:" >&2
      echo "ERROR:   sudo rfkill unblock bluetooth && sudo hciconfig hci0 up" >&2
      echo "ERROR: This does not disconnect wlan0 or wlan1." >&2
      exit 1
    fi

    if ! hciconfig hci0 2>/dev/null | grep -q "UP RUNNING"; then
      echo "ERROR: hci0 is not UP RUNNING. Run this once from a local shell:" >&2
      echo "ERROR:   sudo hciconfig hci0 up" >&2
      echo "ERROR: This does not disconnect wlan0 or wlan1." >&2
      exit 1
    fi

    export MATTER_BLUETOOTH_ADAPTER=0
    echo ">>> Using internal BLE adapter hci0 for Matter commissioning."
    ;;
  disabled)
    export MATTER_BLUETOOTH_ADAPTER=999
    echo ">>> Starting Matter Server with BLE disabled/unavailable."
    echo ">>> Use this mode only for network_only commissioning of already reachable devices."
    ;;
  *)
    echo "ERROR: Unsupported MATTER_BLE_MODE=${MATTER_BLE_MODE}. Use internal or disabled." >&2
    exit 1
    ;;
esac

export MATTER_PRIMARY_IF="${MATTER_PRIMARY_IF:-wlan0}"

echo ">>> Wi-Fi interfaces are left untouched. PRIMARY_INTERFACE=${MATTER_PRIMARY_IF}"
echo ">>> Starting Matter profile services with ${MATTER_SERVER_BACKEND} backend..."
"${COMPOSE[@]}" --profile matter up -d --force-recreate matter-server matter-collector
bms_verify_services_created matter-server matter-collector

echo ">>> Done (matter-server + matter-collector, BLE mode: ${MATTER_BLE_MODE})."
