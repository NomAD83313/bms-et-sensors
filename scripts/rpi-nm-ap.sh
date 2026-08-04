#!/usr/bin/env bash
set -euo pipefail

# /gemini: Логика автоматического разделения встроенного Wi-Fi (AP), внешних USB-адаптеров и опционального Ethernet AP.
# Raspberry Pi only:
# - built-in Wi-Fi -> Access Point (NetworkManager) by default
# - additional Wi-Fi adapters -> normal client Wi-Fi
# - Ethernet can be used as a direct AP interface if AP_INTERFACE=eth0
# - other Ethernet interfaces stay as-is, autoconnect is enabled

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"

load_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

load_env

AP_NAME="${AP_NAME:-rpi-ap}"
AP_SECONDARY_NAME=""
AP_INTERFACE="${AP_INTERFACE:-}"
AP_SECONDARY_INTERFACE="${AP_SECONDARY_INTERFACE:-}"
AP_SSID="${AP_SSID:-BMSensors}"
AP_PASSWORD="${AP_PASSWORD:-ChangeMe12345}"
AP_BAND="${AP_BAND:-bg}"   # bg or a
AP_CHANNEL="${AP_CHANNEL:-6}"
AP_COUNTRY="${AP_COUNTRY:-DE}"
AP_WIFI_IPV4_CIDR="${AP_WIFI_IPV4_CIDR:-10.42.0.1/24}"
AP_ETH_IPV4_CIDR="${AP_ETH_IPV4_CIDR:-10.42.1.1/24}"
AP_LOCAL_DNS_ENABLE="${AP_LOCAL_DNS_ENABLE:-true}"
AP_LOCAL_DNS_NAME="${AP_LOCAL_DNS_NAME:-$(hostnamectl --static 2>/dev/null || hostname).internal}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

run_nmcli() {
  if [[ "${EUID}" -eq 0 ]]; then
    nmcli "$@"
  else
    sudo nmcli "$@"
  fi
}

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

is_usb_wifi() {
  local ifname="$1"
  local dev_path=""
  dev_path="$(readlink -f "/sys/class/net/${ifname}/device" 2>/dev/null || true)"
  [[ "${dev_path}" == *"/usb"* ]]
}

is_builtin_wifi() {
  local ifname="$1"
  # On Raspberry Pi, built-in radio is not on USB bus.
  ! is_usb_wifi "${ifname}"
}

pick_builtin_wifi() {
  local wifi_ifaces=("$@")
  local ifn=""
  for ifn in "${wifi_ifaces[@]}"; do
    if is_builtin_wifi "${ifn}"; then
      echo "${ifn}"
      return 0
    fi
  done
  return 1
}

get_device_type() {
  local ifname="$1"
  nmcli -t -f DEVICE,TYPE device status | awk -F: -v dev="${ifname}" '$1==dev {print $2; exit}' || true
}

device_exists() {
  local ifname="$1"
  nmcli -t -f DEVICE device status | awk -F: '{print $1}' | grep -Fxq "${ifname}"
}

list_wifi_ifaces() {
  nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1}'
}

list_eth_ifaces() {
  nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="ethernet"{print $1}' | while read -r ifn; do
    [[ -z "${ifn}" ]] && continue
    # Skip virtual/docker-style interfaces; keep only physical NICs.
    [[ "${ifn}" == veth* || "${ifn}" == docker* || "${ifn}" == br-* || "${ifn}" == virbr* ]] && continue
    [[ -e "/sys/class/net/${ifn}/device" ]] || continue
    [[ -n "${AP_INTERFACE}" && "${ifn}" == "${AP_INTERFACE}" ]] && continue
    echo "${ifn}"
  done
}

get_device_type() {
  local ifname="$1"
  nmcli -t -f DEVICE,TYPE device status | awk -F: -v dev="${ifname}" '$1==dev {print $2; exit}' || true
}

device_exists() {
  local ifname="$1"
  nmcli -t -f DEVICE device status | awk -F: '{print $1}' | grep -Fxq "${ifname}"
}

configure_ap_on_ethernet() {
  local ap_if="$1"
  local ap_cidr="$2"

  echo ">>> Configure shared Ethernet access on interface: ${ap_if} (${ap_cidr})"

  local eth_profile="${AP_NAME}-eth"
  if nmcli -t -f NAME connection show | grep -Fxq "${eth_profile}"; then
    run_nmcli connection modify "${eth_profile}" \
      connection.interface-name "${ap_if}" \
      connection.autoconnect yes \
      ipv4.method shared \
      ipv4.addresses "${ap_cidr}" \
      ipv6.method link-local
  else
    run_nmcli connection add type ethernet ifname "${ap_if}" con-name "${eth_profile}" autoconnect yes ipv4.method shared ipv4.addresses "${ap_cidr}" ipv6.method link-local
  fi

  if ! run_nmcli connection up "${eth_profile}" ifname "${ap_if}"; then
    echo "WARNING: could not bring up secondary AP connection '${eth_profile}' on '${ap_if}'. Continuing with the primary AP configuration." >&2
  fi
}

