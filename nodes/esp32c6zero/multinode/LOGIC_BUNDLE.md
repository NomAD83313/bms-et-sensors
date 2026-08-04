# Multinode Logic Bundle Contract

This document defines the boundary between the cable-flashed firmware and the
small wireless logic bundle stored in `logic_a` / `logic_b`.

## Firmware vs bundle

The cable-flashed firmware owns native capabilities:

- Matter 1.5.x stack and endpoint factories
- Thread, radio, BLE commissioning, networking, diagnostics
- built-in sensor and bus drivers
- high-rate sampling primitives
- calculation pipeline runtime
- optional sandbox VM runtime
- signature, hash, version, and rollback checks for logic bundles
- storage access to `logic_meta`, `logic_a`, `logic_b`, and `calib`

The logic bundle owns policy:

- which supported endpoints are active
- which supported clusters and attributes are exposed by each endpoint
- which firmware-provided sensor source feeds each attribute
- sampling rates and reporting policy within firmware limits
- calculation pipelines built from supported operators
- optional VM modules when the firmware enables `bms_vm_v1`
- calibration references and constants that do not replace `calib`

The bundle must not contain native ESP32 code, Matter stack code, radio code,
new drivers, or new Matter cluster implementations.

## Flash slots

`logic_a` and `logic_b` are immutable A/B slots for signed behavior bundles.
The firmware writes a new bundle only to the inactive slot, validates it, and
then updates `logic_meta`.

`logic_meta` stores active/pending bundle state:

- active slot
- pending slot
- last confirmed slot
- bundle version
- schema version
- payload hash
- rollback state
- boot/apply attempt counters
- last apply error

`calib` stores local calibration values and sensor constants that should survive
logic bundle replacement.

## Human format

The authoring format is JSON. The flash format may be CBOR, protobuf, Matter TLV,
or another compact binary encoding generated from the same schema.

The JSON schema is declarative. It describes sources, pipelines, endpoints, and
reporting. It is not arbitrary code.

## Bundle structure

```json
{
  "schema_version": 1,
  "bundle_version": 1,
  "requires": {
    "product": "esp32c6_multinode",
    "min_firmware_schema": 1
  },
  "sources": [],
  "calculations": [],
  "endpoints": []
}
```

## Sources

A source names a firmware-supported input. The source can represent a physical
channel, a board-local signal, or a virtual signal.

Initial source kinds:

- `internal_temperature`
- `gpio`
- `adc`
- `i2c_sensor`
- `spi_sensor`
- `virtual`

Example ADC source:

```json
{
  "id": "adc_pressure_0",
  "kind": "adc",
  "unit": "mv",
  "adc_unit": 1,
  "channel": 3,
  "attenuation": "11db",
  "sample_rate_hz": 100,
  "oversampling": 16
}
```

Firmware decides whether a requested source is available on the current board.
Unavailable sources must make dependent attributes nullable or faulted according
to the endpoint policy.

## Calculations

A calculation converts one source or calculation into a publishable value.

Initial pipeline operators:

- `scale`
- `offset`
- `linear_map`
- `polynomial`
- `lookup_table`
- `clamp`
- `median`
- `moving_average`
- `low_pass_iir`
- `high_pass_iir`
- `rate_limit`
- `debounce`
- `hysteresis`
- `rms`
- `peak_to_peak`
- `fault_if_stale`

Example pressure calculation:

```json
{
  "id": "pressure_filtered",
  "input": "adc_pressure_0",
  "pipeline": [
    { "op": "median", "window": 5 },
    { "op": "low_pass_iir", "alpha": 0.15 },
    {
      "op": "linear_map",
      "in_min": 500,
      "in_max": 4500,
      "out_min": 0,
      "out_max": 1000
    },
    { "op": "clamp", "min": 0, "max": 1000 }
  ],
  "output_unit": "kpa"
}
```

Optional VM calculation:

```json
{
  "id": "custom_pressure_filter",
  "input": "adc_pressure_0",
  "runtime": "bms_vm_v1",
  "module": "pressure_filter.vm",
  "limits": {
    "period_ms": 10,
    "max_cycles": 2000,
    "state_bytes": 128
  },
  "output_unit": "kpa"
}
```

The VM runtime, if enabled, is sandboxed:

