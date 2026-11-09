import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.shared.redlab_channels import load_redlab_channels, save_redlab_channels
from app.graf.graf_redlab_state import load_redlab_channels as graf_load, save_redlab_channels as graf_save


class RedlabChannelsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "channels.json"
        self.keys = ["ch0", "ch1"]

    def test_legacy_boolean_and_structured_metadata_round_trip(self):
        saved = graf_save(self.path, self.keys, {
            "ch0": False, "dev|ch1": {"enabled": False, "name": " Sensor A "},
            "ch1": {"enabled": True, "name": "   "}, "dev|ch9": False, "|ch0": False,
        })
        self.assertEqual(saved, {"ch0": False, "ch1": True, "dev|ch1": {"enabled": False, "name": "Sensor A"}})
        self.assertEqual(graf_load(self.path, self.keys), saved)
        self.assertEqual(load_redlab_channels(self.path, self.keys), saved)

    def test_loading_existing_empty_name_object_preserves_structure(self):
        self.path.write_text(json.dumps({"ch0": {"enabled": False}}))
        self.assertEqual(load_redlab_channels(self.path, self.keys)["ch0"], {"enabled": False, "name": ""})

    def test_missing_invalid_and_non_object_json_use_defaults(self):
        self.assertEqual(load_redlab_channels(self.path, self.keys), {"ch0": True, "ch1": True})
        for content in ("broken", "[]", "null"):
            self.path.write_text(content)
            self.assertEqual(load_redlab_channels(self.path, self.keys), {"ch0": True, "ch1": True})

    def test_replace_failure_keeps_original_file_and_removes_temporary_file(self):
        self.path.write_text('{"ch0": false}')
        with patch("app.shared.redlab_channels.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                save_redlab_channels(self.path, self.keys, {"ch0": True})
        self.assertEqual(self.path.read_text(), '{"ch0": false}')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_is_atomic_and_preserves_existing_permissions(self):
        self.path.write_text('{"ch0": false}')
        self.path.chmod(0o640)
        replace = os.replace
        observed = []
        def check_replace(source, destination):
            observed.append(json.loads(self.path.read_text()))
            self.assertEqual(Path(source).parent, self.path.parent)
            replace(source, destination)
        with patch("app.shared.redlab_channels.os.replace", side_effect=check_replace):
            save_redlab_channels(self.path, self.keys, {"ch0": True})
        self.assertEqual(observed, [{"ch0": False}])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)


if __name__ == "__main__":
    unittest.main()
