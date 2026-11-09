# Ahlborn ALMEMO 2490 (USB) Protocol Reference

## Physical / USB link
- USB data cable ZA 1919 DKU is a USB->RS232 converter and appears as a virtual COM port.
- The interface cable is connected to output socket A1 on the device; parameters are stored in the plug and recognized automatically.
- Max baud rate: 115.2 kBd (for V7 devices up to 921.6 kBd).
- Interface is ASCII text over serial; software flow control XON/XOFF only.

## Serial basics
- Commands are a letter, optional minus, and 0-6 digits.
- Each command/response ends with line feed.
- Data format: 8 data bits, no parity, 1 stop bit (8N1).

## Device version (V6 vs V7)
- Query software version: `t0` -> response like `8590-9KL 6.52`.
- The first digit of the version identifies V6 devices (e.g., 6.xx).
- V7 has protocol changes: table-mode only, new channel notation with dot, retained CRC with k-flag variants.

## V6 protocol - core read commands
From `Command overview V6 protocol`:
- Select measuring point: `Mxx`
- Select input channel only: `Exx`
- Output measured value (no new query): `p` -> `01: +0023.5 C`
- Output measured value with time: `P01` -> `12:34:00 01: +0023.5 C`
- Output measured value with designation: `P35` -> `01: +0023.5 C Temperature`

## V7 protocol - core read commands
From `Command overview V7 protocol`:
- Select measuring channel: `Mxxx.x`
- Select input channel only: `Exxx.x`
- Output measured value (no new query): `p` -> `0.0;23.5;C`
- Output measured value with time: `P01` -> `12:34:00;0.0;23.5;C`

## Protocol changes for V7
- Only table mode is used for transmission of extended variables.
- Output format `Nx` is omitted.
- Quotation marks for time/date omitted; non-numeric measured values are in quotes.
- Channel format uses dot and leading zero suppression (`xxx.x`).
- V6 CRC protocol retained, with k-flag differentiation for configuration changes.

## Useful commands for integration
- Device selection on network: `Gxx` (select device address in multi-device network)
- Device info: `t0` (software version)
- Baud change is possible but should be avoided unless necessary (default 9600 in cable); command family `f1 b6..b9` for 9600..230400.

## Minimal startup checklist (USB)
1. Identify USB-serial port (Linux likely `/dev/ttyUSB*` for CP210x).
2. Start at 9600 8N1, XON/XOFF, CR/LF.
3. Send `t0` to get device type/version.
4. If V6:
   - Select channel with `Mxx` or `Exx`, then `p` / `P01` for reading.
5. If V7:
   - Use `Mxxx.x` or `Exxx.x`, then `p` / `P01` for reading.

## Open questions for device-specific behavior
- Exact channel numbering scheme for ALMEMO-2490 (likely V7). Verify via P15 output or device menu.
- Whether the device expects/uses CRC mode by default in your configuration.

## Current app status (v4.1.0, 2026-04-19)
- Observed device/version in live tests: `A2490-2    6.30` (`V6`).
- The main freeze cause was consistent with software flow control: if the host cleared buffers while the device remained in `XOFF` state, the ALMEMO link could stall until reconnect or power cycle.
- `almemo-collector` now uses explicit `XON` after serial buffer resets and batches multi-step UI actions on the server side.
- Field-tested UI flows no longer reproduced cable/device loss during `Print Cycle`, `Continuous Query`, `Sensor Info`, smoothing writes, or time/date writes.
- `Sensor Info` now uses the fast path `G00; Mxx; f2 P00; P32` instead of polling a larger set of per-field commands.
- Peak values and peak timestamps (`P02`, `P03`, `P28`, `P29`) are no longer fetched by the button and must be queried manually when needed.
- Remaining issue: latency under active live streaming is still higher than the no-stream baseline and should be treated as a performance task, not as the original cable-loss defect.
