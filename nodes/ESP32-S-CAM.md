# ESP32-S-CAM

Matter-only firmware profile for AI Thinker `ESP32-CAM`.

## Scope

- Matter over Wi-Fi
- GPIO `4` flash LED exposed as Matter `OnOff`
- Status LED on GPIO `33` for local commissioning/runtime indication

Camera capture and HTTP streaming are intentionally disabled in this branch.

## Current status

Working:
- Matter stack starts and advertises over DNS-SD
- BLE onboarding payloads are generated on boot
- Node can be commissioned into `matter-server`
- `OnOff` controls GPIO `4` flash LED

Not in scope for now:
- `esp_camera` initialization
- `snapshot`/`stream` HTTP endpoints
- camera-side transport/integration logic

## Commissioning data

```text
BLE QR code:     MT:6FCJ142C00KA0648G00
BLE manual code: 34970112332
Passcode:        20202021
Discriminator:   3840
DHCP hostname:   runtime `BMS-CAM-<MAC6>` from base MAC
```

## Build and flash

```bash
cd /home/ets/bms-et-sensors/nodes/esp32sCam/camera-node
source ~/.espressif/v5.4.1/esp-idf/export.sh
idf.py build
idf.py -p /dev/serial/by-id/usb-1a86_USB2.0-Ser_-if00-port0 flash
```
