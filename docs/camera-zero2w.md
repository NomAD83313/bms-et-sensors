# Zero 2 W camera source

The first deployed node runs Raspberry Pi OS Lite 64-bit Trixie with a Sony IMX415.
It provides `rtsp://<node-ip>:8554/cam` to the Pi 5 camera manager.

```
IMX415 -> native MediaMTX on Zero (hardware H.264) -> shared MediaMTX on Pi 5
       -> camera-media (preview/recording) -> camera-analysis (normal-view detection)
```

There is no Docker or detector on the Zero. The central camera containers stay
`camera-media`, `camera-analysis` and the `camera-hub` relay, whatever the node count. Native MediaMTX handles capture, hardware encoding and RTSP together;
no separate rpicam-vid or FFmpeg process is needed on the source node.

## Installation

Copy `scripts/install-zero-camera.sh`, `config/zero2w-camera.example.json`, both
`config/bms-zero-camera*.service.in` templates, and `app/cameras/zero_control.py`
with `app/cameras/capture_settings.py` to the node, preserving their relative paths.
Before installation, add `dtoverlay=imx415` to `/boot/firmware/config.txt`, reboot, and
confirm that `rpicam-hello --list-cameras` reports the IMX415. Do not add the `cam0`
parameter on a Zero 2 W; it selects a Compute Module camera route. Then run:

```bash
sudo ./scripts/install-zero-camera.sh <camera-user>
```

The installer pins MediaMTX 1.21.3 and verifies the downloaded ARM64 archive's SHA256.
It installs `/usr/local/bin/mediamtx`, `/var/lib/bms-zero-camera/mediamtx.json`,
`/opt/bms-zero-camera/zero_control.py` with its validator, and the
`bms-zero-camera.service` / `bms-zero-camera-control.service` systemd units. Repeated installation preserves the node's
existing camera configuration and restarts its service. No credentials are stored
in these repository templates. Node configuration and downloaded binaries are
runtime installation files and are not copied back into Git.

The example permits RTSP reading only from `10.42.0.1` and localhost. Adjust
`authInternalUsers[].ips` in the node configuration if the central AP address changes.
It disables other streaming protocols, publishing and the MediaMTX management API.
A separate restricted HTTP agent serves only capture settings on port 8084.

## Initial capture settings

- 1280 x 720, 10 FPS, H.264 Main, 2 Mbit/s.
- Hardware H.264 is explicitly required; it will not silently fall back to software.
- An IDR frame every 10 frames permits joining the stream about once a second.
- IMX415 uses its full `3864:2192:10:P` sensor mode at every output resolution. The
  capture-control agent preserves this mode when output resolution changes. Existing
  OV5647 configurations continue to select
  `1296:972:10:P` or `2592:1944:10:P` based on output size.
- Auto exposure; no audio or local recording.
- Capture starts on demand and stops five seconds after the final reader leaves.
  Central Stop camera also stops the sensor after the hub releases its connection;
  it does not shut down the node or its RTSP service.

In the main Cameras UI, add an RTSP camera using the **node's** URL,
`rtsp://<node-ip>:8554/cam`. The manager configures its central relay automatically.
Use a stable DHCP address for the node. Choose the analysis area and learn a normal
view before enabling detection. In **Add camera**, choose **Raspberry Pi Zero 2 W** and enter the node's RTSP URL.
The manager registers the capture endpoint on the same host at port 8084 automatically,
even if the node is temporarily offline. No per-camera source-code or registry edits
are required. A generic RTSP addition also detects an online BMS Zero capture agent
through its `/api/capabilities` identity marker. Install the current node package
on each new Zero so it exposes the same capture API. The common UI reads each node's
capabilities and shows the same supported resolution and frame-rate controls. The shared Camera capture panel then offers 640x360, 1280x720 and
1920x1080 at 5/10/15 FPS. Unsupported snapshots and larger resolutions are hidden.
The control agent validates every request and atomically saves the node configuration;
MediaMTX reloads it without a privileged HTTP service. Its settings survive service
restarts and reboot. Both APIs accept only the configured central/localhost reader IPs;
POST additionally requires the camera-control header. Port 8084 must not be exposed
through public forwarding. Resolution changes pause detection and invalidate the
previous baseline and normal sessions; relearn after checking the new view.
All three output resolutions retain the full `3864:2192:10:P` IMX415 sensor mode;
MediaMTX scales the output while preserving its 16:9 field of view. Exposure remains
automatic. An initial cold-start preview request can return 503 while the sensor starts;
the UI retries automatically.

## Diagnostics

```bash
rpicam-hello --list-cameras
systemctl status bms-zero-camera
journalctl -u bms-zero-camera -n 50 --no-pager
sudo systemctl restart bms-zero-camera bms-zero-camera-control
```

Verified on the first node: IMX415 detected, hardware encoder selected in the service
log, RTSP reports 1280 x 720 at 10 FPS, central relay and JPEG preview work.
The camera is added as `zero-1`; its normal-view learning is intentionally left to
the user after positioning the camera.

References: [MediaMTX camera support](https://mediamtx.org/docs/static-sources/raspberry-pi-cameras),
[Raspberry Pi camera software](https://www.raspberrypi.com/documentation/accessories/camera-software.html).
