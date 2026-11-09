# Micro-Epsilon thermoMETER CT serial integration

## Protocol

thermoMETER CT sensors use a binary protocol by default. Selected LT variants can be switched to ASCII, but ASCII must not be assumed for an unknown device. The collector in this repository uses the binary classic CT protocol at 115200 baud, 8 data bits, no parity, one stop bit, and no flow control.

The USB interface appears as a CP210x-based `Infrared Online Sensor Adapter`. Its USB serial number identifies the interface, not the sensor head or controller. Keep both identities in the runtime registry:

- `serial`: USB interface serial used by udev, for example `CT-00004944`.
- `sensor_serial`: internal sensor serial returned by binary command `0x0E`, for example `6060099`.

## Classic CT burst stream

The configured marked stream uses this value order:

1. Object/process temperature
2. Sensor-head temperature
3. Controller-box temperature
4. Object/process temperature (duplicate)

Each frame is ten bytes: synchronization marker `AA AA` followed by four unsigned 16-bit big-endian values. A temperature word is decoded as `(word - 1000) / 10` degrees Celsius.

Classic CT burst commands use packed half-byte channel identifiers. The checksum setting is device-configurable, so the registry must explicitly set `checksum_enabled` to match the device. Do not infer checksum behavior from the Optris or Micro-Epsilon brand name.

## CT versus CTi

Classic CT and newer CTi devices use different burst command layouts:

- `classic_ct`: packed half-byte burst string and `52 01` start command.
- `optris_cti`: sixteen full-byte burst fields and a three-byte interval parameter.

Select the command family from verified device documentation or captured responses. The `AA AA` marked four-word stream used by the currently installed devices is handled as `classic_ct`.

## Runtime and diagnostics

The tracked repository contains templates only. Actual device profiles live in `runtime/pyrometers-devices.json` and must not be committed.

For a stream failure, check all three layers separately:

1. Kernel logs for CP210x/USB errors.
2. Raw binary input for continuing `AA AA` frames.
3. Collector health fields such as `last_measurement_at`, `stream_stale_count`, and `serial_reopen_count`.

The collector uses non-blocking reads so a stalled USB adapter cannot indefinitely prevent stale-stream recovery.

## References

- Micro-Epsilon thermoMETER CT operating instructions.
- CompactConnect command documentation distributed with the vendor software.
