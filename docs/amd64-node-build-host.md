# AMD64 Build Host for Node Firmware

This document describes the recommended toolchain and host setup for building
the firmware projects under `nodes/` on a faster Linux amd64 machine.

The Raspberry Pi can still be used for flashing, commissioning, Matter Server,
OTBR, and runtime validation. The amd64 host is intended to produce firmware
artifacts quickly and reproducibly.

## Recommended Host

- CPU: modern amd64/x86_64 CPU with at least 8 hardware threads.
- RAM: 16 GB minimum, 32 GB recommended for repeated esp-matter builds.
- Disk: 40 GB free minimum, 80 GB recommended.
- Filesystem: local SSD/NVMe; avoid network filesystems for ESP-IDF build
  directories.
- OS: Ubuntu 22.04/24.04 LTS, Debian 12, or another recent systemd-based Linux.

Avoid building the ESP-IDF and esp-matter workspaces inside slow shared folders
from VM providers. Native Linux or a Linux VM with a local virtual disk is much
faster and less surprising.

## Required System Packages

Install the usual ESP-IDF and Matter build dependencies:

```bash
sudo apt update
sudo apt install -y \
  git wget flex bison gperf python3 python3-pip python3-venv \
  cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0 \
  libusb-1.0-0-dev pkg-config zip unzip xz-utils file \
  build-essential clang-format curl
```

Recommended extras:

```bash
sudo apt install -y \
  jq ripgrep tree socat picocom minicom avahi-utils
```

`picocom` or `minicom` is useful when the build host also flashes devices.
`avahi-utils` is useful for ad-hoc mDNS checks, but Matter commissioning should
still be validated on the runtime host.

The node builds also generate printable onboarding cards. Install the Python
dependency used by the card generator:

```bash
python3 -m pip install --user 'qrcode[pil]'
```

Alternative for stricter isolation: create a `.venv` inside each node project
and install that project's `tools/requirements.txt`. The CMake files prefer
`$PROJECT/.venv/bin/python` when it exists.

## Required Toolchain Versions

Use the same major toolchain versions as the Raspberry Pi runtime host:

- ESP-IDF: `v5.4.1`
- ESP Matter: local `esp-matter-release-v1.5`
- Target chips:
  - `esp32c6` for Thread nodes
  - `esp32c3` for the C3 Wi-Fi node
  - `esp32` for the ESP32-S-CAM project

The node CMake projects intentionally require an `ESP_MATTER_PATH` containing
`release-v1.5`. Do not silently point them at a different esp-matter checkout.

## Directory Layout

Use a layout matching the current project conventions:

```text
~/.espressif/
  v5.4.1/esp-idf/
  esp-matter-release-v1.5/
  tools/
~/src/bms-et-sensors/
```

The exact home directory can differ, but keep paths consistent on that host and
avoid spaces in the toolchain or repository path.

## ESP-IDF Setup

Clone and install ESP-IDF:

```bash
mkdir -p ~/.espressif/v5.4.1
git clone --branch v5.4.1 --recursive \
  https://github.com/espressif/esp-idf.git \
  ~/.espressif/v5.4.1/esp-idf

~/.espressif/v5.4.1/esp-idf/install.sh esp32,esp32c3,esp32c6
```

Create a small activation wrapper so build commands are stable:

```bash
mkdir -p ~/.espressif/tools
cat > ~/.espressif/tools/activate_idf_v5.4.1.sh <<'EOF'
#!/usr/bin/env bash
source "$HOME/.espressif/v5.4.1/esp-idf/export.sh"
EOF
chmod +x ~/.espressif/tools/activate_idf_v5.4.1.sh
```

## ESP Matter Setup

The firmware projects expect an esp-matter release-v1.5 checkout with its
Connected Home IP submodules and Pigweed/CIPD environment ready.

Recommended shape:

```bash
git clone --recursive <esp-matter-release-v1.5-source> \
  ~/.espressif/esp-matter-release-v1.5
```

If the release source is mirrored internally, use that mirror. The important
requirement is that this path contains:

```text
~/.espressif/esp-matter-release-v1.5/components
~/.espressif/esp-matter-release-v1.5/connectedhomeip/connectedhomeip
~/.espressif/esp-matter-release-v1.5/examples/common
```

Then initialize the esp-matter/Connected Home IP environment according to the
release instructions for that checkout. After setup, the following paths should
exist:

