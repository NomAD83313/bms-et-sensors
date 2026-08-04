# ESP32-C3-SuperMini

Matter over Wi-Fi firmware for the BMS DOA `ESP32-C3-SuperMini`.

## Hardware

- Target: `ESP32-C3`, `4 MB` flash
- Transport: Matter over Wi-Fi
- Commissioning: BLE
- Status LED: blue onboard LED on `GPIO 8`, active-low
- Button: `BOOT` on `GPIO 9`
- Red LED: power indicator only

## Device Model

- Endpoint `1`: `Temperature Sensor`
- Endpoint `2`: `Contact Sensor`

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8001`
- ProductName: `ESP32-C3-SuperMini`
- SoftwareVersion: current git commit
- QR code: `MT:-24J042C00KA0648G00`
- Manual pairing code: `34970112332`
- Setup passcode: `20202021`
- Discriminator: `3840 (0xF00)`
- Serial: runtime `BMS-C3SM-<MAC6>` from base MAC
- Wi-Fi DHCP hostname: same as serial, for example `BMS-C3SM-86590C`

## Blue LED Status

- `breathe` - boot
- `double pulse` - commissioning
- `slow blink` - Wi-Fi not connected
- `dim steady` - online
- `fast blink` - BOOT hold
- `rapid blink` - reset

## Build

Run from `esp32c3SuperMini/matter-node`:

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

## OTA Status

This board is the first Matter over Wi-Fi OTA lab target.

Current OTA layout:

| Partition | Offset | Size | Purpose |
| --- | --- | --- | --- |
| `nvs` | `0x9000` | `0x6000` | Wi-Fi credentials, Matter pairing, and runtime state |
| `otadata` | `0xf000` | `0x2000` | ESP-IDF OTA slot metadata |
| `phy_init` | `0x11000` | `0x1000` | RF calibration data |
| `ota_0` | `0x20000` | `0x1F0000` | First app slot |
| `ota_1` | `0x210000` | `0x1F0000` | Second app slot |

Build the OTA prototype with a clean build-local sdkconfig:

```bash
cd esp32c3SuperMini/matter-node
source /home/nomad375/.espressif/tools/activate_idf_v5.4.1.sh
/home/nomad375/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/nomad375/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota \
  -DSDKCONFIG=$PWD/build-ota/sdkconfig \
  build
```

Do not use `erase-flash` for migration testing. NVS remains at the same offset,
so normal serial flashing of the OTA layout is expected to preserve Wi-Fi and
Matter pairing.

Measured OTA prototype on 2026-04-27:

- App image: `build-ota/esp32c3_matter_node.bin`, `0x1C1550` bytes
- Matter OTA package: `build-ota/esp32c3_matter_node-ota.bin`, `0x1C15AB` bytes
- OTA app slot size: `0x1F0000` bytes
- Free space in slot: `0x2EAB0` bytes for serial app image, `0x2EA55` bytes for Matter OTA package
- Serial migration to the OTA layout preserved pairing on the lab board:
  `wifi_connected=1`, `commissioned=1`, `window_open=0`

## Important Files

- `esp32c3SuperMini/matter-node/main/main.cpp`
- `esp32c3SuperMini/matter-node/sdkconfig.defaults`
- `esp32c3SuperMini/matter-node/CMakeLists.txt`
- `esp32c3SuperMini/matter-node/tools/generate_onboarding_card.py`
- `esp32c3SuperMini/matter-node/matter-node-card.png`
