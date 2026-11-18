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