ensure_nm_running() {
  local nm_state
  nm_state="$(nmcli -t -f RUNNING general status | head -n1 || true)"
  if [[ "${nm_state}" != "running" ]]; then
    echo "ERROR: NetworkManager is not running." >&2
    exit 1
  fi
}

validate_ap_password() {
  local len=${#AP_PASSWORD}
  if (( len < 8 || len > 63 )); then
    echo "ERROR: AP_PASSWORD length must be 8..63 for WPA-PSK." >&2
    exit 1
  fi
}

set_regulatory_domain() {
  # brcmfmac (BCM4345, RPi built-in) ignores iw reg set — its regulatory domain
  # is set via ccode= in the NVRAM file at:
  #   /lib/firmware/brcm/brcmfmac43455-sdio.raspberrypi,5-model-b.txt
  # iw reg set is still useful for other chipsets (e.g. USB adapters using rtl8xxxu).
  if command -v iw >/dev/null 2>&1; then
    echo ">>> Set regulatory domain: ${AP_COUNTRY}"
    run_root iw reg set "${AP_COUNTRY}" || true
  fi
}

configure_ap_on_builtin_wifi() {
  local ap_if="$1"
  local profile="${2:-${AP_NAME}}"
  local ap_cidr="$3"

  echo ">>> Configure AP '${profile}' on built-in Wi-Fi: ${ap_if} (${ap_cidr})"

  # Reuse existing AP profile if present, otherwise create.
  if nmcli -t -f NAME connection show | grep -Fxq "${profile}"; then
    run_nmcli connection modify "${profile}" \
      connection.interface-name "${ap_if}" \
      connection.autoconnect yes \
      802-11-wireless.ssid "${AP_SSID}" \
      802-11-wireless.mode ap \
      802-11-wireless.band "${AP_BAND}" \
      802-11-wireless.channel "${AP_CHANNEL}" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.proto rsn \
      wifi-sec.pairwise ccmp \
      wifi-sec.group ccmp \
      wifi-sec.pmf 2 \
      wifi-sec.psk "${AP_PASSWORD}" \
      ipv4.method shared \
      ipv4.addresses "${ap_cidr}" \
      ipv6.method link-local
  else
    run_nmcli connection add type wifi ifname "${ap_if}" con-name "${profile}" ssid "${AP_SSID}"
    run_nmcli connection modify "${profile}" \
      connection.interface-name "${ap_if}" \
      connection.autoconnect yes \
      802-11-wireless.mode ap \
      802-11-wireless.band "${AP_BAND}" \
      802-11-wireless.channel "${AP_CHANNEL}" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.proto rsn \
      wifi-sec.pairwise ccmp \
      wifi-sec.group ccmp \
      wifi-sec.pmf 2 \
      wifi-sec.psk "${AP_PASSWORD}" \
      ipv4.method shared \
        ipv4.addresses "${ap_cidr}" \
      ipv6.method link-local
  fi

  run_nmcli connection up "${profile}" ifname "${ap_if}" || {
    echo "ERROR: failed to bring up AP connection '${profile}'." >&2
    exit 1
  }
}

get_iface_ipv4() {
  local ifname="$1"
  nmcli -g IP4.ADDRESS device show "${ifname}" 2>/dev/null | head -n1 | cut -d/ -f1
}

configure_local_dns_alias() {
  local ap_if="$1"
  local ap_ipv4=""
  local ap_domain=""
  local wildcard_line=""
  local dhcp_dns_line=""

  ap_ipv4="$(get_iface_ipv4 "${ap_if}")"
  if [[ -z "${ap_ipv4}" ]]; then
    # Fallback for NetworkManager shared default.
    ap_ipv4="10.42.0.1"
  fi

  ap_domain="${AP_LOCAL_DNS_NAME#*.}"
  if [[ -n "${ap_domain}" && "${ap_domain}" != "${AP_LOCAL_DNS_NAME}" ]]; then
    wildcard_line="address=/.${ap_domain}/${ap_ipv4}"
  fi
  # Force AP clients to use Raspberry local DNS resolver.
  dhcp_dns_line="dhcp-option=6,${ap_ipv4}"

  if [[ "${AP_LOCAL_DNS_ENABLE,,}" != "true" ]]; then
    echo ">>> Local DNS alias disabled (AP_LOCAL_DNS_ENABLE=false)"
    return 0
  fi

  echo ">>> Configure local DNS alias '${AP_LOCAL_DNS_NAME}' -> ${ap_ipv4}"

  local nm_conf="/etc/NetworkManager/conf.d/99-bms-dns.conf"
  local dnsmasq_conf="/etc/NetworkManager/dnsmasq.d/99-bms-ap-local.conf"
  local tmp_nm="/tmp/99-bms-dns.conf.$$"
  local tmp_dnsmasq="/tmp/99-bms-ap-local.conf.$$"

  cat > "${tmp_nm}" <<'EOF'
[main]
dns=dnsmasq
EOF

  cat > "${tmp_dnsmasq}" <<EOF
# Managed by rpi-nm-ap.sh
address=/${AP_LOCAL_DNS_NAME}/${ap_ipv4}
${wildcard_line}
${dhcp_dns_line}
EOF

  run_root mkdir -p /etc/NetworkManager/conf.d /etc/NetworkManager/dnsmasq.d
  run_root cp "${tmp_nm}" "${nm_conf}"
  run_root cp "${tmp_dnsmasq}" "${dnsmasq_conf}"
  rm -f "${tmp_nm}" "${tmp_dnsmasq}"

  # Restart NetworkManager to ensure dnsmasq plugin and alias config are active.
  run_root systemctl restart NetworkManager
  run_nmcli connection up "${AP_NAME}" || true
}

configure_extra_wifi_as_clients() {
  local ap_ifaces=("$@")
  local ifn=""

  while IFS= read -r ifn; do
    [[ -z "${ifn}" ]] && continue

    local skip=false
    for ap_if in "${ap_ifaces[@]}"; do
      [[ "${ifn}" == "${ap_if}" ]] && skip=true
    done
    [[ "${skip}" == true ]] && continue

    echo ">>> Keep extra Wi-Fi '${ifn}' in client mode"
    run_nmcli device set "${ifn}" managed yes || true

    # If this iface is currently on an AP-mode connection, disconnect it.
    local active_conn mode
    active_conn="$(nmcli -t -f GENERAL.CONNECTION device show "${ifn}" 2>/dev/null | head -n1 | cut -d: -f2- || true)"
    if [[ -n "${active_conn}" && "${active_conn}" != "--" ]]; then
      mode="$(nmcli -g 802-11-wireless.mode connection show "${active_conn}" 2>/dev/null || true)"
      if [[ "${mode}" == "ap" ]]; then
        run_nmcli connection down "${active_conn}" || true
      fi
    fi
  done < <(list_wifi_ifaces)
}

ensure_ethernet_autoconnect() {
  local ifn=""
  while IFS= read -r ifn; do
    [[ -z "${ifn}" ]] && continue
    echo ">>> Ensure Ethernet autoconnect on '${ifn}' (no IP mode rewrite)"

    local existing=""
    local cname="wired-auto-${ifn}"
    existing="$(nmcli -t -f NAME,TYPE,DEVICE connection show | awk -F: -v dev="${ifn}" '$2=="802-3-ethernet" && $3==dev {print $1; exit}')"
    if [[ -z "${existing}" ]] && nmcli -t -f NAME connection show | grep -Fxq "${cname}"; then
      existing="${cname}"
    fi

    if [[ "${ifn}" == "${AP_INTERFACE}" ]]; then
      continue
    fi

    if [[ -n "${existing}" ]]; then
      run_nmcli connection modify "${existing}" connection.autoconnect yes || true
      run_nmcli connection up "${existing}" ifname "${ifn}" >/dev/null 2>&1 || true
    else
      run_nmcli connection add type ethernet ifname "${ifn}" con-name "${cname}" autoconnect yes ipv4.method auto ipv6.method auto
      run_nmcli connection up "${cname}" ifname "${ifn}" >/dev/null 2>&1 || true
    fi
  done < <(list_eth_ifaces)
}

print_summary() {
  echo
  echo "=== NetworkManager summary ==="
  nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
  echo
  echo "AP profile(s):"
  nmcli -f connection.id,connection.interface-name,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode,ipv4.method connection show "${AP_NAME}" 2>/dev/null || true
  if [[ -n "${AP_SECONDARY_NAME}" ]]; then
    nmcli -f connection.id,connection.interface-name,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode,ipv4.method connection show "${AP_SECONDARY_NAME}" 2>/dev/null || true
  fi
  if [[ "${AP_LOCAL_DNS_ENABLE,,}" == "true" ]]; then
    echo
    echo "Local DNS alias for AP clients: http://${AP_LOCAL_DNS_NAME}"
  fi
}

main() {
  need_cmd nmcli
  ensure_nm_running
  validate_ap_password

  local ap_if ap_type secondary_if secondary_type
  local primary_profile="${AP_NAME}"
  local secondary_profile=""
  local primary_cidr=""
  local secondary_cidr=""

  # Collect available Wi‑Fi interfaces early for potential secondary AP.
  mapfile -t wifi_ifaces < <(list_wifi_ifaces) || true

  if [[ -n "${AP_INTERFACE}" ]]; then
    ap_if="${AP_INTERFACE}"
    if ! device_exists "${ap_if}"; then
      echo "ERROR: configured AP_INTERFACE '${ap_if}' not found." >&2
      exit 1
    fi
  else
    if [[ "${#wifi_ifaces[@]}" -eq 0 ]]; then
      echo "ERROR: no Wi-Fi interfaces found." >&2
      exit 1
    fi

    ap_if="$(pick_builtin_wifi "${wifi_ifaces[@]}")" || {
      echo "ERROR: could not detect built-in Wi-Fi (non-USB) interface." >&2
      exit 1
    }
  fi

  ap_type="$(get_device_type "${ap_if}")"
  case "${ap_type}" in
    wifi)
      primary_cidr="${AP_WIFI_IPV4_CIDR}"
      ;;
    ethernet)
      primary_cidr="${AP_ETH_IPV4_CIDR}"
      ;;
    *)
      primary_cidr=""
      ;;
  esac

  case "${ap_type}" in
    wifi)
      set_regulatory_domain
      configure_ap_on_builtin_wifi "${ap_if}" "${primary_profile}" "${primary_cidr}"
      ;;
    ethernet)
      # Primary: configure Ethernet shared access
      configure_ap_on_ethernet "${ap_if}" "${primary_cidr}"
      ;;
    *)
      echo "ERROR: configured AP_INTERFACE '${ap_if}' is not a supported interface type: ${ap_type}" >&2
      exit 1
      ;;
  esac

  if [[ -n "${AP_SECONDARY_INTERFACE}" ]]; then
    secondary_if="${AP_SECONDARY_INTERFACE}"
    if [[ "${secondary_if}" == "${ap_if}" ]]; then
      echo "INFO: AP_SECONDARY_INTERFACE matches the primary interface; skipping secondary AP configuration."
    elif ! device_exists "${secondary_if}"; then
      echo "ERROR: configured AP_SECONDARY_INTERFACE '${secondary_if}' not found." >&2
      exit 1
    else
      secondary_type="$(get_device_type "${secondary_if}")"
      case "${secondary_type}" in
        wifi)
          secondary_cidr="${AP_WIFI_IPV4_CIDR}"
          set_regulatory_domain
          secondary_profile="${AP_NAME}-wifi"
          AP_SECONDARY_NAME="${secondary_profile}"
          configure_ap_on_builtin_wifi "${secondary_if}" "${secondary_profile}" "${secondary_cidr}"
          ;;
        ethernet)
          secondary_cidr="${AP_ETH_IPV4_CIDR}"
          secondary_profile="${AP_NAME}-eth"
          AP_SECONDARY_NAME="${secondary_profile}"
          configure_ap_on_ethernet "${secondary_if}" "${secondary_cidr}"
          ;;
        *)
          echo "ERROR: configured AP_SECONDARY_INTERFACE '${secondary_if}' is not a supported interface type: ${secondary_type}" >&2
          exit 1
          ;;
      esac
    fi
  elif [[ "${ap_type}" == "ethernet" ]]; then
    # Preserve legacy behavior: if Ethernet is primary, add a built-in Wi‑Fi AP as secondary when available.
    if [[ "${#wifi_ifaces[@]}" -gt 0 ]]; then
      secondary_if="$(pick_builtin_wifi "${wifi_ifaces[@]}")" || true
    fi
    if [[ -n "${secondary_if}" ]]; then
      set_regulatory_domain
      AP_SECONDARY_NAME="${AP_NAME}-wifi"
      configure_ap_on_builtin_wifi "${secondary_if}" "${AP_SECONDARY_NAME}" "${AP_WIFI_IPV4_CIDR}"
    fi
  fi

  # Prepare list of AP interfaces for auxiliary steps
  ap_ifaces=("${ap_if}")
  [[ -n "${secondary_if}" ]] && ap_ifaces+=("${secondary_if}")

  # Configure local DNS alias for the primary AP and adjust other Wi‑Fi devices
  configure_local_dns_alias "${ap_if}"
  configure_extra_wifi_as_clients "${ap_ifaces[@]}"
  ensure_ethernet_autoconnect
  print_summary
}

main "$@"
