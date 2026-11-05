"""Numerical model checks; run inside the camera-anomaly image."""
import tempfile
import unittest
from pathlib import Path

try:
    import torch
    from app.cameras.patchcore_lite import PatchCoreLite
except ImportError:
    torch = None


@unittest.skipIf(torch is None, 'Requires the optional camera-anomaly image')
class PatchMemoryTests(unittest.TestCase):
    def test_channels_last_preserves_features_and_existing_baseline_scores(self):
        import io
        from PIL import Image
        model = PatchCoreLite()
        source = io.BytesIO()
        Image.effect_noise((960, 540), 60).convert('RGB').save(source, format='JPEG')
        jpeg = source.getvalue()
        model.network.to(memory_format=torch.contiguous_format)
        reference = model.features(jpeg)
        model.memory = reference[::2].contiguous() + .1
        model.threshold = 1.0
        model.baseline_frames = 60
        score, heatmap = model.score(reference)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model.save(path)
            optimized = PatchCoreLite()
            optimized.load(path)
            features = optimized.features(jpeg)
            new_score, new_heatmap = optimized.score(features)
        torch.testing.assert_close(features, reference, rtol=1e-5, atol=1e-5)
        torch.testing.assert_close(new_heatmap, heatmap, rtol=1e-4, atol=1e-4)
        self.assertAlmostEqual(new_score, score, places=4)

    def test_calibration_holds_out_entire_frames_and_model_round_trip(self):
        model = PatchCoreLite()
        # Held-out frames contain distinct features, so leakage would yield zero.
        samples = [torch.full((784, 128), 1.0 if index % 5 == 0 else 0.0) for index in range(20)]
        scores = model.fit(samples)
        self.assertEqual(len(scores), 4)
        self.assertTrue(all(value > 1 for value in scores))
        self.assertGreater(model.threshold, 1)
        score, heatmap = model.score(torch.zeros(784, 128))
        self.assertEqual(score, 0)
        self.assertEqual(tuple(heatmap.shape), (28, 28))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model.save(path)
            restored = PatchCoreLite()
            restored.load(path)
            self.assertEqual(restored.baseline_frames, 20)
            self.assertEqual(restored.threshold, model.threshold)
            self.assertTrue(torch.equal(restored.memory, model.memory))

    def test_heatmap_highlights_only_novel_regions(self):
        import io
        from PIL import Image
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (224, 224), (128, 128, 128)).save(source, format='JPEG')
        distances = torch.zeros(28, 28)
        distances[7:14, 7:14] = 2
        result = Image.open(io.BytesIO(overlay(source.getvalue(), distances, 1)))
        normal = result.getpixel((180, 180))
        unusual = result.getpixel((80, 80))
        self.assertLess(abs(normal[0] - normal[1]), 5)
        self.assertGreater(unusual[0] - unusual[1], 80)

    def test_insufficient_normal_frames_are_rejected(self):
        model = PatchCoreLite()
        with self.assertRaises(ValueError):
            model.fit([torch.zeros(784, 128)])


if __name__ == '__main__':
    unittest.main()
