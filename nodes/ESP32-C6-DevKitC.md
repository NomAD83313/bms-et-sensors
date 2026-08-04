# ESP32-C6-DevKitC

Matter over Thread firmware for the BMS DOA `ESP32-C6-DevKitC-1-N4` node.

## Hardware

- Target: `ESP32-C6`, `4 MB` flash
- Transport: Matter over Thread
- Commissioning: BLE
- RGB LED: onboard LED on `GPIO 8`
- Button: `BOOT` on `GPIO 9`

## Device Model

- Endpoint `1`: `Temperature Sensor`
- Endpoint `2`: `Contact Sensor`
- Matter actions: `Reboot`, plus `Identify` on the root device and contact sensor endpoint

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8002`
- ProductName: `ESP32-C6-DevKitC`
- SoftwareVersion: current git commit
- QR code: `MT:06PS042C00KA0648G00`
- Manual pairing code: `34970112332`
- Setup passcode: `20202021`
- Discriminator: `3840 (0xF00)`
- Serial: runtime `BMS-C6D-<MAC6>` from base MAC

## Intended Role

This board is a powered Matter over Thread development node. It is suitable for:

- lab sensor nodes
- powered Thread mesh participants
- integration testing against Matter controllers

It is not currently documented as a battery profile.

## Thread Mode

This project is configured as `FTD` (`Full Thread Device`).

Practical meaning:

- the node is powered, not battery-first
- it is router-capable in the Thread mesh
- it may carry `temperature` and `contact` sensor endpoints at the same time
- it is not a Border Router

## Pairing Persistence

Normal `flash` updates preserve NVS and do not require re-pairing. `erase-flash`,
factory reset, or NVS wipe require fresh commissioning.

## OTA Prototype

This board is the first lab target for OTA.

Current OTA layout:

| Partition | Offset | Size | Purpose |
| --- | --- | --- | --- |
| `nvs` | `0x9000` | `0x6000` | Matter pairing and runtime state |
| `otadata` | `0xf000` | `0x2000` | ESP-IDF OTA slot metadata |
| `phy_init` | `0x11000` | `0x1000` | RF calibration data |
| `ota_0` | `0x20000` | `0x1F0000` | First app slot |
| `ota_1` | `0x210000` | `0x1F0000` | Second app slot |

Build the OTA prototype with a clean build-local sdkconfig:

```bash
cd esp32c6DevKitC/matter-node
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota \
  -DSDKCONFIG=$PWD/build-ota/sdkconfig \
  build
```

For a real OTA transfer test, the OTA image must carry a `SoftwareVersion`
higher than the firmware already running on the device. The 2026-04-27 lab
DevKitC currently runs `0xD63B98A5`, so the first OTA test image uses:

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota-next \
  -DSDKCONFIG=$PWD/build-ota-next/sdkconfig \
  -DMATTER_NODE_SOFTWARE_VERSION_U32=D63B98A6 \
  -DMATTER_NODE_FIRMWARE_VERSION=v1.1.9-ota1 \
  build
```

Expected outputs:

- `build-ota/esp32c6_devkitc_matter_node.bin`
- `build-ota/esp32c6_devkitc_matter_node-ota.bin`
- `build-ota-next/esp32c6_devkitc_matter_node-v1.1.9-ota1.local-update.json`
  is a Matter Server local-update descriptor for the first OTA transfer test.

To make `python-matter-server` find a local update, place both files in its
`--ota-provider-dir` and restart the server so it reloads local `*.json`
descriptors:

```text
esp32c6_devkitc_matter_node-ota.bin
esp32c6_devkitc_matter_node-v1.1.9-ota1.local-update.json
```

Serial-flash the first OTA-layout image without erasing flash:

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota -p /dev/ttyACM0 flash
```

Do not use `erase-flash` for migration testing. NVS remains at the same offset.
On 2026-04-27 the lab DevKitC was migrated to this partition table without
erasing NVS; serial logs showed `thread_attached=1`, `commissioned=1`, and
Matter reports acknowledged by the server.

Latest measured DevKitC OTA build:

- App slot size: `0x1F0000`
- App binary with Matter OTA Requestor: `0x1C85B0`
- Free space in smallest app slot: `0x27A50`
- Matter OTA package: `build-ota/esp32c6_devkitc_matter_node-ota.bin`
- OTA package VID/PID: `0xFFF1` / `0x8002`

The first real Matter OTA transfer to the lab DevKitC node completed
successfully on 2026-04-27 using the local Matter Server update descriptor.

## Device Identity

Each flashed board derives its Matter `SerialNumber` from the chip base MAC.

Practical meaning:

- many `ESP32-C6-DevKitC` boards can run the same firmware image
- each board still has a stable per-device identity
- `NodeId` and `RLOC16` may change by fabric or network state, but the serial stays tied to the hardware
- onboarding codes are shared for this lab profile; production devices should move to per-device factory data and credentials

## Build

Run from `esp32c6DevKitC/matter-node`:

```bash
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py build
```

## Flash

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py -p /dev/ttyACM0 flash
```

## Important Files

- `esp32c6DevKitC/matter-node/main/main.cpp`
- `esp32c6DevKitC/matter-node/sdkconfig.defaults`
- `esp32c6DevKitC/matter-node/CMakeLists.txt`
- `esp32c6DevKitC/matter-node/tools/generate_onboarding_card.py`
- `esp32c6DevKitC/matter-node/matter-node-card.png`
