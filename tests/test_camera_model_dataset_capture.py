"""Test dependency-free validation used by the camera model dataset capture."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/capture-camera-model-dataset.py'
SPEC = importlib.util.spec_from_file_location('capture_camera_model_dataset', SCRIPT)
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class CameraModelDatasetCaptureTests(unittest.TestCase):
    def response(self, token='generation-0'):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'\xff\xd8frame\xff\xd9'
        response.headers = {'X-Camera-Analysis-Source': 'original-v1', 'X-Camera-Frame-Token': token}
        return response

    def test_capture_uses_current_token_and_checks_decoder_validity(self):
        with patch.object(CAPTURE, 'capture_settings', return_value={'roi': None}), \
                patch.object(CAPTURE, 'urlopen', return_value=self.response()), \
                patch.object(CAPTURE, 'request_json', return_value={'valid': True}) as check:
            _, token = CAPTURE.capture_frame('http://camera', {'roi': None})
        self.assertEqual(token, 'generation-0')
        check.assert_called_once_with('http://camera/api/live.jpg?check=generation-0')

    def test_capture_rejects_roi_changes_and_invalidated_frames(self):
        for settings, valid in [([{'roi': None}, {'roi': 'changed'}], True),
                                ([{'roi': None}], False)]:
            with self.subTest(settings=settings), \
                    patch.object(CAPTURE, 'capture_settings', side_effect=settings), \
                    patch.object(CAPTURE, 'urlopen', return_value=self.response()), \
                    patch.object(CAPTURE, 'request_json', return_value={'valid': valid}), \
                    self.assertRaises(ValueError):
                CAPTURE.capture_frame('http://camera', {'roi': None})

    def test_missing_generation_header_is_not_silently_accepted(self):
        with patch.object(CAPTURE, 'capture_settings', return_value={}), \
                patch.object(CAPTURE, 'urlopen', return_value=self.response(None)), \
                self.assertRaisesRegex(ValueError, 'guarded'):
            CAPTURE.capture_frame('http://camera', {})

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
