#!/usr/bin/env bash
# Install or update the host-side Raspberry Pi camera source (systemd bms-pi-camera).
# The source runs on the host so libcamera always matches the Pi kernel; camera-media
# reads it from http://127.0.0.1:8084. Safe to rerun: the service is restarted only when
# its files or unit changed, or when it is not running.
# Usage: scripts/pi-camera-host-setup.sh [--check]   (--check: exit 1 if the host has no Pi camera tools)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_DIR=/usr/local/lib/bms-cameras
UNIT=/etc/systemd/system/bms-pi-camera.service

missing=()
command -v rpicam-vid >/dev/null || missing+=("rpicam-apps-core and rpicam-apps-encoder")
command -v ffmpeg >/dev/null || missing+=("ffmpeg (full-view capture padding)")
[[ -d /run/systemd/system ]] || missing+=("systemd")
if [[ ${#missing[@]} -gt 0 ]]; then
  [[ "${1:-}" == "--check" ]] && exit 1
  printf 'Pi camera source needs: %s\n' "${missing[@]}" >&2
  exit 1
fi
[[ "${1:-}" == "--check" ]] && exit 0

unit_file="$(mktemp)"
trap 'rm -f "$unit_file"' EXIT
cat > "$unit_file" <<UNIT
[Unit]
Description=BMS Raspberry Pi camera source
After=network.target

[Service]
Type=simple
User=$(id -un)
SupplementaryGroups=video
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/pi_source.py
Restart=on-failure
RestartSec=3
TimeoutStopSec=10
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
StateDirectory=bms-pi-camera

[Install]
WantedBy=multi-user.target
UNIT

changed=0
sudo install -d "$INSTALL_DIR"
# pi_source.py imports only capture_settings.py; keep the host copy to exactly these files.
for name in pi_source.py capture_settings.py; do
  if ! cmp -s "${PROJECT_ROOT}/app/cameras/${name}" "${INSTALL_DIR}/${name}"; then
    sudo install -m 644 "${PROJECT_ROOT}/app/cameras/${name}" "${INSTALL_DIR}/${name}"
    changed=1
  fi
done
if ! cmp -s "$unit_file" "$UNIT"; then
  sudo install -m 644 "$unit_file" "$UNIT"
  sudo systemctl daemon-reload
  changed=1
fi
sudo systemctl enable --quiet bms-pi-camera.service
if [[ "$changed" == "1" ]] || ! systemctl is-active --quiet bms-pi-camera.service; then
  echo ">>> Restarting the Pi camera source (bms-pi-camera)"
  sudo systemctl restart bms-pi-camera.service
else
  echo ">>> Pi camera source is up to date"
fi
