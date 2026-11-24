#!/usr/bin/env bash
# Install a native hardware-encoded RTSP camera source on a Zero 2 W (64-bit OS).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAMERA_USER="${1:-${SUDO_USER:-}}"
VERSION="1.21.3"
ARCHIVE_SHA256="27f038237cb5f77a724f6afc58eca2798f2bc4edf9989895631a80eeb41a4fe2"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo $0 <camera-user>" >&2
  exit 1
fi
if [[ "$(uname -m)" != aarch64 ]] || ! grep -q 'Raspberry Pi Zero 2 W' /proc/device-tree/model; then
  echo "This installer requires a Zero 2 W running a 64-bit OS." >&2
  exit 1
fi
if [[ ! "$CAMERA_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || ! id "$CAMERA_USER" >/dev/null 2>&1 || [[ "$CAMERA_USER" == root ]]; then
  echo "Specify an existing non-root camera user." >&2
  exit 1
fi
for tool in curl tar sha256sum python3 rpicam-hello; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done

stage="$(mktemp -d)"
trap 'python3 -c "import shutil,sys; shutil.rmtree(sys.argv[1])" "$stage"' EXIT
if [[ ! -x /usr/local/bin/mediamtx ]] || [[ "$(/usr/local/bin/mediamtx --version)" != "v$VERSION" ]]; then
  curl -fsSL --retry 2 --connect-timeout 10 --max-time 180 \
    "https://github.com/bluenviron/mediamtx/releases/download/v$VERSION/mediamtx_v${VERSION}_linux_arm64.tar.gz" \
    -o "$stage/mediamtx.tar.gz"
  printf '%s  %s\n' "$ARCHIVE_SHA256" "$stage/mediamtx.tar.gz" | sha256sum -c -
  tar -xzf "$stage/mediamtx.tar.gz" -C "$stage" mediamtx
  install -m 755 "$stage/mediamtx" /usr/local/bin/mediamtx
fi
# Preserve node settings; the unprivileged control service updates this file atomically.
install -d -o "$CAMERA_USER" -g "$(id -gn "$CAMERA_USER")" -m 755 /var/lib/bms-zero-camera
if [[ ! -e /var/lib/bms-zero-camera/mediamtx.json ]]; then
  install -o "$CAMERA_USER" -g "$(id -gn "$CAMERA_USER")" -m 600 \
    "$PROJECT_ROOT/config/zero2w-camera.example.json" /var/lib/bms-zero-camera/mediamtx.json
fi
install -d -m 755 /opt/bms-zero-camera
install -m 644 "$PROJECT_ROOT/app/cameras/zero_control.py" "$PROJECT_ROOT/app/cameras/capture_settings.py" /opt/bms-zero-camera/
usermod -aG video,render "$CAMERA_USER"
for service in bms-zero-camera bms-zero-camera-control; do
  sed "s/@CAMERA_USER@/$CAMERA_USER/g" "$PROJECT_ROOT/config/$service.service.in" > "$stage/$service.service"
  install -m 644 "$stage/$service.service" "/etc/systemd/system/$service.service"
done
systemctl daemon-reload
systemctl enable bms-zero-camera.service bms-zero-camera-control.service
systemctl restart bms-zero-camera.service bms-zero-camera-control.service
systemctl is-active bms-zero-camera.service bms-zero-camera-control.service
echo "RTSP: rtsp://<node-ip>:8554/cam; capture controls: http://<node-ip>:8084."

# Wi-Fi fallback: without a Wi-Fi connection for 2 minutes the node opens a WPA2 setup
# hotspot with a setup page on port 80 (see docs/camera-zero2w.md). The hotspot password is
# common to the camera nodes and never stored in Git: pass BMS_CAMERA_SETUP_PASSWORD on the
# first installation; later installations keep the node's value unless a new one is passed.
for tool in nmcli iw; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite for the Wi-Fi setup: $tool" >&2; exit 1; }
done
dpkg -s dnsmasq-base >/dev/null 2>&1 || { echo "Missing prerequisite for the Wi-Fi setup: dnsmasq-base" >&2; exit 1; }
install -d -m 700 /etc/bms-zero-camera
hotspot_address="$(python3 - "$PROJECT_ROOT/config/zero2w-wifi-setup.example.json" /etc/bms-zero-camera/wifi-setup.json <<'PY'
import json, os, sys
from pathlib import Path
example, target = Path(sys.argv[1]), Path(sys.argv[2])
config = json.loads(example.read_text())
if target.exists():
    config.update(json.loads(target.read_text()))
password = os.environ.get("BMS_CAMERA_SETUP_PASSWORD", "")
if password:
    config["hotspot_password"] = password
if not 8 <= len(config["hotspot_password"]) <= 63:
    sys.exit("Set the common setup hotspot password (8-63 characters):\n"
             "  sudo BMS_CAMERA_SETUP_PASSWORD='...' ./scripts/install-zero-camera.sh <camera-user>")
temporary = target.with_name(target.name + ".tmp")
descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w") as stream:
    json.dump(config, stream, indent=2)
os.replace(temporary, target)
print(config["hotspot_address"].split("/")[0])
PY
)"
install -m 644 "$PROJECT_ROOT/app/cameras/zero_wifi_setup.py" /opt/bms-zero-camera/
# Captive portal: every name resolves to the setup page while the hotspot is up.
install -d -m 755 /etc/NetworkManager/dnsmasq-shared.d
printf 'address=/#/%s\n' "$hotspot_address" > /etc/NetworkManager/dnsmasq-shared.d/bms-camera-setup.conf
install -m 644 "$PROJECT_ROOT/config/bms-zero-wifi-setup.service.in" /etc/systemd/system/bms-zero-wifi-setup.service
systemctl daemon-reload
systemctl enable bms-zero-wifi-setup.service
systemctl restart bms-zero-wifi-setup.service
systemctl is-active bms-zero-wifi-setup.service
echo "Wi-Fi setup page: http://<node-ip>/ (central network) or http://${hotspot_address}/ on the setup hotspot."
