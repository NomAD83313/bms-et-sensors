# ESP32-C6-Zero Board Capabilities

This document separates what the current firmware already exposes from what the
Waveshare ESP32-C6-Zero hardware can support in future `multinode` firmware.

## Current firmware outputs

Implemented in the current cable-flashed firmware:

- internal ESP32-C6 temperature sensor
- BOOT button state on `GPIO9`
- commissioning/runtime status through the onboard WS2812 RGB LED on `GPIO8`
- Matter/Thread commissioning and diagnostics state
- calibrated ADC voltage endpoint slots for `GPIO0..GPIO6`
- built-in static logic currently activates voltage updates for `GPIO0` and
  `GPIO5` only

Current Matter paths:

| Source | Endpoint | Cluster | Attribute | Path | Unit |
| --- | --- | --- | --- | --- | --- |
| Internal chip temperature | `1` | `1026` / `0x402` | `0` | `1/1026/0` | `0.01 C` |
| BOOT button state | `2` | `69` / `0x45` | `0` | `2/69/0` | boolean |
| `GPIO0` voltage | `3` | `144` / `0x90` | `4` | `3/144/4` | mV, active |
| `GPIO1` voltage | `4` | `144` / `0x90` | `4` | `4/144/4` | nullable, inactive |
| `GPIO2` voltage | `5` | `144` / `0x90` | `4` | `5/144/4` | nullable, inactive |
| `GPIO3` voltage | `6` | `144` / `0x90` | `4` | `6/144/4` | nullable, inactive |
| `GPIO4` voltage | `7` | `144` / `0x90` | `4` | `7/144/4` | nullable, inactive |
| `GPIO5` voltage | `8` | `144` / `0x90` | `4` | `8/144/4` | mV, active |
| `GPIO6` voltage | `9` | `144` / `0x90` | `4` | `9/144/4` | nullable, inactive |

For active ADC voltage endpoints, Matter Server values are millivolts. For
example, `289` means `0.289 V`, while `3500` means `3.500 V`.

## Onboard signals

Reserved or already used by the board/firmware:

| Signal | Pin | Notes |
| --- | --- | --- |
| WS2812 RGB LED | `GPIO8` | Used by local status indicator |
| BOOT button | `GPIO9` | Active-low input and ESP32-C6 strapping pin |
| USB D- | `GPIO12` | Native USB Serial/JTAG |
| USB D+ | `GPIO13` | Native USB Serial/JTAG |
| UART0 TX | `TX` / `GPIO16` | Serial console/programming path |
| UART0 RX | `RX` / `GPIO17` | Serial console/programming path |

`GPIO8`, `GPIO9`, and `GPIO15` are ESP32-C6 strapping pins, so external circuits
on these pins must not force unsafe boot levels.

## Exposed analog inputs

The ESP32-C6 ADC-capable pins are:

| GPIO | ADC channel | Board status |
| --- | --- | --- |
| `GPIO0` | `ADC1_CH0` | Exposed |
| `GPIO1` | `ADC1_CH1` | Exposed |
| `GPIO2` | `ADC1_CH2` | Exposed |
| `GPIO3` | `ADC1_CH3` | Exposed |
| `GPIO4` | `ADC1_CH4` | Exposed |
| `GPIO5` | `ADC1_CH5` | Exposed |
| `GPIO6` | `ADC1_CH6` | Exposed |

These pins can measure an external analog signal within the ESP32-C6 ADC input
range. They cannot directly measure 5 V, 12 V, battery packs, or industrial
sensor outputs above the ADC-safe range without an external divider or signal
conditioning circuit.

## Supply voltage measurement

The Waveshare ESP32-C6-Zero does not provide a documented onboard voltage divider
or fuel-gauge path for measuring USB 5 V, the 3.3 V rail, or a battery voltage.

Practical meaning:

- the node can read voltage on `GPIO0` through `GPIO6`
- the node cannot read its own 5 V USB input by default
- the node cannot read a battery voltage by default
- the node cannot safely read voltages above the ADC-safe range directly
- measuring supply or battery voltage requires external hardware connected to an
  ADC-capable GPIO

## Good first `multinode` source list

For the first capability bundle contract, advertise only conservative sources:

```json
[
  { "id": "internal.tsens", "kind": "internal_temperature" },
  { "id": "gpio.boot", "kind": "gpio", "gpio": 9, "mode": "input_pullup" },
  { "id": "adc.gpio0", "kind": "adc", "gpio": 0, "channel": 0 },
  { "id": "adc.gpio1", "kind": "adc", "gpio": 1, "channel": 1 },
  { "id": "adc.gpio2", "kind": "adc", "gpio": 2, "channel": 2 },
  { "id": "adc.gpio3", "kind": "adc", "gpio": 3, "channel": 3 },
  { "id": "adc.gpio4", "kind": "adc", "gpio": 4, "channel": 4 },
  { "id": "adc.gpio5", "kind": "adc", "gpio": 5, "channel": 5 },
  { "id": "adc.gpio6", "kind": "adc", "gpio": 6, "channel": 6 }
]
```

Do not advertise board supply voltage as a source until a real measurement path
is added to the carrier circuit.

## No-solder ADC check

The `multinode` firmware logs ADC diagnostics for `GPIO0` through `GPIO6`.
It also publishes calibrated active ADC voltages over Matter as
`ElectricalPowerMeasurement.Voltage` attributes in millivolts.
The telemetry period is currently 5 seconds, so Matter Server updates can appear
with a visible delay.

Safe quick checks:

- leave the pins floating and confirm that the log prints changing raw values
- briefly connect an ADC-capable GPIO to `GND` with a jumper/probe and expect a
  value near 0
- briefly connect an ADC-capable GPIO to `3V3` with a jumper/probe and expect a
  high value near the top of the ADC range

Never connect `5V` directly to `GPIO0` through `GPIO6`. Use a voltage divider or
signal conditioner for voltages above the ADC-safe range.
