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
    @staticmethod
    def _roi_jpeg(change_outside=False, size=(320, 240)):
        import io
        from PIL import Image, ImageDraw
        image = Image.new('RGB', size, (50, 60, 70))
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((80, 60, 239, 179), fill=(80, 110, 140))
        drawing.rectangle((110, 90, 140, 120), fill=(190, 70, 40))
        if change_outside:
            # Keep modified JPEG blocks well away from the crop boundary.
            drawing.rectangle((0, 0, 31, size[1] - 1), fill=(220, 20, 200))
        stream = io.BytesIO()
        image.save(stream, format='JPEG', quality=95)
        return stream.getvalue()

    def test_roi_crop_ignores_outside_pixels_and_preserves_its_aspect_ratio(self):
        model = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx')
        model.set_roi(dict(x=.25, y=.25, width=.5, height=.5))
        first = model._image(self._roi_jpeg())
        second = model._image(self._roi_jpeg(change_outside=True))
        torch.testing.assert_close(first, second, rtol=0, atol=0)
        self.assertEqual(model.source_size, (320, 240))
        self.assertEqual(model.effective_input_size, (160, 120))
        self.assertEqual(tuple(first.shape), (1, 3, 120, 160))
        model.set_roi(None)
        self.assertEqual(tuple(model._image(self._roi_jpeg()).shape), (1, 3, 432, 768))

    def test_jpeg_draft_preserves_roi_shape_records_metrics_and_separates_baselines(self):
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        jpeg = self._roi_jpeg(size=(3280, 2464))
        full = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx', jpeg_draft=False)
        draft = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx', jpeg_draft=True)
        for model in (full, draft):
            model.set_roi(roi)
        full_tensor, draft_tensor = full._image(jpeg), draft._image(jpeg)
        self.assertEqual(tuple(full_tensor.shape), tuple(draft_tensor.shape))
        self.assertEqual(draft.effective_input_size, (575, 432))
        self.assertEqual((full.preparation_metrics['decoded_width'],
                          full.preparation_metrics['decoded_height']), (3280, 2464))
        self.assertLess(draft.preparation_metrics['decoded_width'], 3280)
        self.assertLess(draft.preparation_metrics['decoded_height'], 2464)
        self.assertLess(float((full_tensor - draft_tensor).abs().mean()), .03)
        for key in ('geometry_ms', 'decode_ms', 'transform_ms', 'tensor_ms', 'total_ms'):
            self.assertGreaterEqual(draft.preparation_metrics[key], 0)
        draft.memory = torch.zeros(1, draft.feature_dimensions)
        draft.threshold, draft.baseline_frames = 1, 20
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            draft.save(path)
            saved = torch.load(path, weights_only=True)
            self.assertTrue(saved['jpeg_draft'])
            restored = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx', jpeg_draft=True)
            restored.set_roi(roi)
            restored.load(path)
            with self.assertRaises(ValueError):
                full.load(path)
        for invalid in (-1, 2, 'yes'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(jpeg_draft=invalid)

    def test_top_k_averages_the_largest_patch_scores_and_separates_baselines(self):
        patches = torch.zeros(96 * 54, 128)
        patches[:-1, 0] = 1
        patches[-1, 0] = 20
        maximum = PatchCoreLite(top_k=1)
        maximum.memory = torch.zeros(1, 128)
        averaged = PatchCoreLite(top_k=5)
        averaged.memory = maximum.memory
        self.assertAlmostEqual(maximum.score(patches)[0], 20, places=5)
        self.assertAlmostEqual(averaged.score(patches)[0], 4.8, places=5)
        averaged.threshold, averaged.baseline_frames = 1, 20
        averaged.inspect_geometry(self._roi_jpeg())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            averaged.save(path)
            saved = torch.load(path, weights_only=True)
            self.assertEqual(saved['score_top_k'], 5)
            PatchCoreLite(top_k=5).load(path)
            with self.assertRaises(ValueError):
                maximum.load(path)
        for invalid in (0, 21, 1.5, True, 'invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(top_k=invalid)

    def test_configurable_coreset_size_is_bounded_and_persisted(self):
        model = PatchCoreLite(coreset_size=256)
        count = 96 * 54
        samples = [torch.full((count, 128), index * .01) for index in range(20)]
        model.fit(samples)
        model.inspect_geometry(self._roi_jpeg())
        self.assertEqual(len(model.memory), 256)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model.save(path)
            saved = torch.load(path, weights_only=True)
            self.assertEqual(saved['coreset_size'], 256)
            restored = PatchCoreLite()
            restored.load(path)
            self.assertEqual(len(restored.memory), 256)
        for invalid in (0, 255, 4097, 256.5, True, 'invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(coreset_size=invalid)

    def test_roi_dynamic_onnx_encoders_and_heatmaps_match_actual_input_geometry(self):
        from app.cameras.model_profiles import MODELS, heatmap_dimensions
        roi = dict(x=.25, y=.1, width=.5, height=.7)
        jpeg = self._roi_jpeg()
        for key, profile in MODELS.items():
            with self.subTest(model=key):
                reference = PatchCoreLite(model_key=key, feature_engine='torch')
                fast = PatchCoreLite(model_key=key, feature_engine='onnx', distance_engine='faiss')
                reference.set_roi(roi)
                fast.set_roi(roi)
                expected, actual = reference.features(jpeg), fast.features(jpeg)
                self.assertEqual(fast.effective_input_size, (160, 168))
                heat_width, heat_height = heatmap_dimensions(*fast.effective_input_size, key)
                self.assertEqual(tuple(actual.shape), (heat_width * heat_height, profile['dimensions']))
                self.assertEqual(actual.shape, expected.shape)
                difference = actual - expected
                self.assertLessEqual(float(torch.linalg.vector_norm(difference) /
                                           torch.linalg.vector_norm(expected).clamp_min(1e-12)), 1e-4)
                self.assertLessEqual(float(difference.abs().max() / expected.abs().max().clamp_min(1e-12)), 1e-4)
                fast.memory = actual[:64].contiguous()
                self.assertEqual(tuple(fast.score(actual)[1].shape), (heat_height, heat_width))

    def test_roi_baseline_preserves_geometry_and_rejects_other_regions_or_source_sizes(self):
        from app.cameras.roi import ROIGeometryChanged
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        model = PatchCoreLite(model_key='mobilenetv3', feature_engine='torch', distance_engine='faiss')
        model.set_roi(roi)
        patches = model.features(self._roi_jpeg())
        model.fit([patches + index * .01 for index in range(20)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            model.save(path)
            artifact = torch.load(path, weights_only=True)
            self.assertEqual(artifact['version'], 6)
            self.assertEqual(artifact['roi'], roi)
            self.assertEqual(artifact['roi_preprocessing'], 'roi-original-aspect-v1')
            self.assertEqual(tuple(artifact['source_size']), (320, 240))
            self.assertEqual(tuple(artifact['effective_input_size']), (160, 120))
            restored = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx')
            restored.set_roi(roi)
            restored.load(path)
            self.assertEqual(restored.source_size, model.source_size)
            self.assertEqual(restored.effective_input_size, model.effective_input_size)
            self.assertEqual(restored.baseline_frames, 20)
            self.assertTrue(torch.equal(restored.memory, model.memory))
            self.assertEqual(restored.threshold, model.threshold)
            restored_memory = restored.memory
            restored.set_roi(dict(roi))
            self.assertIs(restored.memory, restored_memory)
            before = path.read_bytes()
            for changed in ({'roi_preprocessing': 'old-roi-preprocessing'},
                            {'effective_input_size': (576, 432)},
                            {'effective_input_size': (768, 432)}, {'source_size': (0, 240)}):
                torch.save(dict(artifact, **changed), path)
                corrupted = path.read_bytes()
                incompatible = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx')
                incompatible.set_roi(roi)
                with self.subTest(metadata=changed), self.assertRaises(ValueError):
                    incompatible.load(path)
                self.assertEqual(path.read_bytes(), corrupted)
            path.write_bytes(before)
            for different_roi in (None, dict(roi, x=.125)):
                incompatible = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx')
                incompatible.set_roi(different_roi)
                with self.subTest(roi=different_roi), self.assertRaises(ValueError):
                    incompatible.load(path)
                self.assertEqual(path.read_bytes(), before)
            restored._image(self._roi_jpeg())
            with self.assertRaises(ROIGeometryChanged):
                restored._image(self._roi_jpeg(size=(640, 480)))
            self.assertIsNone(restored.memory)
            self.assertIsNone(restored.threshold)
            self.assertEqual(restored.baseline_frames, 0)

    def test_roi_heatmap_stays_inside_selected_area_of_full_source(self):
        import io
        from PIL import Image
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (640, 480), (128, 128, 128)).save(source, format='JPEG')
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        rendered = Image.open(io.BytesIO(overlay(source.getvalue(), torch.full((27, 36), 2.), 1, roi=roi)))
        self.assertEqual(rendered.size, (640, 480))
        for point in ((50, 240), (590, 240), (320, 50), (320, 430)):
            pixel = rendered.getpixel(point)
            self.assertLess(max(pixel) - min(pixel), 5)
            self.assertLess(abs(pixel[0] - 128), 5)
        inside = rendered.getpixel((320, 240))
        self.assertGreater(inside[0] - inside[1], 80)


    @staticmethod
    def _small_backbone_jpegs():
        import io
        from PIL import Image
        images = []
        for index in range(3):
            image = Image.new('RGB', (96, 54))
            image.putdata([((x * 5 + index * 31) % 256,
                            (y * 7 + index * 23) % 256,
                            ((x + y) * 3 + index * 47) % 256)
                           for y in range(54) for x in range(96)])
            stream = io.BytesIO()
            image.save(stream, format='JPEG', quality=90)
            images.append(stream.getvalue())
        return images

    def test_small_backbone_normalization_uses_pretrained_rgb_statistics(self):
        import io
        from PIL import Image
        stream = io.BytesIO()
        Image.new('RGB', (96, 54), (25, 100, 200)).save(stream, format='JPEG', quality=95)
        jpeg = stream.getvalue()
        pixel = Image.open(io.BytesIO(jpeg)).getpixel((0, 0))
        imagenet = ((.485, .456, .406), (.229, .224, .225))
        for key, (mean, deviation) in (
                ('mobilenetv3', imagenet), ('pp-lcnet-x0.5', imagenet),
                ('mobilenetv4-convsmall-x0.5', ((.5, .5, .5), (.5, .5, .5)))):
            with self.subTest(model=key):
                model = PatchCoreLite(model_key=key, feature_engine='onnx')
                normalized = model._image(jpeg)
                expected = torch.tensor([(channel / 255. - center) / scale
                                         for channel, center, scale in zip(pixel, mean, deviation)])
                self.assertEqual(tuple(normalized.shape), (1, 3, 432, 768))
                torch.testing.assert_close(normalized[0], expected[:, None, None].expand_as(normalized[0]),
                                           rtol=1e-6, atol=1e-6)

    def test_small_backbones_match_onnx_on_three_nonconstant_jpegs(self):
        from app.cameras.feature_encoder import FeatureEncoder
        for key in ('pp-lcnet-x0.5', 'mobilenetv4-convsmall-x0.5'):
            model = PatchCoreLite(model_key=key, feature_engine='onnx')
            network = FeatureEncoder(key).eval()
            for index, jpeg in enumerate(self._small_backbone_jpegs()):
                with self.subTest(model=key, frame=index), torch.inference_mode():
                    expected = network(model._image(jpeg))
                    actual = model.features(jpeg)
                    self.assertEqual(actual.shape, expected.shape)
                    difference = actual - expected
                    relative_l2 = float(torch.linalg.vector_norm(difference) /
                                        torch.linalg.vector_norm(expected).clamp_min(1e-12))
                    normalized_max = float(difference.abs().max() / expected.abs().max().clamp_min(1e-12))
                    self.assertLessEqual(relative_l2, 1e-4)
                    self.assertLessEqual(normalized_max, 1e-4)

    def test_small_backbones_cover_all_input_sizes_and_keep_learning_independent(self):
        from app.cameras.model_profiles import MODELS, heatmap_dimensions
        jpeg = self._small_backbone_jpegs()[0]
        keys = ('pp-lcnet-x0.5', 'mobilenetv4-convsmall-x0.5')
        for key in keys:
            with self.subTest(model=key):
                model = PatchCoreLite(model_key=key, feature_engine='onnx', distance_engine='faiss')
                for size in INPUT_SIZES:
                    model.set_input_size(size)
                    patches = model.features(jpeg)
                    width, height = heatmap_dimensions(*input_dimensions(size), key)
                    self.assertEqual(tuple(patches.shape), (width * height, MODELS[key]['dimensions']))
                    model.memory = patches[:128].contiguous()
                    model.threshold, model.baseline_frames = 1., 20
                    self.assertEqual(tuple(model.score(patches)[1].shape), (height, width))
                model.set_input_size(DEFAULT_INPUT_SIZE)
                patches = model.features(jpeg)
                scores = model.fit([patches + index * .01 for index in range(20)])
                self.assertEqual(len(scores), 4)
                self.assertEqual(model.baseline_frames, 20)
                self.assertGreater(model.threshold, 0)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'baseline.pt'
                    model.save(path)
                    saved = path.read_bytes()
                    restored = PatchCoreLite(model_key=key, feature_engine='onnx', distance_engine='faiss')
                    restored.load(path)
                    self.assertEqual(restored.baseline_frames, 20)
                    self.assertEqual(restored.threshold, model.threshold)
                    self.assertTrue(torch.equal(restored.memory, model.memory))
                    self.assertAlmostEqual(restored.score(patches)[0], model.score(patches)[0], places=6)
                    for other in ('mobilenetv3',) + tuple(value for value in keys if value != key):
                        with self.subTest(rejected_model=other), self.assertRaises(ValueError):
                            PatchCoreLite(model_key=other, feature_engine='onnx').load(path)
                        self.assertEqual(path.read_bytes(), saved)
                    restored.set_model(next(value for value in keys if value != key))
                    self.assertIsNone(restored.memory)
                    self.assertIsNone(restored.threshold)
                    self.assertEqual(restored.baseline_frames, 0)

    def test_mobileone_matches_onnx_and_keeps_its_baseline_separate(self):
        import io
        from PIL import Image
        from app.cameras.feature_encoder import FeatureEncoder
        from app.cameras.model_profiles import MODELS, heatmap_dimensions
        jpeg = io.BytesIO()
        Image.effect_noise((96, 54), 30).convert('RGB').save(jpeg, format='JPEG')
        for key in ('mobileone-s0',):
            with self.subTest(model=key):
                model = PatchCoreLite(model_key=key, feature_engine='onnx', distance_engine='faiss')
                network = FeatureEncoder(key).eval()
                with torch.inference_mode():
                    expected = network(model._image(jpeg.getvalue()))
                torch.testing.assert_close(model.features(jpeg.getvalue()), expected, atol=1e-4, rtol=1e-4)
                for size in INPUT_SIZES:
                    model.set_input_size(size)
                    patches = model.features(jpeg.getvalue())
                    width, height = heatmap_dimensions(*input_dimensions(size), key)
                    self.assertEqual(tuple(patches.shape), (width * height, MODELS[key]['dimensions']))
                    model.memory = patches[:128].contiguous()
                    model.threshold, model.baseline_frames = 1., 20
                    self.assertEqual(tuple(model.score(patches)[1].shape), (height, width))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'baseline.pt'
                    model.save(path)
                    restored = PatchCoreLite(model_key=key, input_size=model.input_size, feature_engine='onnx')
                    restored.load(path)
                    with self.assertRaises(ValueError):
                        PatchCoreLite(input_size=model.input_size).load(path)

    def test_mobile_onnx_layout_baseline_identity_and_learning(self):
        import io
        from PIL import Image
        from app.cameras.model_profiles import heatmap_dimensions
        jpeg = io.BytesIO()
        Image.new('RGB', (96, 54), (80, 110, 140)).save(jpeg, format='JPEG')
        mobile = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx', distance_engine='faiss')
        for size in INPUT_SIZES:
            mobile.set_input_size(size)
            patches = mobile.features(jpeg.getvalue())
            width, height = heatmap_dimensions(*input_dimensions(size), 'mobilenetv3')
            self.assertEqual(tuple(patches.shape), (width * height, 48))
            mobile.memory = patches[:128].contiguous()
            _, heatmap = mobile.score(patches)
            self.assertEqual(tuple(heatmap.shape), (height, width))
        mobile.set_input_size(DEFAULT_INPUT_SIZE)
        patches = mobile.features(jpeg.getvalue())
        mobile.fit([patches + index * .01 for index in range(20)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.pt'
            mobile.save(path)
            with self.assertRaises(ValueError): PatchCoreLite().load(path)
            restored = PatchCoreLite(model_key='mobilenetv3', feature_engine='onnx')
            restored.load(path)
            self.assertEqual(restored.baseline_frames, 20)
            restored.set_model('resnet-fp32')
            self.assertIsNone(restored.memory)

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

    def test_fit_holds_out_entire_frames_and_model_round_trip(self):
        model = PatchCoreLite()
        model.inspect_geometry(self._roi_jpeg())
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

    def test_heatmap_preserves_full_sensor_dimensions(self):
        import io
        from PIL import Image
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (3280, 2464), (128, 128, 128)).save(source, format='JPEG')
        distances = torch.zeros(68, 120)
        result = Image.open(io.BytesIO(overlay(source.getvalue(), distances, 1)))
        self.assertEqual(result.size, (3280, 2464))

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
