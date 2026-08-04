# ESP32-C6-Zero

Matter over Thread firmware for the BMS DOA `ESP32-C6-Zero` node.

## Hardware

- Target: `ESP32-C6`, `4 MB` flash
- Transport: Matter over Thread
- Commissioning: BLE
- RGB LED: single onboard `WS2812` on `GPIO 8`
- Button: `BOOT` on `GPIO 9`

## Device Model

- Endpoint `1`: `Temperature Sensor` using the ESP32-C6 internal temperature sensor
- Endpoint `2`: `Contact Sensor` using the `BOOT` button

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8000`
- ProductName: `ESP32-C6-Zero`
- SoftwareVersion: current git commit
- QR code: `MT:Y.K9042C00A9OK0KO00`
- Manual pairing code: `31859518648`
- Setup passcode: `30541987`
- Discriminator: `3328 (0xD00)`
- Serial: runtime `BMS-C6Z-<MAC6>` from base MAC

## LED Status

- `white pulse` - boot
- `blue blink` - commissioning window open / not yet commissioned
- `yellow steady` - commissioned but Thread not attached
- `green steady` - commissioned and online
- `blue fast blink` - BOOT held for commissioning
- `red fast blink` - factory reset preview / reset active

## BOOT Button

- `< 7 s` - contact sensor only
- `7-15 s` - open a `180 s` commissioning window
- `>= 15 s` - factory reset

## Build

Run from `esp32c6zero/matter-node`:

```bash
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py build
```

## Flash

Normal app update:

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py -p /dev/ttyACM0 flash
```

Full clean state:

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py -p /dev/ttyACM0 erase-flash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py -p /dev/ttyACM0 flash
```

## Pairing Persistence

- `flash` / `app-flash` normally keep NVS and preserve pairing
- `erase-flash`, factory reset, or NVS wipe require fresh commissioning

## OTA Status

This board is the second OTA lab target after the `ESP32-C6-DevKitC`.

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
cd esp32c6zero/matter-node
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota \
  -DSDKCONFIG=$PWD/build-ota/sdkconfig \
  build
```

Do not use `erase-flash` for migration testing. NVS remains at the same offset,
so normal serial flashing of the OTA layout is expected to preserve pairing.

Measured OTA prototype on 2026-04-27:

- App image: `build-ota/esp32c6_matter_node.bin`, `0x1B8A90` bytes
- Matter OTA package: `build-ota/esp32c6_matter_node-ota.bin`, `0x1B8AEB` bytes
- OTA app slot size: `0x1F0000` bytes
- Free space in slot: `0x37570` bytes for serial app image, `0x37515` bytes for Matter OTA package
- Serial migration to the OTA layout preserved pairing on the lab board:
  `thread_attached=1`, `commissioned=1`, `window_open=0`

For a real OTA transfer test, the OTA image must carry a `SoftwareVersion`
higher than the firmware already running on the device. The 2026-04-27 lab Zero
currently runs `0x9CCD9A2E`, so the first OTA test image uses:

```bash
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota-next \
  -DSDKCONFIG=$PWD/build-ota-next/sdkconfig \
  -DMATTER_NODE_SOFTWARE_VERSION_U32=9CCD9A2F \
  -DMATTER_NODE_FIRMWARE_VERSION=v1.1.10-zero-ota1 \
  build
```

Prepared Zero OTA transfer files:

- `build-ota-next/esp32c6_matter_node-ota.bin`
- `build-ota-next/esp32c6_matter_node-v1.1.10-zero-ota1.local-update.json`

## Thread Device Mode

This node is intentionally treated as a powered Thread node and should stay
`Minimal End Device`.

Why:

- `python-matter-server` must finish post-commissioning interview and subscriptions
- forcing this firmware into `Sleepy End Device` caused successful commissioning
  followed by interview/subscription timeouts

Implication:

- this `ESP32-C6-Zero` firmware is the right base for a powered node
- a future battery node should use a separate low-power profile / separate
  firmware path, not this exact configuration

## Important Files

- `esp32c6zero/matter-node/main/main.cpp`
- `esp32c6zero/matter-node/sdkconfig.defaults`
- `esp32c6zero/matter-node/CMakeLists.txt`
- `esp32c6zero/matter-node/tools/generate_onboarding_card.py`
- `esp32c6zero/matter-node/matter-node-card.png`
