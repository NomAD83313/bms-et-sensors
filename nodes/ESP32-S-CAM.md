# ESP32-S-CAM

Matter-over-Wi-Fi and HTTP camera firmware for AI Thinker `ESP32-CAM`.

## Scope

- Matter over Wi-Fi
- OV2640 capture using the AI Thinker pin map
- HTTP JPEG snapshot at `/snapshot.jpg`
- HTTP MJPEG stream at `/stream.mjpeg`
- GPIO `4` flash LED exposed as Matter `OnOff`
- Status LED on GPIO `33` for local commissioning/runtime indication

The web camera starts before Matter commissioning. It becomes reachable after
Matter commissioning provisions the Raspberry Pi AP Wi-Fi credentials.

## Current status

Working:
- Matter stack starts and advertises over DNS-SD
- BLE onboarding payloads are generated on boot
- Node can be commissioned into `matter-server`
- `OnOff` controls GPIO `4` flash LED

Camera defaults:
- JPEG VGA (`640x480`)
- one PSRAM frame buffer to preserve memory for Matter/BLE commissioning
- HTTP port `80`

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
