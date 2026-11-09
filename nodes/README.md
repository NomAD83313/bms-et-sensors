# DOA Radio Nodes

Firmware workspace for BMS DOA Matter nodes.

## Projects

- `esp32c6zero/matter-node` - Matter over Thread node for Waveshare-style `ESP32-C6-Zero`
- `esp32c6DevKitC/matter-node` - Matter over Thread node for `ESP32-C6-DevKitC-1-N4`
- `esp32c6Pico/matter-thread-node` - Matter over Thread node for Waveshare `ESP32-C6-Pico`
- `esp32c6Pico/matter-wifi-node` - Matter over Wi-Fi node for Waveshare `ESP32-C6-Pico`
- `esp32c6Pico/matter-wifi-sps30-node` - Matter over Wi-Fi air-quality node for Waveshare `ESP32-C6-Pico` with Sensirion `SPS30`
- `esp32c3SuperMini/matter-node` - Matter over Wi-Fi node for `ESP32-C3-SuperMini`
- `m5stickc/matter-node` - Matter over Wi-Fi node for first-generation `M5StickC`
- `m5stickcPlus2/matter-node` - Matter over Wi-Fi node for `M5StickC Plus2`
- `esp32sCam/camera-node` - Matter over Wi-Fi node for `ESP32-S-CAM` (camera runtime intentionally disabled)
- `lilygoTEnergyS3/matter-wifi-node` - Matter over Wi-Fi node for `LILYGO T-Energy-S3`

## Documentation

- [AMD64 build host setup](../docs/amd64-node-build-host.md)
- [ESP32-C6-Zero](./ESP32-C6-Zero.md)
- [ESP32-C6-DevKitC](./ESP32-C6-DevKitC.md)
- [ESP32-C6-Pico](./ESP32-C6-Pico.md)
- [ESP32-C3-SuperMini](./ESP32-C3-SuperMini.md)
- [M5StickC](./M5StickC.md)
- [M5StickC Plus2](./M5StickC-Plus2.md)
- [ESP32-S-CAM](./ESP32-S-CAM.md)
- [LILYGO T-Energy-S3](./LILYGO-T-Energy-S3.md)

## Shared Components

- `components/bms_node_core` — shared ESP-IDF component used by all board firmwares
  - `BoardIdentity` — POD struct describing per-board identity (VID, PID, names, serial prefix, HW/SW version, rotating-ID flag)
  - `DeviceInfoProvider` — implements `chip::DeviceLayer::DeviceInstanceInfoProvider`; builds a MAC-derived serial number at construction time
  - `install_device_identity()` — registers the provider with both the esp_matter layer and the chip layer, and persists the serial to the `chip-factory` NVS partition

Each board's `main.cpp` declares a `constexpr BoardIdentity` and a static `DeviceInfoProvider`, then calls `install_device_identity()` in `app_main`. Adding `bms_node_core` to a board's `REQUIRES` in its `main/CMakeLists.txt` is all that is needed to pull in the component.

## Notes

- Current commissioning data is lab-only and uses test VID/PID.
- Printable onboarding cards are generated from each project `CMakeLists.txt`.
- For normal app updates, keep NVS intact so the node stays paired.
- Full erase or factory reset requires fresh commissioning.
- Keep radio behavior inside the assigned standards: Matter over Thread via
  OTBR for Thread nodes, and Matter over Wi-Fi for Wi-Fi nodes.
- If a proposed feature, diagnostic path, or integration would add data outside
  those protocols, call that out explicitly before implementing it.
- `ESP32-S-CAM` is currently used as a Matter-only Wi-Fi node.
- Camera capture and HTTP streaming are intentionally removed from this branch
  and will be reintroduced later in a dedicated phase.

## OTA Status

OTA support is staged board-by-board.

| Board | OTA status | Notes |
| --- | --- | --- |
| `ESP32-C6-DevKitC` | Validated lab prototype | Uses two OTA app slots and Matter OTA Requestor. NVS remains at `0x9000`; first Matter OTA transfer succeeded on 2026-04-27. |
| `ESP32-C6-Zero` | Serial migration validated | Uses two OTA app slots and Matter OTA Requestor. Serial flashing to the OTA layout preserved pairing on 2026-04-27; over-the-air transfer still needs validation. Keep the powered `Minimal End Device` profile unchanged. |
| `ESP32-C6-Pico` | Serial migration prepared | Uses two OTA app slots and Matter OTA Requestor. Keep NVS at `0x9000`; serial migration should be flashed without `erase-flash` so pairing data stays intact. |
| `ESP32-C3-SuperMini` | Serial migration validated | Uses two OTA app slots and Matter OTA Requestor over Wi-Fi. Serial flashing to the OTA layout preserved Wi-Fi and Matter pairing on 2026-04-27; over-the-air transfer still needs validation. |
| `M5StickC` | Initial serial target | Uses two OTA app slots and Matter OTA Requestor over Wi-Fi. First-generation board support uses 4 MB flash, AXP192 battery readings, and the smaller ST7735S display. |
| `M5StickC Plus2` | Serial migration validated | Uses two OTA app slots and Matter OTA Requestor over Wi-Fi. Serial flashing to the OTA layout preserved pairing on 2026-05-18; generated `m5stickc_plus2_matter_node-ota.bin` is ready for provider-based OTA validation. Pairing code is MAC-derived and shown on the built-in display before commissioning. |

For the DevKitC prototype, build with a clean OTA sdkconfig so existing local
`sdkconfig` files do not mask `sdkconfig.defaults`:

```bash
cd esp32c6DevKitC/matter-node
source /home/ets/.espressif/tools/activate_idf_v5.4.1.sh
/home/ets/.espressif/tools/python/v5.4.1/venv/bin/python \
  /home/ets/.espressif/v5.4.1/esp-idf/tools/idf.py \
  -B build-ota \
  -DSDKCONFIG=$PWD/build-ota/sdkconfig \
  build
```

This creates:

- `build-ota/esp32c6_devkitc_matter_node.bin` for serial flashing into `ota_0`
- `build-ota/esp32c6_devkitc_matter_node-ota.bin` for a Matter OTA provider

First migration to the OTA partition table should be flashed without
`erase-flash`. The first DevKitC lab migration on 2026-04-27 preserved Matter
pairing because NVS stays at `0x9000`, and the first real Matter OTA transfer
succeeded. Validate rollback and failed-transfer behavior before copying the
layout to other boards.
