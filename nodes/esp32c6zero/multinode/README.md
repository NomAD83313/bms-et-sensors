# ESP32-C6-Zero Multinode

ESP-IDF / esp-matter project for the ESP32-C6-Zero "multinode" firmware.

The long-term intent is to keep the cable-flashed firmware as a stable Matter
capability platform:

- Matter 1.5.x over Thread
- commissioning and diagnostics
- virtual sensor endpoint slots
- sensor channel abstraction
- transform/calibration logic runtime
- vendor-defined logic bundle update path

Wireless updates should change endpoint policy and sensor calculations, not the
native Matter/radio stack.

The initial version is intentionally close to `../matter-node`: it preserves the
known-good Thread commissioning, LED status, BOOT button, internal temperature
endpoint, contact endpoint, and seven ADC voltage endpoint slots while giving
the new firmware its own product id, serial prefix, build artifact name, and
onboarding card.

The current built-in logic activates only `GPIO0` and `GPIO5` voltage updates.
The other ADC voltage endpoints are present as cable-flashed capabilities and
return `null` until logic enables them.

## Flash layout

The firmware uses a single cable-flashed `factory` app partition. Native Matter,
Thread, radio, drivers, endpoint factories, diagnostics, and the logic runtime
belong in that app image.

Small wireless updates are stored as signed behavior bundles in `logic_a` and
`logic_b`. The active/pending bundle state lives in `logic_meta`, while sensor
calibration and local constants live in `calib`.

The exact firmware-vs-bundle contract is documented in `LOGIC_BUNDLE.md`.
The current and planned board-level capabilities are documented in
`BOARD_CAPABILITIES.md`.
The exact cable-flashed firmware contents and live Matter paths are documented
in `CURRENT_FIRMWARE.md`.
The future high-speed telemetry split is documented in
`LOGIC_BUNDLE.md#high-speed-data-plane`.
