import datetime as dt
import os
import signal
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

try:
    from .camera_probe import build_rtsp_url, discover_rtsp_hosts, probe_camera
    from .camera_store import CameraStore
except ImportError:
    from camera_probe import build_rtsp_url, discover_rtsp_hosts, probe_camera
    from camera_store import CameraStore


PORT = int(os.getenv("CAMERAS_PORT", "3090"))
DATA_DIR = os.getenv("CAMERAS_DATA_DIR", "/runtime/cameras")
AP_SUBNET = os.getenv("CAMERAS_AP_SUBNET", "10.42.0.0/24")
DEFAULT_USERNAME = os.getenv("CAMERAS_DEFAULT_RTSP_USERNAME", "thingino")
DEFAULT_PASSWORD = os.getenv("CAMERAS_DEFAULT_RTSP_PASSWORD", "thingino")
MAX_UPLOAD_BYTES = int(os.getenv("CAMERAS_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "mp4", "mov", "mkv", "avi", "webm"}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
store = CameraStore(DATA_DIR)
recordings: dict[str, dict] = {}
recordings_lock = threading.Lock()


@app.after_request
def no_store(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "cameras-app"})


@app.get("/api/config")
def config():
    return jsonify({"ap_subnet": AP_SUBNET, "ai_enabled": False, "max_upload_mb": MAX_UPLOAD_BYTES // 1024 // 1024})


@app.get("/api/cameras")
def cameras():
    result = []
    for camera in store.list_cameras(include_credentials=False):
        full = store.get_camera(camera["id"])
        with recordings_lock:
            recording = recordings.get(camera["id"])
            recording_status = {"active": bool(recording), "started_at": recording.get("started_at") if recording else None}
        result.append({**camera, "probe": probe_camera(full, timeout=5), "recording": recording_status})
    return jsonify(result)


@app.post("/api/cameras")
def add_camera():
    payload = request.get_json(silent=True) or {}
    address = str(payload.get("address", "")).strip()
    if not address:
        return jsonify({"error": "address is required"}), 400
    camera = store.save_camera({
        "name": str(payload.get("name") or address).strip(),
        "address": address,
        "port": int(payload.get("port", 554)),
        "path": str(payload.get("path", "ch0")).strip().lstrip("/"),
        "username": str(payload.get("username", DEFAULT_USERNAME)),
        "password": str(payload.get("password", DEFAULT_PASSWORD)),
    })
    return jsonify(camera), 201


@app.post("/api/discover")
def discover():
    hosts = discover_rtsp_hosts(AP_SUBNET)
    added = []
    for address in hosts:
        full = {
            "name": f"Camera {address}", "address": address, "port": 554,
            "path": "ch0", "username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD,
        }
        status = probe_camera(full, timeout=5)
        camera = store.save_camera(full)
        added.append({**camera, "probe": status})
    return jsonify({"subnet": AP_SUBNET, "cameras": added})


@app.get("/api/cameras/<camera_id>/snapshot.jpg")
def snapshot(camera_id):
    camera = store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "camera not found"}), 404
    command = [
        "ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", build_rtsp_url(camera),
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=12, check=False)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "snapshot timed out"}), 504
    if result.returncode != 0 or not result.stdout:
        return jsonify({"error": "snapshot unavailable"}), 502
    return Response(result.stdout, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/cameras/<camera_id>/stream.mjpeg")
def stream(camera_id):
    camera = store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "camera not found"}), 404
    command = [
        "ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", build_rtsp_url(camera),
        "-an", "-vf", "fps=5,scale='min(1280,iw)':-2", "-q:v", "6", "-f", "mjpeg", "pipe:1",
    ]

    def frames():
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        buffer = bytearray()
        try:
            while process.stdout:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if start < 0 or end < 0:
                        break
                    frame = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.post("/api/cameras/<camera_id>/recording/start")
def start_recording(camera_id):
    camera = store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "camera not found"}), 404
    with recordings_lock:
        current = recordings.get(camera_id)
        if current and current["process"].poll() is None:
            return jsonify({"active": True, "started_at": current["started_at"]})
        asset_id = uuid.uuid4().hex
        stored_name = f"{asset_id}.mp4"
        temporary_name = f"{asset_id}.recording.mp4"
        temporary_path = store.uploads / temporary_name
        started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        command = [
            "ffmpeg", "-v", "error", "-rtsp_transport", "tcp", "-i", build_rtsp_url(camera),
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-y", str(temporary_path),
        ]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        recordings[camera_id] = {
            "process": process, "asset_id": asset_id, "stored_name": stored_name,
            "temporary_name": temporary_name, "started_at": started_at, "camera": camera,
        }
    return jsonify({"active": True, "started_at": started_at}), 201