```text
~/.espressif/esp-matter-release-v1.5/connectedhomeip/connectedhomeip/.environment/cipd/packages/pigweed
~/.espressif/esp-matter-release-v1.5/connectedhomeip/connectedhomeip/.environment/cipd/packages/pigweed/bin
```

## Build Environment

Use this shell setup before building any Matter node:

```bash
source ~/.espressif/tools/activate_idf_v5.4.1.sh

export ESP_MATTER_PATH="$HOME/.espressif/esp-matter-release-v1.5"
export PW_PROJECT_ROOT="$ESP_MATTER_PATH/connectedhomeip/connectedhomeip"
export PW_ROOT="$PW_PROJECT_ROOT/third_party/pigweed/repo"
export _PW_ACTUAL_ENVIRONMENT_ROOT="$PW_PROJECT_ROOT/.environment"
export _PW_ENVIRONMENT_CONFIG_FILE="$PW_PROJECT_ROOT/scripts/setup/environment.json"
export PATH="$PW_PROJECT_ROOT/.environment/cipd/packages/pigweed:$PW_PROJECT_ROOT/.environment/cipd/packages/pigweed/bin:$PW_PROJECT_ROOT/out/host:$PATH"
```

Important: do not source the full Connected Home IP `.environment/activate.sh`
for these builds unless it has been validated on the host. In this project it
can override the ESP-IDF Python environment and cause errors such as missing
`esp_idf_monitor`.

Optional speedups:

```bash
export IDF_CCACHE_ENABLE=1
export CCACHE_DIR="$HOME/.cache/ccache"
ccache --max-size=20G
```

Keep each board's build directory local to the amd64 host. Do not copy `build/`
directories between Raspberry Pi and amd64 machines.

## Repository Setup

Clone the project:

```bash
mkdir -p ~/src
git clone git@github.com:NomAD83313/bms-et-sensors.git ~/src/bms-et-sensors
cd ~/src/bms-et-sensors
```

No runtime `.env` or `runtime/` state is required for firmware compilation.
Do not copy Matter Server fabric state or Thread datasets to the build host.

## Build Commands

### ESP32-C6-Zero

```bash
cd ~/src/bms-et-sensors/nodes/esp32c6zero/matter-node
idf.py build
```

Expected serial app image:

```text
build/esp32c6_matter_node.bin
```

### ESP32-C6-Zero Multinode

```bash
cd ~/src/bms-et-sensors
./scripts/build-node-esp32c6zero-multinode.sh
```

Expected serial app image:

```text
build/esp32c6_multinode.bin
```

The script enforces a build time limit and prints the elapsed time. Override the
limit when needed:

```bash
BUILD_TIMEOUT_SEC=7200 ./scripts/build-node-esp32c6zero-multinode.sh
```

If an old local `build/` directory was configured with a different ESP-IDF
Python environment, rebuild it explicitly:

```bash
FIRMWARE_FULLCLEAN=1 ./scripts/build-node-esp32c6zero-multinode.sh
```

### ESP32-C6-Pico

```bash
cd ~/src/bms-et-sensors
./scripts/build-node-esp32c6-pico.sh
```

Expected serial app image:

```text
build/esp32c6_pico_matter_node.bin
```

The script enforces a build time limit and prints the elapsed time. Override the
limit when needed:

```bash
BUILD_TIMEOUT_SEC=7200 ./scripts/build-node-esp32c6-pico.sh
```

If an old local `build/` directory was configured with a different ESP-IDF
Python environment, rebuild it explicitly:

```bash
FIRMWARE_FULLCLEAN=1 ./scripts/build-node-esp32c6-pico.sh
```

### ESP32-C6-DevKitC

```bash
cd ~/src/bms-et-sensors/nodes/esp32c6DevKitC/matter-node
idf.py build
```

Expected serial app image:

```text
build/esp32c6_devkitc_matter_node.bin
```

### ESP32-C3-SuperMini

```bash
cd ~/src/bms-et-sensors
./scripts/build-node-esp32c3-supermini.sh
```

Expected serial app image:

```text
build/esp32c3_matter_node.bin
```

The script enforces a build time limit and prints the elapsed time. Override the
limit when needed:

```bash
BUILD_TIMEOUT_SEC=7200 ./scripts/build-node-esp32c3-supermini.sh
```

