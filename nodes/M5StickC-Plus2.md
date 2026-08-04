# M5StickC Plus2

Matter over Wi-Fi firmware for the BMS DOA `M5StickC Plus2`.

## Hardware

- Target: `ESP32-PICO-V3-02`, `8 MB` flash, `2 MB` PSRAM
- Transport: Matter over Wi-Fi
- Commissioning: BLE
- Display: `ST7789V2`, `135 x 240`, landscape UI
- Main button: Button A on `GPIO37`
- Battery voltage: `GPIO38` ADC, board divider requires `x2`
- Battery charge/discharge indication: inferred from ADC voltage trend because Plus2 has no readable PMIC charge-status register
- Power hold: `GPIO4` must be driven high after boot

## Device Model

- Endpoint `1`: `Temperature Sensor`
- Endpoint `2`: `Contact Sensor` for Button A state
- Endpoint `3`: `Relative Humidity Sensor`
- Endpoint `4`: `Pressure Sensor`

## Matter Identity

- VendorID: `0xFFF1`
- VendorName: `BMS DOA`
- ProductID: `0x8002`
- ProductName: `M5StickC Plus2`
- SoftwareVersion: current git commit
- Setup passcode: MAC-derived, unique per board
- Discriminator: MAC-derived, unique per board
- Current lab unit (`f0:24:f9:9d:dd:10`): passcode `20345744`, discriminator `3344 (0xD10)`
- Serial: runtime `BMS-M5C2-<MAC6>` from base MAC
- Wi-Fi DHCP hostname: same as serial

## Screens

The display wakes for 10 seconds after Button A is pressed, then turns off to
save battery. A short press wakes the display; if it is already awake, the same
short press cycles screens and requests an immediate Matter sensor report.

- `Status`: Matter fabric state, Wi-Fi state, IP, RSSI, battery voltage, estimate, and inferred `CHG`/`DIS` direction with the last voltage delta
- `Pair`: QR payload and manual pairing code; shown before commissioning and while a commissioning window is open
- `ENV`: auto-detected sensor model and temperature, humidity, pressure values
- `Device`: serial, MAC suffix, firmware, heap and uptime

Long Button A actions:

- Hold for `7 s`: open a commissioning window and show the `Pair` screen
- Hold for `15 s`: factory reset Matter state

## ENV Sensor Auto-Detection

The top ENV sensor is probed on the Grove bus first and on the StickC HAT I2C
pins second:

- Grove bus: SDA `GPIO32`, SCL `GPIO33`
- HAT bus: SDA `GPIO0`, SCL `GPIO26`
- ENV.III: SHT30 `0x44`, QMP6988 `0x70` or `0x56`
- ENV.IV: SHT40 `0x44`, BMP280 `0x76`

If the ENV sensor is missing or swapped at runtime, the node keeps Matter and
Wi-Fi online. After three consecutive read failures, the corresponding Matter
`MeasuredValue` is published as `null` and the display shows `LOST`. The node
then re-probes the I2C buses periodically and resumes publishing values when a
compatible sensor is found again.

ENV readings are published over Matter every 60 seconds. Pressing Button A also
forces a fresh ENV read and immediate Matter attribute update.

## Build

```bash
./scripts/build-node-m5stickc-plus2.sh
```

The build produces both a serial-flash image and a Matter OTA image:

- `nodes/m5stickcPlus2/matter-node/build/m5stickc_plus2_matter_node.bin`
- `nodes/m5stickcPlus2/matter-node/build/m5stickc_plus2_matter_node-ota.bin`

Dirty working-tree builds append `-dirty` to `SoftwareVersionString` and use a
timestamp-based `SoftwareVersionNumber`. This keeps Matter OTA images monotonic
during lab work even when changes are not committed yet.

## Flash

```bash
source /home/nomad375/.espressif/v5.4.1/esp-idf/export.sh
cd nodes/m5stickcPlus2/matter-node
idf.py -p /dev/ttyACM0 flash monitor
```

## Matter OTA

OTA is enabled for this node:

- `partitions.csv` has `ota_0` and `ota_1` app slots.
- `sdkconfig.defaults` enables `CONFIG_ENABLE_OTA_REQUESTOR=y`.
- `sdkconfig.defaults` enables `CONFIG_CHIP_OTA_IMAGE_BUILD=y`.

After the USB baseline has this layout, normal updates should use the generated
Matter OTA image. A typical local provider workflow is:

```bash
export CHIP_ROOT="$HOME/.espressif/esp-matter-release-v1.5/connectedhomeip/connectedhomeip"
cd "$CHIP_ROOT"
scripts/examples/gn_build_example.sh examples/ota-provider-app/linux out/ota-provider chip_config_network_layer_ble=false
out/ota-provider/chip-ota-provider-app \
  --filepath /home/nomad375/vscode/bms-et-sensors/nodes/m5stickcPlus2/matter-node/build/m5stickc_plus2_matter_node-ota.bin
```

Then announce that provider to the node from a controller on the same Matter
fabric:

```bash
chip-tool otasoftwareupdaterequestor announce-otaprovider \
  <PROVIDER_NODE_ID> 0 0 0 <M5_NODE_ID> 0
```

The requestor starts automatically after DNS-SD initialization and also runs its
periodic query timer.