- no direct hardware access
- no native pointers
- no dynamic allocation
- fixed state memory
- fixed cycle budget
- fixed input and output types
- no network or storage access

## Endpoints

An endpoint maps calculations to Matter attributes.

Initial endpoint types:

- `temperature_sensor`
- `humidity_sensor`
- `pressure_sensor`
- `flow_sensor`
- `illuminance_sensor`
- `occupancy_sensor`
- `contact_sensor`
- `boolean_state`
- `generic_analog_sensor`
- `vendor_vibration_sensor`
- `vendor_diagnostic`

Example pressure endpoint:

```json
{
  "slot": 1,
  "endpoint_id": 1,
  "type": "pressure_sensor",
  "label": "Hydraulic pressure",
  "attributes": [
    {
      "cluster": "pressure_measurement",
      "attribute": "measured_value",
      "source": "pressure_filtered",
      "matter_unit": "0.1_kpa",
      "scale": 10,
      "reporting": {
        "min_interval_ms": 1000,
        "max_interval_ms": 10000,
        "min_change": 5
      },
      "fault": {
        "on_source_missing": "nullable",
        "on_out_of_range": "clamp"
      }
    }
  ]
}
```

## Vibration example

Precise high-rate sampling belongs to firmware. The bundle can select the source,
window, filter, native DSP primitive, and endpoint mapping.

```json
{
  "id": "vibration_peak_x",
  "input": "accel0.x",
  "pipeline": [
    { "op": "high_pass_iir", "cutoff_hz": 2.0 },
    {
      "op": "fft_peak",
      "window": "hann",
      "samples": 1024,
      "min_hz": 5,
      "max_hz": 700,
      "output": "peak_frequency_hz"
    }
  ],
  "output_unit": "hz"
}
```

`fft_peak` is only valid if the cable-flashed firmware advertises that operator.
Otherwise the bundle must be rejected before activation.

## Validation rules

Before activation the firmware must validate:

- bundle signature
- payload hash
- schema version
- product compatibility
- monotonic or explicitly allowed bundle version
- slot and endpoint id limits
- source availability or declared fallback behavior
- supported endpoint types
- supported clusters and attributes
- supported pipeline operators
- pipeline memory use
- pipeline cycle budget
- reporting interval limits
- Matter attribute value ranges

Invalid bundles remain inactive.

## Current implementation state

The current `multinode` firmware implements a fixed cable-flashed endpoint set:

- Thread commissioning
- LED status
- BOOT button handling
- internal temperature endpoint
- contact sensor endpoint
- seven ADC voltage endpoint slots using `ElectricalPowerMeasurement.Voltage`
- built-in `logic_config.cpp` fallback logic that activates only `GPIO0` and
  `GPIO5`

The logic bundle loader, `logic_meta`, pipeline runtime, VM runtime, and dynamic
endpoint policy are planned by this contract but are not implemented yet. The
future loader should replace the current `GetActiveLogicConfig()` source without
changing Matter endpoint creation.

The live endpoint paths and units are documented in `CURRENT_FIRMWARE.md`.

## High-speed data plane

Matter remains the control and state plane. It is suitable for:

- commissioning and fabric membership
- endpoint discovery
- slow sensor state
- computed sensor values
- stream enable/disable commands
- stream configuration
- device health and diagnostics

High-rate raw samples should not be reported as individual Matter attribute
updates. For example, an ADC or accelerometer may be sampled locally at
`1000 Hz`, but the Matter model should normally expose derived values such as
RMS, peak, average, calibrated pressure, or dominant vibration frequency at a
much lower reporting rate.

If raw high-speed data is required, the planned architecture is a separate data
plane:

```text
Matter over Thread:
  - commission the device
  - expose endpoint capabilities
  - configure sampling and filters
  - start/stop the stream
  - report computed values and status

Wi-Fi data stream:
  - raw ADC samples
  - raw accelerometer samples
  - waveform chunks
  - debug captures
```

The Wi-Fi stream transport is not implemented in the current firmware. A future
cable-flashed firmware can add a stream runtime and use logic bundles only to
select sources, filters, rates, and destinations. Wi-Fi credentials, stream
authentication, destination configuration, backpressure, and Thread/Wi-Fi radio
coexistence must be treated as part of that future contract.
