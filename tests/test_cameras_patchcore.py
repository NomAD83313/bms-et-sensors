"""Numerical model checks; run inside the camera-anomaly image."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import torch
    from app.cameras.anomaly_control import INPUT_SIZES, DEFAULT_INPUT_SIZE, input_dimensions
    from app.cameras.patchcore_lite import PatchCoreLite
except ImportError:
    torch = None


@unittest.skipIf(torch is None, 'Requires the optional camera-anomaly image')
class PatchMemoryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {
            'CAMERA_ANOMALY_FEATURE_ENGINE': 'torch',
            'CAMERA_ANOMALY_DISTANCE_ENGINE': 'torch',
            'CAMERA_ANOMALY_THREADS': '1'})
        environment.start()
        self.addCleanup(environment.stop)

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
        count = 96 * 54
        samples = [torch.full((count, 128), 1.0 if index % 5 == 0 else 0.0) for index in range(20)]
        scores = model.fit(samples)
        self.assertEqual(len(scores), 4)
        self.assertTrue(all(value > 1 for value in scores))
        self.assertGreater(model.threshold, 1)
        score, heatmap = model.score(torch.zeros(count, 128))
        self.assertEqual(score, 0)
        self.assertEqual(tuple(heatmap.shape), (54, 96))
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

    def test_input_sizes_produce_matching_patch_and_heatmap_grids(self):
        import io
        from PIL import Image
        source = io.BytesIO()
        Image.new('RGB', (960, 540), (128, 128, 128)).save(source, format='JPEG')
        model = PatchCoreLite()
        for size in INPUT_SIZES:
            with self.subTest(size=size):
                width, height = input_dimensions(size)
                model.set_input_size(size)
                patches = model.features(source.getvalue())
                self.assertEqual(tuple(patches.shape), ((width // 8) * (height // 8), 128))
                model.memory = patches[:1024].contiguous()
                model.threshold = 1
                _, heatmap = model.score(patches)
                self.assertEqual(tuple(heatmap.shape), (height // 8, width // 8))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'baseline.pt'
                    model.save(path)
                    restored = PatchCoreLite(input_size=size)
                    restored.load(path)
                    if size != DEFAULT_INPUT_SIZE:
                        with self.assertRaises(ValueError):
                            PatchCoreLite().load(path)
                model.set_input_size(DEFAULT_INPUT_SIZE)
                self.assertIsNone(model.memory)

    def test_square_baselines_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model = PatchCoreLite()
            for size in (None, 224, 320, 448, 512, 640):
                baseline = dict(memory=torch.zeros(1,128),threshold=1,baseline_frames=60,version=1)
                if size is not None: baseline['input_size']=size
                torch.save(baseline,path)
                with self.subTest(size=size), self.assertRaises(ValueError): model.load(path)
            self.assertEqual(model.input_size,DEFAULT_INPUT_SIZE)

    def test_previous_encoder_baseline_is_rejected(self):
        from app.cameras.feature_encoder import ENCODER_ID
        model = PatchCoreLite()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            baseline = dict(memory=torch.zeros(1, 128), threshold=1,
                            baseline_frames=60, input_size=model.input_size)
            for metadata in (dict(version=2), dict(version=3, encoder='old-encoder'),
                             dict(version=3, encoder=ENCODER_ID)):
                saved = dict(baseline, **metadata)
                if metadata.get('encoder') == ENCODER_ID:
                    saved['memory'] = torch.zeros(1, 384)
                torch.save(saved, path)
                with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                    model.load(path)
            self.assertIsNone(model.memory)

    def test_faiss_preserves_euclidean_scores_and_rebuilds_index(self):
        try:
            import faiss
        except ImportError:
            self.skipTest('Requires faiss-cpu')
        generator = torch.Generator().manual_seed(12)
        patches = torch.randn(96 * 54, 128, generator=generator)
        reference = PatchCoreLite()
        fast = PatchCoreLite(distance_engine='faiss')
        for offset in (0, 3):
            reference.memory = torch.randn(64, 128, generator=generator) + offset
            fast.memory = reference.memory
            expected_score, expected_heatmap = reference.score(patches)
            actual_score, actual_heatmap = fast.score(patches)
            torch.testing.assert_close(actual_heatmap, expected_heatmap, rtol=1e-5, atol=1e-5)
            self.assertAlmostEqual(actual_score, expected_score, places=4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            reference.threshold, reference.baseline_frames = 1, 60
            reference.save(path)
            fast.load(path)
            actual_score, actual_heatmap = fast.score(patches)
            torch.testing.assert_close(actual_heatmap, expected_heatmap, rtol=1e-5, atol=1e-5)
            self.assertAlmostEqual(actual_score, expected_score, places=4)

    def test_onnx_profiles_preserve_features_and_scores(self):
        import io
        from PIL import Image
        try:
            import onnxruntime
        except ImportError:
            self.skipTest('Requires ONNX Runtime and exported encoders')
        source = io.BytesIO()
        Image.effect_noise((960, 540), 60).convert('RGB').save(source, format='JPEG')
        reference = PatchCoreLite()
        fast = PatchCoreLite(feature_engine='onnx', distance_engine='faiss')
        self.assertIn(fast.onnx_provider, onnxruntime.get_available_providers())
        for size in INPUT_SIZES:
            with self.subTest(size=size):
                reference.set_input_size(size)
                fast.set_input_size(size)
                expected = reference.features(source.getvalue())
                actual = fast.features(source.getvalue())
                torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-4)
                reference.memory = fast.memory = expected[::20].contiguous() + .1
                expected_score, expected_heatmap = reference.score(expected)
                actual_score, actual_heatmap = fast.score(actual)
                torch.testing.assert_close(actual_heatmap, expected_heatmap, rtol=1e-4, atol=1e-4)
                self.assertAlmostEqual(actual_score, expected_score, places=3)

    def test_chunked_distances_match_full_matrix(self):
        model = PatchCoreLite()
        generator = torch.Generator().manual_seed(10)
        patches = torch.randn(96*54,128,generator=generator)
        model.memory = torch.randn(64,128,generator=generator)
        reference = torch.cdist(patches,model.memory).min(dim=1).values
        from unittest.mock import patch
        original = torch.cdist
        sizes = []
        def distance(first, second):
            sizes.append(len(first))
            return original(first,second)
        with patch('app.cameras.patchcore_lite.torch.cdist',side_effect=distance):
            score,heatmap = model.score(patches)
        self.assertLessEqual(max(sizes),1024)
        torch.testing.assert_close(heatmap.flatten(),reference)
        self.assertAlmostEqual(score,float(reference.max()),places=5)

    def test_thread_settings_preserve_existing_baseline_scores(self):
        import io
        from PIL import Image
        source = io.BytesIO()
        Image.effect_noise((960, 540), 60).convert('RGB').save(source, format='JPEG')
        model = PatchCoreLite(input_size='768x432', threads=1)
        features = model.features(source.getvalue())
        model.memory = features[::4].contiguous() + .1
        model.threshold = 1
        score, heatmap = model.score(features)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model.save(path)
            parallel = PatchCoreLite(input_size='768x432', threads=2)
            parallel.load(path)
            patches = parallel.features(source.getvalue())
            new_score, new_heatmap = parallel.score(patches)
            torch.testing.assert_close(patches, features, rtol=1e-4, atol=1e-4)
            torch.testing.assert_close(new_heatmap, heatmap, rtol=1e-4, atol=1e-4)
            self.assertAlmostEqual(new_score, score, places=3)
        for count in (0, 5, 1.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                PatchCoreLite(threads=count)

    def test_insufficient_normal_frames_are_rejected(self):
        model = PatchCoreLite()
        with self.assertRaises(ValueError):
            model.fit([torch.zeros(784, 128)])


if __name__ == '__main__':
    unittest.main()
