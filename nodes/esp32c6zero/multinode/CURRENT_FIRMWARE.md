# ESP32-C6-Zero Multinode Current Firmware

This document describes exactly what is currently compiled into the
cable-flashed `multinode` firmware and what can already be observed over Matter.

## Firmware identity

| Field | Value |
| --- | --- |
| Project | `esp32c6_multinode` |
| Target | `esp32c6` |
| Matter version family | Matter 1.5.x / esp-matter release v1.5 |
| Vendor ID | `0xFFF1` |
| Product ID | `0x8001` |
| Product name | `ESP32-C6-Zero Multinode` |
| Serial prefix | `BMS-C6M-` |
| Transport | Matter over Thread |
| Full Matter OTA image update | Disabled |

Full firmware updates are cable-flashed. Wireless behavior bundles are reserved
for future small logic updates and are not implemented yet.

## Flash layout

The current 4 MB flash layout is:

| Partition | Type | Subtype | Offset | Size | Update path | Purpose |
| --- | --- | --- | --- | --- | --- | --- |
| `nvs` | data | `nvs` | `0x9000` | `0x6000` | cable/runtime | Matter fabric, counters, serial, OpenThread storage |
| `phy_init` | data | `phy` | `0xf000` | `0x1000` | cable/runtime | RF calibration data |
| `app` | app | `factory` | `0x10000` | `0x340000` | cable only | Native firmware: Matter, Thread, BLE, drivers, endpoints, diagnostics |
| `logic_meta` | data | `0x40` | `0x350000` | `0x4000` | future wireless/cable | Future active/pending logic bundle state |
| `logic_a` | data | `0x41` | `0x354000` | `0x28000` | future wireless/cable | Future logic bundle slot A |
| `logic_b` | data | `0x42` | `0x37c000` | `0x28000` | future wireless/cable | Future logic bundle slot B |
| `calib` | data | `0x43` | `0x3a4000` | `0x10000` | future wireless/cable | Future local calibration constants |
| `coredump` | data | `coredump` | `0x3b4000` | `0x10000` | runtime | Crash dump |

There is no `ota_0` / `ota_1` full-firmware app pair. This is intentional:
radio/Matter/native code changes are cable-flashed, while future wireless updates
are expected to be small behavior bundles in `logic_a` / `logic_b`.

## Built-in hardware use

| Function | GPIO / source | Notes |
| --- | --- | --- |
| WS2812 status LED | `GPIO8` | Local boot, commissioning, Thread, running, and button-hold indication |
| BOOT button | `GPIO9` | Active-low input; also used for commissioning/factory-reset hold logic |
| Internal temperature | ESP32-C6 temperature sensor | Published as Matter temperature |
| ADC diagnostics | `GPIO0..GPIO6` / `ADC1_CH0..ADC1_CH6` | Logged over UART every telemetry cycle |
| ADC voltage endpoint slots | `GPIO0..GPIO6` / `ADC1_CH0..ADC1_CH6` | Published over Matter as electrical voltage values in mV when enabled by logic |

`GPIO0..GPIO6` must only see ADC-safe voltages. Do not connect `5V` directly.

## Current Matter endpoints

| Endpoint | Device meaning | Cluster | Attribute | Matter path | Unit / encoding |
| --- | --- | --- | --- | --- | --- |
| `1` | Internal chip temperature | `TemperatureMeasurement` / `1026` / `0x402` | `MeasuredValue` / `0` | `1/1026/0` | centi-degrees Celsius: `3500` means `35.00 C` |
| `2` | BOOT button contact state | `BooleanState` / `69` / `0x45` | `StateValue` / `0` | `2/69/0` | boolean |
| `3` | `GPIO0` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `3/144/4` | active, millivolts |
| `4` | `GPIO1` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `4/144/4` | inactive, nullable |
| `5` | `GPIO2` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `5/144/4` | inactive, nullable |
| `6` | `GPIO3` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `6/144/4` | inactive, nullable |
| `7` | `GPIO4` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `7/144/4` | inactive, nullable |
| `8` | `GPIO5` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `8/144/4` | active, millivolts |
| `9` | `GPIO6` ADC voltage | `ElectricalPowerMeasurement` / `144` / `0x90` | `Voltage` / `4` | `9/144/4` | inactive, nullable |

The `ElectricalPowerMeasurement` cluster is implemented with firmware delegates.
Each ADC voltage endpoint has its own delegate. The delegate stores the latest
calibrated voltage and notifies Matter reporting when the value changes.

The current built-in static logic is:

```json
{
  "adc_voltage_enabled": {
    "gpio0": true,
    "gpio1": false,
    "gpio2": false,
    "gpio3": false,
    "gpio4": false,
    "gpio5": true,
    "gpio6": false
  }
}
```

This logic block is implemented in `main/logic_config.cpp` as the current
cable-flashed fallback source, reported at boot as `cable-fallback`. It is the
placeholder interface for the future `logic_a` / `logic_b` OTA behavior bundle.
Later the same activation policy should be loaded from a validated wireless
bundle through the same `GetActiveLogicConfig()` path.

## Telemetry cadence

The firmware telemetry task currently runs every `5000 ms`.

Each cycle:

- reads the internal temperature and updates endpoint `1`
- logs ADC diagnostics for `GPIO0..GPIO6`
- reads calibrated ADC voltages for active logic channels
- currently updates `GPIO0` on endpoint `3` and `GPIO5` on endpoint `8`

Matter Server may show the value with additional subscription/reporting delay.
For manual testing, expect a visible delay of roughly one telemetry period plus
Matter transport/reporting latency.

## Verified no-solder test

The current flashed firmware was verified with this behavior:

| Pin state | Expected Matter value |
| --- | --- |
| active pin floating | unstable, often around `600..900 mV` |
| active pin touched to `GND` | low, near `0 mV` depending on contact |
| active pin touched to `3V3` | high, near the top of the ADC range |

Observed examples:

- `289` on `3/144/4` means `0.289 V`
- `3500` on `3/144/4` means `3.500 V`
- `289` on `8/144/4` means `0.289 V`
- `3500` on `8/144/4` means `3.500 V`

Do not confuse this with temperature:

- `3500` on `1/1026/0` means `35.00 C`
- `3500` on `3/144/4` means `3.500 V`
- `3500` on `8/144/4` means `3.500 V`

## Not implemented yet

The following are reserved by the firmware layout and documentation but are not
implemented in the current binary:

- logic bundle loader
- `logic_meta` active/pending state machine
- signed `logic_a` / `logic_b` wireless updates
- dynamic endpoint activation from JSON
- calculation pipeline runtime
- sandbox VM runtime
- calibration storage in `calib`
- OTA activation of ADC voltage endpoints
- non-voltage mappings for ADC channels, such as pressure or flow
