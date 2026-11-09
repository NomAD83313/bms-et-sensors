"""ROI validation, source geometry and file-based controls without inference."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app.cameras import anomaly_control
from app.cameras.roi import validate_roi, roi_bounds, roi_input_dimensions


class RoiGeometryTests(unittest.TestCase):
    def test_full_frame_is_canonical_and_valid_rectangles_are_copied(self):
        self.assertIsNone(validate_roi(None))
        self.assertIsNone(validate_roi(dict(x=0, y=0, width=1, height=1)))
        rectangle = dict(x=.125, y=.25, width=.5, height=.5)
        normalized = validate_roi(rectangle)
        self.assertEqual(normalized, rectangle)
        self.assertIsNot(normalized, rectangle)
        self.assertEqual(validate_roi(dict(x=0, y=0, width=.01, height=.01)),
                         dict(x=0, y=0, width=.01, height=.01))

    def test_invalid_rectangles_are_rejected_before_geometry_is_used(self):
        valid = dict(x=.1, y=.2, width=.5, height=.4)
        invalid = [True, '', [], .5, {}, dict(x=0, y=0, width=.5),
                   dict(valid, x=-.001), dict(valid, y=-.001),
                   dict(valid, width=.009), dict(valid, height=.009),
                   dict(valid, x=.6), dict(valid, y=.7)]
        for name in valid:
            for value in (True, False, None, '0.5', float('nan'), float('inf'), float('-inf')):
                invalid.append(dict(valid, **{name: value}))
        for rectangle in invalid:
            with self.subTest(roi=rectangle), self.assertRaises(ValueError):
                validate_roi(rectangle)

    def test_pixel_bounds_cover_fractional_edges_and_full_frame(self):
        self.assertEqual(roi_bounds(2000, 1000, None), (0, 0, 2000, 1000))
        self.assertEqual(roi_bounds(2000, 1000, dict(x=.1, y=.2, width=.5, height=.4)),
                         (200, 200, 1200, 600))
        self.assertEqual(roi_bounds(101, 51, dict(x=.1, y=.2, width=.5, height=.4)),
                         (10, 10, 61, 31))

    def test_full_frame_preserves_existing_presets_even_for_a_four_three_source(self):
        for preset in anomaly_control.INPUT_SIZES:
            with self.subTest(preset=preset):
                self.assertEqual(roi_input_dimensions(preset, 3280, 2464, None),
                                 anomaly_control.input_dimensions(preset))

    def test_roi_uses_its_own_aspect_ratio_inside_the_selected_input_bounds(self):
        square = dict(x=.1, y=.1, width=.25, height=.5)
        for preset in anomaly_control.INPUT_SIZES:
            with self.subTest(preset=preset):
                _, maximum_height = anomaly_control.input_dimensions(preset)
                self.assertEqual(roi_input_dimensions(preset, 2000, 1000, square),
                                 (maximum_height, maximum_height))
        self.assertEqual(roi_input_dimensions('768x432', 2000, 1000,
                                             dict(x=.1, y=.1, width=.5, height=.5)), (768, 384))
        self.assertEqual(roi_input_dimensions('768x432', 2000, 1000,
                                             dict(x=.1, y=.1, width=.2, height=.5)), (346, 432))

    def test_extreme_roi_aspects_cannot_create_a_model_side_smaller_than_thirty_two(self):
        for rectangle in (dict(x=0, y=0, width=.01, height=1),
                          dict(x=0, y=0, width=1, height=.01)):
            with self.subTest(roi=rectangle), self.assertRaises(ValueError):
                roi_input_dimensions('768x432', 2000, 1000, rectangle)


class RoiControlTests(unittest.TestCase):
    def test_roi_command_persists_canonical_rectangle_and_pauses_any_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            rectangle = dict(x=.1, y=.2, width=.5, height=.4)
            command = anomaly_control.request('roi', roi=rectangle)
            self.assertEqual(command['roi'], rectangle)
            self.assertEqual(anomaly_control.read_json(Path(directory) / 'command.json'), command)
            for ready in (False, True):
                for learning, monitoring in ((True, False), (False, True), (False, False)):
                    self.assertEqual(anomaly_control.command_mode('roi', ready, learning, monitoring),
                                     (False, False))
            self.assertIsNone(anomaly_control.request('roi', roi=None)['roi'])
            self.assertIsNone(anomaly_control.request('roi', roi=dict(x=0, y=0, width=1, height=1))['roi'])

    def test_invalid_roi_does_not_replace_the_last_command(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            path = Path(directory) / 'command.json'
            anomaly_control.write_json(path, dict(id='previous-request', action='pause'))
            previous = path.read_bytes()
            for rectangle in ({}, dict(x=.8, y=.2, width=.5, height=.4),
                              dict(x=False, y=.2, width=.5, height=.4)):
                with self.subTest(roi=rectangle), self.assertRaises(ValueError):
                    anomaly_control.request('roi', roi=rectangle)
                self.assertEqual(path.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
