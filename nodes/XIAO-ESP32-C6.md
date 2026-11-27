# Seeed Studio XIAO ESP32-C6 + Expansion Base v1.1

The firmware in `xiaoEsp32C6/matter-wifi-node` uses Matter over 2.4 GHz Wi-Fi and BLE for initial commissioning.

## Wiring

- Plug the Grove BMA400 into a Grove I²C socket (D4/GPIO22 SDA, D5/GPIO23 SCL). The firmware verifies the Bosch chip ID at 0x15 or 0x14.
- Use the expansion board's LiPo connector for a compatible single-cell 3.7 V LiPo with matching connector polarity. With power disconnected, wire a 100 kΩ resistor from the expansion board's battery positive terminal to D0/GPIO0 and a second 100 kΩ resistor from D0 to battery ground. Place a 100 nF capacitor from D0 to ground. Verify the D0 voltage is approximately half the battery voltage before enabling the ADC option. The divider reserves the A0/D0 Grove signal and leaves the microSD chip-select line on D2 available.
- Enable **XIAO battery monitoring → Enable battery voltage measurement on D0** in `idf.py menuconfig` after installing the divider. If it is disabled, the battery readings remain null. The voltage-based percentage is an estimate affected by load and charging.

Seeed changed the expansion board charger from ETA6003A to SY6974B. If the firmware recognizes the SY6974B at I²C address 0x6B, it reads the charge state and reports it as Power Source attribute 26. The firmware leaves charger settings untouched. Battery voltage reporting uses the ADC divider described above. [Seeed product change notice](https://files.seeedstudio.com/wiki/Seeeduino-XIAO-Expansion-Board/document/PCN-103030356.pdf).

## Build and commission

Use the [ESP-IDF / ESP-Matter build environment](../docs/amd64-node-build-host.md). In the firmware directory run `idf.py build`, then flash the verified XIAO USB port with `idf.py -p /dev/ttyACM0 flash monitor` (adjust the port as needed). Normal flashing preserves the NVS partition on subsequent updates.

Use the onboarding code printed to the serial monitor and follow the [host BLE commissioning procedure](../docs/openthread.md#matter-server-commissioning-note), with the host's Wi-Fi credentials configured for the 2.4 GHz network. Keep the host and node on a network that passes IPv6 and mDNS. The XIAO user LED lights when Wi-Fi connects. This firmware uses the SDK development attestation credentials.

## Telemetry

The expansion board's SSD1306 OLED starts on **RAIL**, showing the selected axis, estimated direction, and last transition time as seconds since boot. Press **USER** (D1/GPIO1) to cycle through rail, tilt, acceleration, and battery/Wi-Fi status. On **RAIL**, hold USER for at least 0.8 seconds to switch X/Y and save the choice. Keep the carriage stationary for about one second at boot and after changing axes. Align the selected axis with the rail, with its positive direction pointing right. The display works before Matter commissioning.

The firmware reads the BMA400 at a target rate of 100 Hz and reports orientation approximately once per second. It estimates rail velocity by integrating bias-corrected acceleration. A start needs about 30 ms of confirmation. A stop needs a braking impulse followed by about 120 ms of quiet samples at low estimated velocity. The recorded timestamp is the start of the confirmed condition. Sustained acceleration bias, weak braking, vibration and changes in sensor tilt can cause missed or incorrect transitions. Verify the thresholds on the actual rail.

The manufacturer-specific cluster `0xFFF1FC00` (decimal `4294048768`) on endpoint 0 has these numeric attributes:

| ID | Value |
| --- | --- |
| 0, 1, 2 | X, Y, Z acceleration in mg |
| 3 | Pitch in hundredths of a degree |
| 4 | Roll in hundredths of a degree |
| 5 | Rail transition packed as `uptime_ms * 8 + axis * 4 + state`. Axis is 0 for X and 1 for Y. State is 0 for unknown, 1 for stop, 2 for left, and 3 for right. Zero uptime indicates no detected transition yet. |

Tilt assumes gravity dominates the sample. The firmware suppresses tilt outside 0.8–1.2 g and roll near the pitch singularity. The accelerometer alone cannot determine yaw. Sensor axes follow the BMA400 board's mounting orientation.

Endpoint 1 reports Matter Power Source `0x2F` attribute 11 in mV and attribute 12 in half-percent units, sampled approximately every 30 seconds. Matter Collector can ingest numeric `attribute_updated` events when the server exposes them. Verify manufacturer-specific attributes with the running Matter Server before relying on live host reporting.

The node stays associated with the Wi-Fi network for live reporting, including when running from the battery.

## References

- [XIAO pinout](https://wiki.seeedstudio.com/xiao_esp32c6_getting_started/)
- [Expansion Base](https://wiki.seeedstudio.com/Seeeduino-XIAO-Expansion-Board/)
- [Grove BMA400](https://wiki.seeedstudio.com/Grove-3-Axis_Digital_Accelerometer%C2%B116g_Ultra-low_Power-BMA400/)
- [Bosch datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bma400-ds000.pdf)
