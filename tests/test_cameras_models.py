"""Model selection validation and layout rules without inference dependencies."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app.cameras import anomaly_control
from app.cameras.model_profiles import MODELS, validate_model, heatmap_dimensions


class ModelSelectionTests(unittest.TestCase):
    def test_commands_accept_only_named_models_and_stop_monitoring(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            for key in MODELS:
                command = anomaly_control.request('model', model_key=key)
                self.assertEqual(command['model_key'], key)
                self.assertEqual(anomaly_control.command_mode('model', True, False, True), (False, False))
            for invalid in (None, '', 'mobilenet', [], '../model'):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    validate_model(invalid)

    def test_mobile_heatmap_rounds_up_non_divisible_height(self):
        self.assertEqual(heatmap_dimensions(1920, 1080, 'mobilenetv3'), (120, 68))
        self.assertEqual(heatmap_dimensions(1920, 1080, 'mobilenetv4-convsmall-x0.5'), (120, 68))

    def test_encoders_have_independent_identities_and_layouts(self):
        self.assertEqual(len({profile['encoder'] for profile in MODELS.values()}), len(MODELS))
        self.assertEqual(heatmap_dimensions(1920, 1080, 'mobileone-s0'), (240, 135))

    def test_small_backbone_commands_and_all_supported_heatmap_layouts(self):
        from app.cameras.anomaly_control import INPUT_SIZES, input_dimensions
        with tempfile.TemporaryDirectory() as directory, patch.object(anomaly_control, 'DATA', Path(directory)):
            for key, channels in (('mobilenetv4-convsmall-x0.5', 48),):
                with self.subTest(model=key):
                    self.assertEqual(anomaly_control.request('model', model_key=key)['model_key'], key)
                    self.assertEqual(MODELS[key]['dimensions'], channels)
                    self.assertEqual(MODELS[key]['stride'], 16)
                    for size in INPUT_SIZES:
                        width, height = input_dimensions(size)
                        self.assertEqual(heatmap_dimensions(width, height, key),
                                         ((width + 15) // 16, (height + 15) // 16))
