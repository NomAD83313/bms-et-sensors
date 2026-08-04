# DOA Radio Nodes Agent Notes

This repository contains firmware projects for BMS DOA Matter nodes.

## Repo Layout

- `esp32c6zero/matter-node` - Matter over Thread for `ESP32-C6-Zero`
- `esp32c6DevKitC/matter-node` - Matter over Thread for `ESP32-C6-DevKitC`
- `esp32c6Pico/matter-thread-node` - Matter over Thread for `ESP32-C6-Pico`
- `esp32c6Pico/matter-wifi-node` - Matter over Wi-Fi for `ESP32-C6-Pico`
- `esp32c6Pico/matter-wifi-sps30-node` - Matter over Wi-Fi with `SPS30` air-quality sensor for `ESP32-C6-Pico`
- `esp32c3SuperMini/matter-node` - Matter over Wi-Fi for `ESP32-C3-SuperMini`
- `m5stickc/matter-node` - Matter over Wi-Fi for first-generation `M5StickC`
- `m5stickcPlus2/matter-node` - Matter over Wi-Fi for `M5StickC Plus2`
- root `README.md` and board-specific `*.md` files are the canonical docs

## Documentation Rules

- Keep documentation at repo root in:
  - `README.md`
  - `ESP32-C6-Zero.md`
  - `ESP32-C6-DevKitC.md`
  - `ESP32-C6-Pico.md`
  - `ESP32-C3-SuperMini.md`
  - `M5StickC.md`
  - `M5StickC-Plus2.md`
- Do not reintroduce ad hoc files like `*-HELP.md`, `*-QUICKREF.html`, or duplicate per-board READMEs unless explicitly requested.
- The onboarding card PNG is the operator quick reference; do not maintain a parallel HTML quick reference.

## Language Preference

- The user wants answers in Russian language.
- All code listings, including code comments, must remain in English only.

## Firmware Rules

- `ESP32-C6-Zero` is a powered Thread node and should remain `Minimal End Device`.
- Do not switch `ESP32-C6-Zero` to `Sleepy End Device` in the main firmware profile without explicitly validating post-commissioning interview/subscription behavior against `python-matter-server`.
- A future battery-powered Thread node should use a separate low-power profile or separate firmware target.

## Pairing / Flashing Rules

- Normal app flashing should preserve NVS and keep existing pairing.
- `erase-flash`, NVS wipe, or factory reset require fresh commissioning.
- Call out clearly when a change does or does not require re-pairing.

## Git Hygiene

- Do not commit generated build output, local SDK churn, or runtime-only files unless explicitly requested.
- Keep `.vscode/` local to the user workspace.