@app.post("/api/cameras/<camera_id>/recording/stop")
def stop_recording(camera_id):
    with recordings_lock:
        recording = recordings.get(camera_id)
    if recording is None:
        return jsonify({"error": "camera is not recording"}), 409
    process = recording["process"]
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    stderr = process.stderr.read().strip() if process.stderr else ""
    temporary_path = store.uploads / recording["temporary_name"]
    final_path = store.uploads / recording["stored_name"]
    with recordings_lock:
        recordings.pop(camera_id, None)
    if process.returncode not in {0, 255} or not temporary_path.exists() or temporary_path.stat().st_size == 0:
        temporary_path.unlink(missing_ok=True)
        errors = stderr.splitlines()
        return jsonify({"error": errors[-1] if errors else "recording failed"}), 500
    os.replace(temporary_path, final_path)
    camera = recording["camera"]
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    display_name = secure_filename(f"{camera.get('name', camera['address'])}-{timestamp}.mp4")
    metadata = {
        "id": recording["asset_id"], "name": display_name, "stored_name": recording["stored_name"],
        "size": final_path.stat().st_size, "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {"type": "camera_recording", "camera_id": camera_id, "address": camera["address"]},
        "analysis": {"status": "not_started"},
    }
    store.save_upload_metadata(metadata)
    return jsonify({"active": False, "asset": metadata})


@app.get("/api/uploads")
def uploads():
    return jsonify(store.list_uploads())


@app.post("/api/uploads")
def upload():
    media = request.files.get("file")
    if media is None or not media.filename:
        return jsonify({"error": "file is required"}), 400
    original_name = secure_filename(media.filename)
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"unsupported file type: {extension}"}), 400
    asset_id = uuid.uuid4().hex
    stored_name = f"{asset_id}.{extension}"
    media.save(store.uploads / stored_name)
    metadata = {
        "id": asset_id, "name": original_name, "stored_name": stored_name,
        "size": (store.uploads / stored_name).stat().st_size,
        "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "analysis": {"status": "not_started"},
    }
    store.save_upload_metadata(metadata)
    return jsonify(metadata), 201


@app.get("/api/uploads/<asset_id>/content")
def upload_content(asset_id):
    metadata = store.get_upload(asset_id)
    if metadata is None:
        return jsonify({"error": "upload not found"}), 404
    return send_from_directory(store.uploads, metadata["stored_name"], as_attachment=False)


@app.delete("/api/uploads/<asset_id>")
def delete_upload(asset_id):
    result = store.delete_upload(asset_id)
    if result is None:
        return jsonify({"error": "upload not found"}), 404
    return jsonify(result)


def _create_browser_copy(asset_id: str) -> None:
    metadata = store.get_upload(asset_id)
    if metadata is None:
        return
    output_name = f"{asset_id}.browser.mp4"
    output_path = store.uploads / output_name
    source_path = store.uploads / metadata["stored_name"]
    metadata["browser_copy"] = {"status": "processing", "stored_name": output_name}
    store.save_upload_metadata(metadata)
    command = [
        "ffmpeg", "-v", "error", "-i", str(source_path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "zscale=t=linear:npl=100,tonemap=hable:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-y", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    metadata = store.get_upload(asset_id) or metadata
    if result.returncode == 0 and output_path.exists():
        metadata["browser_copy"] = {
            "status": "ready", "stored_name": output_name, "size": output_path.stat().st_size,
        }
    else:
        output_path.unlink(missing_ok=True)
        errors = result.stderr.strip().splitlines()
        metadata["browser_copy"] = {
            "status": "failed", "error": errors[-1] if errors else "FFmpeg conversion failed",
        }
    store.save_upload_metadata(metadata)


@app.post("/api/uploads/<asset_id>/browser-copy")
def create_browser_copy(asset_id):
    metadata = store.get_upload(asset_id)
    if metadata is None:
        return jsonify({"error": "upload not found"}), 404
    current = metadata.get("browser_copy", {})
    if current.get("status") in {"processing", "ready"}:
        return jsonify(current)
    metadata["browser_copy"] = {"status": "processing"}
    store.save_upload_metadata(metadata)
    threading.Thread(target=_create_browser_copy, args=(asset_id,), daemon=True).start()
    return jsonify(metadata["browser_copy"]), 202


@app.get("/api/uploads/<asset_id>/browser")
def browser_copy(asset_id):
    metadata = store.get_upload(asset_id)
    browser = metadata.get("browser_copy", {}) if metadata else {}
    if browser.get("status") != "ready":
        return jsonify({"error": "browser copy is not ready", "status": browser.get("status", "not_started")}), 404
    return send_from_directory(store.uploads, browser["stored_name"], as_attachment=False, mimetype="video/mp4")


@app.post("/api/uploads/<asset_id>/analyze")
def analyze(asset_id):
    if not any(item.get("id") == asset_id for item in store.list_uploads()):
        return jsonify({"error": "upload not found"}), 404
    return jsonify({
        "status": "not_configured",
        "message": "AI model is not configured. The upload and analysis API boundary is ready for an ONNX model.",
    }), 501


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
