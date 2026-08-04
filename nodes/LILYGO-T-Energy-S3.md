# LILYGO T-Energy-S3

Matter over Wi-Fi firmware for the BMS DOA `LILYGO T-Energy-S3`.

## Hardware

- Target: `ESP32-S3`
- Detected board: `ESP32-S3 (QFN56)`, revision `v0.2`
- Detected flash: `16 MB`
- Detected PSRAM: `8 MB`, embedded
- Transport: Matter over Wi-Fi
- Commissioning: BLE
- Button: `BOOT` on `GPIO 0`
- Status LED: charger/power LED is hardware-only on the tested board
- Battery ADC: `GPIO 3` / `ADC1_CHANNEL_2`, board divider `x2`

Connected lab board:

- Port: `/dev/ttyACM0`
- Stable path: `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:97:47:44-if00`
- MAC: `44:1b:f6:97:47:44`

## Device Model

- Endpoint `1`: `Temperature Sensor`
- Endpoint `2`: `Relative Humidity Sensor`
- Endpoint `3`: `Pressure Sensor`
- Endpoint `4`: `Contact Sensor`
- Endpoint `5`: `Power Source`

The temperature, humidity, and pressure endpoints report the BME280 sensor when
it is present. If the BME280 is unavailable, the temperature endpoint falls back
to the ESP32-S3 internal temperature sensor. The contact endpoint reports the
BOOT button state. The power source endpoint reports battery voltage,
percentage, presence, charge level, and charge state.

Battery percent is a simple Li-ion estimate:

- `3300 mV` -> `0%`
- `4200 mV` -> `100%`
- values between them are linearly interpolated

Charge state is inferred from voltage rise between telemetry samples. It is a
useful field indicator, not a charger IC reading.

## Battery Runtime Profile

This firmware keeps Matter over Wi-Fi online, so it does not use deep sleep.
Instead, it reduces active current while preserving normal Matter reachability:

- USB-powered ENV telemetry period: `8 s` by default (`BMS_USB_TELEMETRY_PERIOD_MS`)
- USB-powered heartbeat log period: `60 s` by default (`BMS_USB_HEARTBEAT_PERIOD_MS`)
- Wi-Fi modem power save: enabled at runtime
- ESP-IDF power management and tickless idle: enabled in `sdkconfig.defaults`
- BME280: forced-mode sample on telemetry tick, sleep between samples
- Optional external RGB LED: slow steady-state refresh; disabled by default

When the USB Serial/JTAG host is not connected, the firmware automatically
enters a battery saver profile:

- ENV telemetry period: `120 s`
- Heartbeat log period: `600 s`
- BOOT button polling: slower cadence
- Status LED updates: slower cadence when the RGB LED is present

This matches the battery-powered operating mode on the board. If you need a
true VBUS-powered decision instead of USB-host detection, the hardware needs a
separate USB power sense signal.

Changing `sdkconfig.defaults` affects clean builds. If an existing local
`sdkconfig` was generated before this profile, remove or regenerate it before
measuring battery life.

## LED Indication

The tested board's visible blue LED behaves as a hardware charger/power
indicator: it is on while charging over USB and off on battery-only power. It is
not controlled by this firmware, so it cannot show battery status.

Firmware RGB indication is optional for an external WS2812-style LED:

- Default: disabled (`BMS_RGB_LED_GPIO=-1`)
- Define `BMS_RGB_LED_GPIO=<gpio>` at build time to enable one external RGB LED
- No on-board firmware-controlled LED GPIO is confirmed on the tested board.

Current patterns:

- Boot: white breathing pulse
- Commissioning/open window: blue double pulse
- Wi-Fi disconnected: amber slow blink
- Running: dim green
- Low battery: red warning pulse
- BOOT long-press preview and factory reset: blue/red fast patterns

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8008`
- ProductName: `LILYGO T-Energy-S3`
- SoftwareVersion: current git commit
- Setup passcode: `20202021`
- Discriminator: `3840 (0xF00)`
- Serial: runtime `BMS-TES3-<MAC6>` from base MAC
- Wi-Fi DHCP hostname: same as serial, for example `BMS-TES3-974744`

## Current Factory Firmware

The connected board initially contained a LILYGO/Arduino-style firmware:

- ESP-IDF string: `v4.4.6`
- Arduino path strings: `framework-arduinoespressif32@3.20014.231204`
- Product strings: `LilyGo-AABB`, `T-Energy-S3`
- Partition table: two OTA app slots, SPIFFS, coredump

The first BMS firmware intentionally replaces this with the same Matter over
Wi-Fi model used by the C3 node.

## Build

Run from the repository root:

```bash
./scripts/build-node-lilygo-tenergy-s3.sh
```

Build normal firmware with WS2812 status output on an externally wired GPIO:

```bash
BMS_RGB_LED_GPIO=<gpio> ./scripts/build-node-lilygo-tenergy-s3.sh
```

## Flash

Use the stable USB path when the same board is connected:

```bash
cd nodes/lilygoTEnergyS3/matter-wifi-node
source /home/ets/.espressif/v5.4.1/esp-idf/export.sh
idf.py -p /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:97:47:44-if00 flash monitor
```

For the first lab flash, do not use `erase-flash` unless a clean commissioning
state is explicitly wanted. A partition-table change will replace the previous
Arduino/LILYGO layout.

## Important Files

- `lilygoTEnergyS3/matter-wifi-node/main/main.cpp`
- `lilygoTEnergyS3/matter-wifi-node/sdkconfig.defaults`
- `lilygoTEnergyS3/matter-wifi-node/partitions.csv`
- `lilygoTEnergyS3/matter-wifi-node/CMakeLists.txt`
