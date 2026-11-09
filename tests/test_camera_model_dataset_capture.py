"""Test dependency-free validation used by the camera model dataset capture."""
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/capture-camera-model-dataset.py'
SPEC = importlib.util.spec_from_file_location('capture_camera_model_dataset', SCRIPT)
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class CameraModelDatasetCaptureTests(unittest.TestCase):
    def test_jpeg_size_reads_baseline_sof(self):
        data = (b'\xff\xd8' + b'\xff\xe0\x00\x04xx' +
                b'\xff\xc0\x00\x0b\x08\x05\x10\x09\x00xxx' + b'\xff\xd9')
        self.assertEqual(CAPTURE.jpeg_size(data), (2304, 1296))

    def test_jpeg_size_rejects_non_jpeg_and_incomplete_segments(self):
        with self.assertRaisesRegex(ValueError, 'not a JPEG'):
            CAPTURE.jpeg_size(b'not jpeg')
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            CAPTURE.jpeg_size(b'\xff\xd8\xff\xe0\x00\x10x')


if __name__ == '__main__':
    unittest.main()