If an old local `build/` directory was configured with a different ESP-IDF
Python environment, rebuild it explicitly:

```bash
FIRMWARE_FULLCLEAN=1 ./scripts/build-node-esp32c3-supermini.sh
```

### ESP32-S-CAM

```bash
cd ~/src/bms-et-sensors/nodes/esp32sCam/camera-node
idf.py build
```

Expected serial app image:

```text
build/esp32s_cam_matter_node.bin
```

## Clean OTA Builds

For OTA-oriented builds, use a separate build directory and keep the sdkconfig
inside that directory:

```bash
idf.py \
  -B build-ota \
  -DSDKCONFIG="$PWD/build-ota/sdkconfig" \
  build
```

For a Matter OTA test image, explicitly bump the Matter software version:

```bash
idf.py \
  -B build-ota-next \
  -DSDKCONFIG="$PWD/build-ota-next/sdkconfig" \
  -DMATTER_NODE_SOFTWARE_VERSION_U32=<NEXT_HEX_VERSION> \
  -DMATTER_NODE_FIRMWARE_VERSION=<NEXT_VERSION_STRING> \
  build
```

The generated `*-ota.bin` and `*.local-update.json` artifacts are the files to
copy to the Matter Server OTA provider directory on the runtime host.

## Flashing Strategy

Fast build host, runtime flashing host:

1. Build on amd64.
2. Copy only the required artifact(s) to the Raspberry Pi.
3. Flash from the Raspberry Pi using its local ESP-IDF toolchain, or use OTA
   through Matter Server when that board's OTA path is being tested.

Direct flashing from amd64 is also fine when the board is physically attached:

```bash
idf.py -p /dev/ttyACM0 flash
```

Use stable serial paths when multiple boards are connected:

```bash
ls -l /dev/serial/by-id/
idf.py -p /dev/serial/by-id/<device> flash
```

Do not use `erase-flash` for normal updates or OTA-layout migration tests.
`erase-flash`, factory reset, or NVS wipe removes Matter pairing and requires
fresh commissioning.

## Commissioning Reminder

Compilation can happen on amd64, but commissioning should be performed on the
runtime host where `matter-server`, OTBR, Thread credentials, and the selected
BLE mode are configured.

For fresh or factory-reset Thread devices:

- use `commission_with_code` with the `MT:...` QR payload
- do not pass `network_only`

`network_only: true` disables BLE discovery and is only for devices already
reachable on the IP network.

## Verification Checklist

After setting up the amd64 host:

```bash
idf.py --version
python --version
cmake --version
ninja --version
git --version
ccache --version
```

Then build at least:

```bash
cd ~/src/bms-et-sensors/nodes/esp32c6zero/matter-node
idf.py fullclean
idf.py build
```

Expected signs of a healthy build:

- onboarding card generation succeeds
- `chip_gn` config/build completes
- app binary is generated
- partition size check reports free space
- Matter OTA package generation completes when enabled by the project

## Common Problems

- `ESP_MATTER_PATH must point to local esp-matter release-v1.5`
  - Set `ESP_MATTER_PATH` exactly to the local release-v1.5 checkout.
- `gn` or Pigweed tools are missing
  - Reinitialize the Connected Home IP environment and add the Pigweed CIPD
    paths to `PATH`.
- `No module named esp_idf_monitor`
  - The ESP-IDF Python environment was probably overridden. Re-source
    `activate_idf_v5.4.1.sh` and avoid the full CHIP `.environment/activate.sh`.
- Build is slow after switching hosts
  - This is expected on the first build. Enable `ccache` and keep build
    directories on local SSD/NVMe.
- Flash succeeds but Matter pairing disappears
  - The board was erased or NVS moved. Normal `flash` preserves pairing only
    when the NVS partition remains compatible.
- Commissioning fails after a clean flash
  - Commission from the runtime host through internal `hci0` BLE, using QR
    payload and no `network_only`.
  - For devices already reachable on IP, use `MATTER_BLE_MODE=disabled` and
    commission with `network_only: true`.

## What Not To Copy To The Build Host

- `.env` from the runtime host, unless a specific non-secret build variable is
  needed and reviewed.
- `runtime/` directories.
- Matter Server fabric/controller state.
- Thread active dataset output.
- Any `ot-ctl dataset active -x` output.

The amd64 host should be a firmware build machine, not another source of Matter
or Thread runtime truth.
