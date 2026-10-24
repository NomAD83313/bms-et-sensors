import json
import os
import threading
import uuid
from pathlib import Path


class CameraStore:
    def __init__(self, root: str):
        self.root = Path(root)
        self.uploads = self.root / "uploads"
        self.registry_path = self.root / "cameras.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.registry_path.exists():
            return []
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def _write(self, cameras: list[dict]) -> None:
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cameras, indent=2), encoding="utf-8")
        os.replace(temporary, self.registry_path)

    def list_cameras(self, include_credentials: bool = False) -> list[dict]:
        with self._lock:
            cameras = self._read()
        if include_credentials:
            return cameras
        return [{key: value for key, value in camera.items() if key != "password"} for camera in cameras]

    def get_camera(self, camera_id: str) -> dict | None:
        for camera in self.list_cameras(include_credentials=True):
            if camera.get("id") == camera_id:
                return camera
        return None

    def save_camera(self, data: dict) -> dict:
        address = str(data["address"]).strip()
        port = int(data.get("port", 554))
        path = str(data.get("path", "ch0")).strip().lstrip("/")
        with self._lock:
            cameras = self._read()
            existing = next((
                item for item in cameras
                if item.get("address") == address
                and int(item.get("port", 554)) == port
                and str(item.get("path", "ch0")).strip().lstrip("/") == path
            ), None)
            camera = existing or {"id": uuid.uuid4().hex}
            camera.update(data)
            camera["address"] = address
            camera["port"] = port
            camera["path"] = path
            if existing is None:
                cameras.append(camera)
            self._write(cameras)
        return {key: value for key, value in camera.items() if key != "password"}

    def list_uploads(self) -> list[dict]:
        assets = []
        for metadata_path in self.uploads.glob("*.json"):
            try:
                assets.append(json.loads(metadata_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(assets, key=lambda item: item.get("uploaded_at", ""), reverse=True)

    def save_upload_metadata(self, metadata: dict) -> None:
        path = self.uploads / f"{metadata['id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def get_upload(self, asset_id: str) -> dict | None:
        path = self.uploads / f"{asset_id}.json"
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return metadata if isinstance(metadata, dict) else None

    def delete_upload(self, asset_id: str) -> dict | None:
        with self._lock:
            metadata = self.get_upload(asset_id)
            if metadata is None:
                return None
            filenames = {metadata.get("stored_name")}
            filenames.add(metadata.get("browser_copy", {}).get("stored_name"))
            deleted = []
            for filename in filenames:
                if not filename or Path(filename).name != filename:
                    continue
                path = self.uploads / filename
                if path.exists():
                    path.unlink()
                    deleted.append(filename)
            metadata_path = self.uploads / f"{asset_id}.json"
            metadata_path.unlink(missing_ok=True)
        return {"id": asset_id, "deleted": deleted}
