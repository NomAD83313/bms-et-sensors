# M5StickC

Matter over Wi-Fi firmware for the BMS DOA `M5StickC` first-generation board.

## Hardware

- Target: `ESP32-PICO-D4`, `4 MB` flash, no PSRAM
- Transport: Matter over Wi-Fi
- Commissioning: BLE
- Display: `ST7735S`, `80 x 160`, landscape UI
- Main button: Button A on `GPIO37`
- Red LED: `GPIO10`, active low
- Power management: `AXP192` on internal I2C SDA `GPIO21`, SCL `GPIO22`
- Battery voltage: read from `AXP192`, not from a direct ADC divider
- LCD pins: MOSI `GPIO15`, CLK `GPIO13`, DC `GPIO23`, RST `GPIO18`, CS `GPIO5`

## Device Model

- Endpoint `1`: `Temperature Sensor`
- Endpoint `2`: `Contact Sensor` for Button A state
- Endpoint `3`: `Relative Humidity Sensor`
- Endpoint `4`: `Pressure Sensor`

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8006`
- ProductName: `M5StickC`
- SoftwareVersion: current git commit
- Setup passcode: MAC-derived, unique per board
- Discriminator: MAC-derived, unique per board
- Serial: runtime `BMS-M5C-<MAC6>` from base MAC
- Wi-Fi DHCP hostname: same as serial

## ENV Sensor Auto-Detection

The external ENV sensor is probed on the Grove bus first and on the StickC HAT
I2C pins second:

- Grove bus: SDA `GPIO32`, SCL `GPIO33`
- HAT bus: SDA `GPIO0`, SCL `GPIO26`
- ENV.III: SHT30 `0x44`, QMP6988 `0x70` or `0x56`
- ENV.IV: SHT40 `0x44`, BMP280 `0x76`

If the ENV sensor is missing or swapped at runtime, the node keeps Matter and
Wi-Fi online. After three consecutive read failures, the corresponding Matter
`MeasuredValue` is published as `null` and the display shows `LOST`.

## Build

```bash
./scripts/build-node-m5stickc.sh
```

The build produces both a serial-flash image and a Matter OTA image:

- `nodes/m5stickc/matter-node/build/m5stickc_matter_node.bin`
- `nodes/m5stickc/matter-node/build/m5stickc_matter_node-ota.bin`

## Flash

```bash
source /home/ets/.espressif/v5.4.1/esp-idf/export.sh
cd nodes/m5stickc/matter-node
idf.py -p /dev/ttyUSB0 erase-flash flash monitor
```

Full erase removes NVS, Wi-Fi credentials, and Matter pairing state, so this
requires fresh commissioning.
