import json
import tempfile
import unittest
from pathlib import Path

from app.cameras.camera_probe import build_rtsp_url
from app.cameras.camera_store import CameraStore


class CameraAppTests(unittest.TestCase):
    def test_build_rtsp_url_quotes_credentials(self):
        url = build_rtsp_url({
            "address": "10.42.0.109", "port": 554, "path": "/ch0",
            "username": "camera user", "password": "p@ss/word",
        })
        self.assertEqual(url, "rtsp://camera%20user:p%40ss%2Fword@10.42.0.109:554/ch0")

    def test_camera_store_does_not_return_password(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = CameraStore(temporary_dir)
            saved = store.save_camera({"address": "10.42.0.109", "password": "secret"})
            self.assertNotIn("password", saved)
            self.assertNotIn("password", store.list_cameras()[0])
            self.assertEqual(store.get_camera(saved["id"])["password"], "secret")
            registry = Path(temporary_dir) / "cameras.json"
            self.assertEqual(json.loads(registry.read_text())[0]["address"], "10.42.0.109")

    def test_camera_store_allows_multiple_paths_on_same_address(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = CameraStore(temporary_dir)
            first = store.save_camera({"address": "10.42.0.109", "port": 554, "path": "ch0"})
            second = store.save_camera({"address": "10.42.0.109", "port": 554, "path": "/ch1"})
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual([item["path"] for item in store.list_cameras()], ["ch0", "ch1"])

    def test_upload_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = CameraStore(temporary_dir)
            metadata = {"id": "asset-1", "stored_name": "asset-1.mov"}
            store.save_upload_metadata(metadata)
            self.assertEqual(store.get_upload("asset-1"), metadata)
            self.assertIsNone(store.get_upload("missing"))

    def test_delete_upload_removes_original_copy_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            store = CameraStore(temporary_dir)
            original = store.uploads / "asset-1.mov"
            browser = store.uploads / "asset-1.browser.mp4"
            original.write_bytes(b"original")
            browser.write_bytes(b"browser")
            store.save_upload_metadata({
                "id": "asset-1", "stored_name": original.name,
                "browser_copy": {"status": "ready", "stored_name": browser.name},
            })
            result = store.delete_upload("asset-1")
            self.assertEqual(set(result["deleted"]), {original.name, browser.name})
            self.assertFalse(original.exists())
            self.assertFalse(browser.exists())
            self.assertIsNone(store.get_upload("asset-1"))


if __name__ == "__main__":
    unittest.main()
