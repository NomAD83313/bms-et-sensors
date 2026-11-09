#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/compose-common.sh"
bms_cd_project

command -v ffmpeg >/dev/null || { echo "Install ffmpeg for full-view capture padding first." >&2; exit 1; }
command -v rpicam-vid >/dev/null || { echo "Install rpicam-apps-core and rpicam-apps-encoder first." >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo "The Pi camera source requires systemd on the host." >&2; exit 1; }
# Install the source on the host so libcamera always matches the Pi kernel.
sudo install -d /usr/local/lib/bms-cameras
sudo install -m 644 app/cameras/pi_source.py app/cameras/capture_settings.py /usr/local/lib/bms-cameras/
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
ExecStart=/usr/bin/python3 /usr/local/lib/bms-cameras/pi_source.py
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
sudo install -m 644 "$unit_file" /etc/systemd/system/bms-pi-camera.service
sudo systemctl daemon-reload
sudo systemctl enable bms-pi-camera.service
sudo systemctl restart bms-pi-camera.service
"${COMPOSE[@]}" --profile pi-camera up -d --build pi-camera-recorder pi-camera-dashboard pi-camera-anomaly
for service in pi-camera-recorder pi-camera-dashboard pi-camera-anomaly; do
  if [[ -z "$("${COMPOSE[@]}" --profile pi-camera ps -a -q "$service")" ]]; then
    echo "ERROR: Compose service was not created: $service" >&2
    exit 1
  fi
done
# Ship camera navigation to an already running original dashboard as well.
if [[ -n "$("${COMPOSE[@]}" ps -q camera-dashboard)" ]]; then
  "${COMPOSE[@]}" --profile cameras up -d --no-deps --no-build camera-dashboard
fi
# Reload the new proxy route without interrupting the other services.
"${COMPOSE[@]}" exec -T dashboard nginx -t
"${COMPOSE[@]}" exec -T dashboard nginx -s reload

echo ">>> Raspberry Pi Camera: http://<host>/cameras/pi/"
