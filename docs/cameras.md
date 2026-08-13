# Cameras and AI Inspection

`cameras-app` is the local camera and inspection-media service. It runs with
host networking so it can reach cameras connected to the Raspberry Pi access
point on `wlan0`.

## Current capabilities

- Scan the configured AP subnet for RTSP servers.
- Register multiple cameras with individual RTSP paths and credentials.
- Register multiple streams from one physical camera as separate cards by using distinct RTSP paths such as `ch0` and `ch1`.
- Probe video and audio stream metadata with FFmpeg.
- Display RTSP video in a browser through a low-rate MJPEG proxy.
- Record live RTSP video to an H.264/AAC MP4 and add it directly to inspection files.
- Upload product images and videos for later defect analysis.
- Create an H.264/AAC MP4 browser copy for HEVC, Dolby Vision, or otherwise unsupported uploads while preserving the original.
- Delete an upload together with its generated browser copy after explicit confirmation.
- Keep camera credentials and media in `runtime/cameras/`; never commit them.

The default Thingino stream is `rtsp://thingino:thingino@<camera-ip>:554/ch0`.
Credentials may be changed per camera in the web UI.

## Start and access

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --build cameras-app dashboard
```

Open `http://<host>/cameras/`. The direct host endpoint is port `3090`.

## AI integration boundary

Uploaded files expose an analysis endpoint at
`POST /api/uploads/<asset-id>/analyze`. It intentionally returns
`not_configured` until a product-specific model is selected. A production
defect model requires representative good and defective samples, explicit
defect labels, camera/lighting constraints, and an acceptance threshold.

The intended first inference backend is ONNX Runtime. Model files and generated
results are runtime artifacts and must stay under `runtime/cameras/`.
