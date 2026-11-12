"""Numerical model checks; run inside the camera-analysis image."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

try:
    import numpy as np
    import onnxruntime
    from PIL import Image
    from app.cameras.anomaly_control import INPUT_SIZES, DEFAULT_INPUT_SIZE, input_dimensions
    from app.cameras.model_profiles import MODELS, heatmap_dimensions
    from app.cameras.patchcore_lite import PatchCoreLite
except ImportError:
    np = None


def roi_jpeg(change_outside=False, size=(320, 240)):
    from PIL import ImageDraw
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


def textured_jpegs():
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


def noise_jpeg(size=(960, 540)):
    stream = io.BytesIO()
    Image.effect_noise(size, 60).convert('RGB').save(stream, format='JPEG')
    return stream.getvalue()


def read_baseline(path):
    with np.load(path, allow_pickle=False) as archive:
        return json.loads(str(archive['metadata'])), np.array(archive['memory'])


def write_baseline(path, metadata, memory):
    with open(path, 'wb') as stream:
        np.savez(stream, metadata=np.array(json.dumps(metadata)), memory=memory)


@unittest.skipIf(np is None, 'Requires the optional camera-analysis image')
class PatchMemoryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {
            'CAMERA_ANOMALY_FEATURE_ENGINE': 'onnx',
            'CAMERA_ANOMALY_DISTANCE_ENGINE': 'numpy',
            'CAMERA_ANOMALY_THREADS': '1'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_runtime_never_imports_pytorch(self):
        code = ("import sys; sys.path.insert(0, %r); import app.cameras.patchcore_lite as module; "
                "module.PatchCoreLite(); "
                "bad = sorted(name for name in sys.modules if name.split('.')[0] in ('torch', 'torchvision', 'timm')); "
                "assert not bad, bad" % str(ROOT))
        result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True,
                                env={'CAMERA_ANOMALY_DISTANCE_ENGINE': 'numpy', 'PATH': ''})
        self.assertEqual(result.returncode, 0, result.stderr[-600:])

    def test_bounded_int_accepts_only_whole_numbers_in_range(self):
        from app.cameras.patchcore_lite import bounded_int
        self.assertEqual(bounded_int('4', 1, 4, 'x'), 4)
        self.assertEqual(bounded_int(2.0, 1, 4, 'x'), 2)
        for bad in (True, 0, 5, 2.5, '2.5', 'a', None):
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, 'bounds'):
                bounded_int(bad, 1, 4, 'bounds')

    def test_unsupported_engines_are_rejected(self):
        for arguments in (dict(feature_engine='torch'), dict(distance_engine='torch'),
                          dict(feature_engine='tflite'), dict(distance_engine='annoy')):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                PatchCoreLite(**arguments)

    def test_calibrated_threshold_is_the_floored_ninety_ninth_percentile(self):
        from app.cameras.patchcore_lite import calibrated_threshold
        scores = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(calibrated_threshold(scores), float(np.quantile(scores, .99)), places=12)
        self.assertEqual(calibrated_threshold([0.0, 0.0]), 1e-6)

    def test_top_k_mean_uses_the_largest_values(self):
        from app.cameras.patchcore_lite import top_k_mean
        values = np.array([1, 9, 3, 7, 5], dtype=np.float32)
        self.assertEqual(top_k_mean(values, 1), 9)
        self.assertEqual(top_k_mean(values, 3), (9 + 7 + 5) / 3)
        self.assertEqual(top_k_mean(values, 20), float(values.mean()))

    def test_greedy_coreset_picks_the_farthest_remaining_point(self):
        from app.cameras.patchcore_lite import greedy_coreset
        patches = np.array([[0, 0], [1, 0], [10, 0], [0, 10], [10, 10]], dtype=np.float32)
        projection = np.eye(2, dtype=np.float32)
        self.assertEqual(greedy_coreset(patches, projection, 3), [0, 4, 2])
        self.assertEqual(len(greedy_coreset(patches, projection, 99)), 5)
        reference = []
        distances = np.full(len(patches), np.inf)
        index = 0
        for _ in range(5):
            reference.append(index)
            distances = np.minimum(distances, ((patches - patches[index]) ** 2).sum(axis=1))
            distances[index] = -1
            index = int(distances.argmax())
        self.assertEqual(greedy_coreset(patches, projection, 5), reference)

    def test_chunked_distances_match_the_full_matrix(self):
        from app.cameras.patchcore_lite import nearest_distances, DISTANCE_CHUNK
        generator = np.random.default_rng(10)
        patches = generator.standard_normal((DISTANCE_CHUNK * 2 + 37, 48)).astype(np.float32)
        memory = generator.standard_normal((64, 48)).astype(np.float32)
        expected = np.sqrt(((patches[:, None, :] - memory[None, :, :]) ** 2).sum(axis=2)).min(axis=1)
        np.testing.assert_allclose(nearest_distances(patches, memory), expected, rtol=1e-5, atol=1e-5)
        model = PatchCoreLite()
        model.memory = memory
        score, heatmap = model.score(patches[:48 * 27])
        self.assertEqual(heatmap.shape, (27, 48))
        self.assertAlmostEqual(score, float(expected[:48 * 27].max()), places=4)

    def test_faiss_preserves_euclidean_scores_and_rebuilds_index(self):
        try:
            import faiss  # noqa: F401
        except ImportError:
            self.skipTest('Requires faiss-cpu')
        generator = np.random.default_rng(12)
        patches = generator.standard_normal((48 * 27, 48)).astype(np.float32)
        reference = PatchCoreLite(distance_engine='numpy')
        fast = PatchCoreLite(distance_engine='faiss')
        for offset in (0, 3):
            reference.memory = (generator.standard_normal((64, 48)) + offset).astype(np.float32)
            fast.memory = reference.memory
            expected_score, expected_heatmap = reference.score(patches)
            actual_score, actual_heatmap = fast.score(patches)
            np.testing.assert_allclose(actual_heatmap, expected_heatmap, rtol=1e-5, atol=1e-5)
            self.assertAlmostEqual(actual_score, expected_score, places=4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            reference.threshold, reference.baseline_frames = 1, 60
            reference.inspect_geometry(roi_jpeg())
            reference.save(path)
            fast.load(path)
            actual_score, actual_heatmap = fast.score(patches)
            np.testing.assert_allclose(actual_heatmap, expected_heatmap, rtol=1e-5, atol=1e-5)
            self.assertAlmostEqual(actual_score, expected_score, places=4)

    def test_faiss_thread_limit_applies_in_the_thread_running_the_search(self):
        from concurrent.futures import ThreadPoolExecutor
        try:
            import faiss
        except ImportError:
            self.skipTest('Requires faiss-cpu')
        model = PatchCoreLite(distance_engine='faiss', threads=1)
        model.memory = np.zeros((4, model.feature_dimensions), dtype=np.float32)
        width, height = heatmap_dimensions(*input_dimensions(model.input_size), model.model_key)
        patches = np.zeros((width * height, model.feature_dimensions), dtype=np.float32)

        def search():
            faiss.omp_set_num_threads(3)
            model.score(patches)
            return faiss.omp_get_max_threads()

        with ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(search).result(timeout=5), 1)

    def test_grayscale_decodes_luminance_and_separates_baselines(self):
        jpeg = roi_jpeg()
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        color = PatchCoreLite(model_key='mobilenetv3')
        gray = PatchCoreLite(model_key='mobilenetv3', color_mode='grayscale')
        for model in (color, gray):
            model.set_roi(roi)
        color_tensor, gray_tensor = color._image(jpeg), gray._image(jpeg)
        self.assertEqual(gray_tensor.shape, color_tensor.shape)
        self.assertEqual(tuple(gray_tensor.shape), (1, 3, 120, 160))
        self.assertEqual(gray.preparation_metrics['decoded_mode'], 'L')
        mean = np.array((.485, .456, .406), dtype=np.float32)[None, :, None, None]
        std = np.array((.229, .224, .225), dtype=np.float32)[None, :, None, None]
        luminance = gray_tensor * std + mean
        np.testing.assert_allclose(luminance[:, 0], luminance[:, 1], rtol=0, atol=1e-6)
        np.testing.assert_allclose(luminance[:, 0], luminance[:, 2], rtol=0, atol=1e-6)
        self.assertGreater(float(np.abs(gray_tensor - color_tensor).mean()), .01)
        features = gray.features(jpeg)
        gray.memory = np.ascontiguousarray(features[:64])
        gray.threshold, gray.baseline_frames = 1, 20
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            gray.save(path)
            restored = PatchCoreLite(model_key='mobilenetv3', color_mode='grayscale')
            restored.set_roi(roi)
            restored.load(path)
            np.testing.assert_allclose(restored.features(jpeg), features)
            with self.assertRaises(ValueError):
                color.load(path)
            gray.set_color_mode('grayscale')
            self.assertIsNotNone(gray.memory)
            gray.set_color_mode('rgb')
            self.assertIsNone(gray.memory)
            self.assertEqual(gray.roi, roi)
            color.inspect_geometry(jpeg)
            color.memory, color.threshold, color.baseline_frames = features[:64], 1, 20
            color.save(path)
            metadata, memory = read_baseline(path)
            metadata.pop('color_mode')
            write_baseline(path, metadata, memory)
            color.load(path)
            with self.assertRaises(ValueError):
                restored.load(path)
        for invalid in (None, True, 'gray'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                gray.set_color_mode(invalid)

    def test_saved_sessions_stream_balanced_candidates_and_hold_out_whole_frames(self):
        model = PatchCoreLite(model_key='mobilenetv3', coreset_size=1024)
        width, height = input_dimensions(model.input_size)
        model.effective_input_size = (width, height)
        count = ((width + 15) // 16) * ((height + 15) // 16)

        def features(jpeg):
            return np.full((count, model.feature_dimensions), float(jpeg.decode()), dtype=np.float32)

        model.features = Mock(side_effect=features)
        with tempfile.TemporaryDirectory() as directory:
            groups = []
            for group_index, frames in enumerate((20, 40)):
                paths = []
                for index in range(frames):
                    path = Path(directory) / f'{group_index}-{index}.jpg'
                    path.write_text(str(group_index * 100 + index))
                    paths.append(path)
                groups.append(paths)
            with patch.object(model, '_fit_candidates', wraps=model._fit_candidates) as fit:
                calibration = model.fit_sessions(groups)
            candidates = fit.call_args.args[0]
            self.assertEqual(len(candidates), 8000)
            self.assertEqual(int((candidates[:, 0] < 100).sum()), 4000)
            self.assertFalse(bool((np.remainder(candidates[:, 0], 5) == 0).any()),
                             'Calibration frames must not enter memory')
            self.assertEqual([item['calibration_frames'] for item in calibration], [4, 8])
            self.assertEqual(model.threshold, max(item['calibration_threshold'] for item in calibration))
            self.assertEqual(len(model.memory), 1024)
            self.assertEqual(model.baseline_frames, 60)
            self.assertEqual(model.features.call_count, 60)

    def test_roi_crop_ignores_outside_pixels_and_preserves_its_aspect_ratio(self):
        model = PatchCoreLite(model_key='mobilenetv3')
        model.set_roi(dict(x=.25, y=.25, width=.5, height=.5))
        first = model._image(roi_jpeg())
        second = model._image(roi_jpeg(change_outside=True))
        np.testing.assert_array_equal(first, second)
        self.assertEqual(model.source_size, (320, 240))
        self.assertEqual(model.effective_input_size, (160, 120))
        self.assertEqual(tuple(first.shape), (1, 3, 120, 160))
        model.set_roi(None)
        self.assertEqual(tuple(model._image(roi_jpeg()).shape), (1, 3, 432, 768))

    def test_jpeg_draft_preserves_roi_shape_records_metrics_and_separates_baselines(self):
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        jpeg = roi_jpeg(size=(3280, 2464))
        full = PatchCoreLite(model_key='mobilenetv3', jpeg_draft=False)
        draft = PatchCoreLite(model_key='mobilenetv3', jpeg_draft=True)
        for model in (full, draft):
            model.set_roi(roi)
        full_tensor, draft_tensor = full._image(jpeg), draft._image(jpeg)
        self.assertEqual(tuple(full_tensor.shape), tuple(draft_tensor.shape))
        self.assertEqual(draft.effective_input_size, (575, 432))
        self.assertEqual((full.preparation_metrics['decoded_width'],
                          full.preparation_metrics['decoded_height']), (3280, 2464))
        self.assertLess(draft.preparation_metrics['decoded_width'], 3280)
        self.assertLess(draft.preparation_metrics['decoded_height'], 2464)
        self.assertLess(float(np.abs(full_tensor - draft_tensor).mean()), .03)
        for key in ('geometry_ms', 'decode_ms', 'transform_ms', 'tensor_ms', 'total_ms'):
            self.assertGreaterEqual(draft.preparation_metrics[key], 0)
        draft.memory = np.zeros((1, draft.feature_dimensions), dtype=np.float32)
        draft.threshold, draft.baseline_frames = 1, 20
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            draft.save(path)
            self.assertTrue(read_baseline(path)[0]['jpeg_draft'])
            restored = PatchCoreLite(model_key='mobilenetv3', jpeg_draft=True)
            restored.set_roi(roi)
            restored.load(path)
            with self.assertRaises(ValueError):
                full.load(path)
        for invalid in (-1, 2, 'yes'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(jpeg_draft=invalid)

    def test_top_k_averages_the_largest_patch_scores_and_separates_baselines(self):
        patches = np.zeros((48 * 27, 48), dtype=np.float32)
        patches[:-1, 0] = 1
        patches[-1, 0] = 20
        maximum = PatchCoreLite(top_k=1)
        maximum.memory = np.zeros((1, 48), dtype=np.float32)
        averaged = PatchCoreLite(top_k=5)
        averaged.memory = maximum.memory
        self.assertAlmostEqual(maximum.score(patches)[0], 20, places=5)
        self.assertAlmostEqual(averaged.score(patches)[0], 4.8, places=5)
        averaged.threshold, averaged.baseline_frames = 1, 20
        averaged.inspect_geometry(roi_jpeg())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            averaged.save(path)
            self.assertEqual(read_baseline(path)[0]['score_top_k'], 5)
            PatchCoreLite(top_k=5).load(path)
            with self.assertRaises(ValueError):
                maximum.load(path)
        for invalid in (0, 21, 1.5, True, 'invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(top_k=invalid)

    def test_configurable_coreset_size_is_bounded_and_persisted(self):
        model = PatchCoreLite(coreset_size=256)
        count = 48 * 27
        samples = [np.full((count, 48), index * .01, dtype=np.float32) for index in range(20)]
        model.fit(samples)
        model.inspect_geometry(roi_jpeg())
        self.assertEqual(len(model.memory), 256)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            model.save(path)
            self.assertEqual(read_baseline(path)[0]['coreset_size'], 256)
            restored = PatchCoreLite()
            restored.load(path)
            self.assertEqual(len(restored.memory), 256)
        for invalid in (0, 255, 4097, 256.5, True, 'invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PatchCoreLite(coreset_size=invalid)

    def test_roi_baseline_preserves_geometry_and_rejects_other_regions_or_source_sizes(self):
        from app.cameras.roi import ROIGeometryChanged
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        model = PatchCoreLite(model_key='mobilenetv3', distance_engine='faiss')
        model.set_roi(roi)
        patches = model.features(roi_jpeg())
        model.fit([patches + index * .01 for index in range(20)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            model.save(path)
            artifact, memory = read_baseline(path)
            self.assertEqual(artifact['version'], 7)
            self.assertEqual(artifact['roi'], roi)
            self.assertEqual(artifact['roi_preprocessing'], 'roi-original-aspect-v1')
            self.assertEqual(tuple(artifact['source_size']), (320, 240))
            self.assertEqual(tuple(artifact['effective_input_size']), (160, 120))
            restored = PatchCoreLite(model_key='mobilenetv3')
            restored.set_roi(roi)
            restored.load(path)
            self.assertEqual(restored.source_size, model.source_size)
            self.assertEqual(restored.effective_input_size, model.effective_input_size)
            self.assertEqual(restored.baseline_frames, 20)
            np.testing.assert_array_equal(restored.memory, model.memory)
            self.assertEqual(restored.threshold, model.threshold)
            restored_memory = restored.memory
            restored.set_roi(dict(roi))
            self.assertIs(restored.memory, restored_memory)
            before = path.read_bytes()
            for changed in ({'roi_preprocessing': 'old-roi-preprocessing'},
                            {'effective_input_size': (576, 432)},
                            {'effective_input_size': (768, 432)}, {'source_size': (0, 240)}):
                write_baseline(path, dict(artifact, **changed), memory)
                corrupted = path.read_bytes()
                incompatible = PatchCoreLite(model_key='mobilenetv3')
                incompatible.set_roi(roi)
                with self.subTest(metadata=changed), self.assertRaises(ValueError):
                    incompatible.load(path)
                self.assertEqual(path.read_bytes(), corrupted)
            path.write_bytes(before)
            for different_roi in (None, dict(roi, x=.125)):
                incompatible = PatchCoreLite(model_key='mobilenetv3')
                incompatible.set_roi(different_roi)
                with self.subTest(roi=different_roi), self.assertRaises(ValueError):
                    incompatible.load(path)
                self.assertEqual(path.read_bytes(), before)
            restored._image(roi_jpeg())
            with self.assertRaises(ROIGeometryChanged):
                restored._image(roi_jpeg(size=(640, 480)))
            self.assertIsNone(restored.memory)
            self.assertIsNone(restored.threshold)
            self.assertEqual(restored.baseline_frames, 0)

    def test_baseline_files_that_are_not_valid_current_archives_are_rejected(self):
        model = PatchCoreLite()
        model.inspect_geometry(roi_jpeg())
        model.memory = np.zeros((4, 48), dtype=np.float32)
        model.threshold, model.baseline_frames = 1., 20
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            model.save(path)
            metadata, memory = read_baseline(path)
            fresh = PatchCoreLite()
            fresh.inspect_geometry(roi_jpeg())
            fresh.load(path)
            cases = {
                'previous format version': (dict(metadata, version=6), memory),
                'other analysis source': (dict(metadata, analysis_source='other'), memory),
                'wrong feature width': (metadata, np.zeros((4, 128), dtype=np.float32)),
                'coreset mismatch': (dict(metadata, coreset_size=9), memory),
            }
            for name, (changed, array) in cases.items():
                write_baseline(path, changed, array)
                with self.subTest(case=name), self.assertRaises(ValueError):
                    PatchCoreLite().load(path)
            path.write_bytes(b'baseline')
            with self.subTest(case='not an archive'), self.assertRaises(ValueError):
                PatchCoreLite().load(path)
            with open(path, 'wb') as stream:
                np.savez(stream, metadata=np.array(json.dumps(metadata)), memory=np.array([object()], dtype=object))
            with self.subTest(case='pickled array'), self.assertRaises(ValueError):
                PatchCoreLite().load(path)

    def test_roi_heatmap_stays_inside_selected_area_of_full_source(self):
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (640, 480), (128, 128, 128)).save(source, format='JPEG')
        roi = dict(x=.25, y=.25, width=.5, height=.5)
        rendered = Image.open(io.BytesIO(overlay(source.getvalue(), np.full((27, 36), 2., dtype=np.float32), 1, roi=roi)))
        self.assertEqual(rendered.size, (640, 480))
        for point in ((50, 240), (590, 240), (320, 50), (320, 430)):
            pixel = rendered.getpixel(point)
            self.assertLess(max(pixel) - min(pixel), 5)
            self.assertLess(abs(pixel[0] - 128), 5)
        inside = rendered.getpixel((320, 240))
        self.assertGreater(inside[0] - inside[1], 80)

    def test_heatmap_highlights_only_novel_regions(self):
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (224, 224), (128, 128, 128)).save(source, format='JPEG')
        distances = np.zeros((28, 28), dtype=np.float32)
        distances[7:14, 7:14] = 2
        result = Image.open(io.BytesIO(overlay(source.getvalue(), distances, 1)))
        normal = result.getpixel((180, 180))
        unusual = result.getpixel((80, 80))
        self.assertLess(abs(normal[0] - normal[1]), 5)
        self.assertGreater(unusual[0] - unusual[1], 80)

    def test_heatmap_preserves_full_sensor_dimensions(self):
        from app.cameras.patchcore_lite import overlay
        source = io.BytesIO()
        Image.new('RGB', (3280, 2464), (128, 128, 128)).save(source, format='JPEG')
        result = Image.open(io.BytesIO(overlay(source.getvalue(), np.zeros((68, 120), dtype=np.float32), 1)))
        self.assertEqual(result.size, (3280, 2464))

    def test_normalization_uses_each_models_pretrained_rgb_statistics(self):
        stream = io.BytesIO()
        Image.new('RGB', (96, 54), (25, 100, 200)).save(stream, format='JPEG', quality=95)
        jpeg = stream.getvalue()
        pixel = Image.open(io.BytesIO(jpeg)).getpixel((0, 0))
        imagenet = ((.485, .456, .406), (.229, .224, .225))
        for key, (mean, deviation) in (
                ('mobilenetv3', imagenet),
                ('mobilenetv4-convsmall-x0.5', ((.5, .5, .5), (.5, .5, .5)))):
            with self.subTest(model=key):
                model = PatchCoreLite(model_key=key)
                normalized = model._image(jpeg)
                expected = np.array([(channel / 255. - center) / scale
                                     for channel, center, scale in zip(pixel, mean, deviation)], dtype=np.float32)
                self.assertEqual(tuple(normalized.shape), (1, 3, 432, 768))
                np.testing.assert_allclose(normalized[0], np.broadcast_to(expected[:, None, None], normalized[0].shape),
                                           rtol=1e-6, atol=1e-6)

    def test_every_model_covers_all_input_sizes_and_keeps_learning_independent(self):
        jpeg = textured_jpegs()[0]
        for key in MODELS:
            with self.subTest(model=key):
                model = PatchCoreLite(model_key=key, distance_engine='faiss')
                for size in INPUT_SIZES:
                    model.set_input_size(size)
                    patches = model.features(jpeg)
                    width, height = heatmap_dimensions(*input_dimensions(size), key)
                    self.assertEqual(tuple(patches.shape), (width * height, MODELS[key]['dimensions']))
                    self.assertEqual(patches.dtype, np.float32)
                    model.memory = np.ascontiguousarray(patches[:128])
                    model.threshold, model.baseline_frames = 1., 20
                    self.assertEqual(tuple(model.score(patches)[1].shape), (height, width))
                model.set_input_size(DEFAULT_INPUT_SIZE)
                patches = model.features(jpeg)
                scores = model.fit([patches + index * .01 for index in range(20)])
                self.assertEqual(len(scores), 4)
                self.assertEqual(model.baseline_frames, 20)
                self.assertGreater(model.threshold, 0)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'baseline.npz'
                    model.save(path)
                    saved = path.read_bytes()
                    restored = PatchCoreLite(model_key=key, distance_engine='faiss')
                    restored.load(path)
                    self.assertEqual(restored.baseline_frames, 20)
                    self.assertEqual(restored.threshold, model.threshold)
                    np.testing.assert_array_equal(restored.memory, model.memory)
                    self.assertAlmostEqual(restored.score(patches)[0], model.score(patches)[0], places=6)
                    for other in MODELS:
                        if other == key:
                            continue
                        with self.subTest(rejected_model=other), self.assertRaises(ValueError):
                            PatchCoreLite(model_key=other).load(path)
                        self.assertEqual(path.read_bytes(), saved)
                    restored.set_model(next(value for value in MODELS if value != key))
                    self.assertIsNone(restored.memory)
                    self.assertIsNone(restored.threshold)
                    self.assertEqual(restored.baseline_frames, 0)

    def test_fit_holds_out_entire_frames_and_model_round_trip(self):
        model = PatchCoreLite()
        model.inspect_geometry(roi_jpeg())
        # Held-out frames contain distinct features, so leakage would yield zero.
        count = 48 * 27
        samples = [np.full((count, 48), 1.0 if index % 5 == 0 else 0.0, dtype=np.float32) for index in range(20)]
        scores = model.fit(samples)
        self.assertEqual(len(scores), 4)
        self.assertTrue(all(value > 1 for value in scores))
        self.assertGreater(model.threshold, 1)
        score, heatmap = model.score(np.zeros((count, 48), dtype=np.float32))
        self.assertEqual(score, 0)
        self.assertEqual(tuple(heatmap.shape), (27, 48))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            model.save(path)
            restored = PatchCoreLite()
            restored.load(path)
            self.assertEqual(restored.baseline_frames, 20)
            self.assertEqual(restored.threshold, model.threshold)
            np.testing.assert_array_equal(restored.memory, model.memory)

    def test_fit_is_deterministic(self):
        generator = np.random.default_rng(3)
        samples = [generator.standard_normal((48 * 27, 48)).astype(np.float32) for _ in range(20)]
        first, second = PatchCoreLite(), PatchCoreLite()
        first.fit(samples)
        second.fit(samples)
        np.testing.assert_array_equal(first.memory, second.memory)
        self.assertEqual(first.threshold, second.threshold)

    def test_input_sizes_produce_matching_patch_and_heatmap_grids(self):
        source = io.BytesIO()
        Image.new('RGB', (960, 540), (128, 128, 128)).save(source, format='JPEG')
        model = PatchCoreLite()
        for size in INPUT_SIZES:
            with self.subTest(size=size):
                width, height = input_dimensions(size)
                model.set_input_size(size)
                patches = model.features(source.getvalue())
                grid_width, grid_height = heatmap_dimensions(width, height, model.model_key)
                self.assertEqual(tuple(patches.shape), (grid_width * grid_height, 48))
                model.memory = np.ascontiguousarray(patches[:1024])
                model.threshold = 1
                _, heatmap = model.score(patches)
                self.assertEqual(tuple(heatmap.shape), (grid_height, grid_width))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'baseline.npz'
                    model.save(path)
                    restored = PatchCoreLite(input_size=size)
                    restored.load(path)
                    if size != DEFAULT_INPUT_SIZE:
                        with self.assertRaises(ValueError):
                            PatchCoreLite().load(path)
                model.set_input_size(DEFAULT_INPUT_SIZE)
                self.assertIsNone(model.memory)

    def test_thread_settings_preserve_existing_baseline_scores(self):
        jpeg = noise_jpeg()
        model = PatchCoreLite(input_size='768x432', threads=1)
        features = model.features(jpeg)
        model.memory = np.ascontiguousarray(features[::4]) + .1
        model.threshold = 1
        score, heatmap = model.score(features)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.npz'
            model.save(path)
            parallel = PatchCoreLite(input_size='768x432', threads=2)
            parallel.load(path)
            patches = parallel.features(jpeg)
            new_score, new_heatmap = parallel.score(patches)
            np.testing.assert_allclose(patches, features, rtol=1e-4, atol=1e-4)
            np.testing.assert_allclose(new_heatmap, heatmap, rtol=1e-4, atol=1e-4)
            self.assertAlmostEqual(new_score, score, places=3)
        for count in (0, 5, 1.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                PatchCoreLite(threads=count)

    def test_insufficient_normal_frames_are_rejected(self):
        model = PatchCoreLite()
        with self.assertRaises(ValueError):
            model.fit([np.zeros((784, 48), dtype=np.float32)])


def torch_available():
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipIf(np is None or not torch_available(), 'Requires the PyTorch reference (build-time only)')
class PytorchReferenceTests(unittest.TestCase):
    """ONNX encoders and preprocessing against the PyTorch/torchvision originals."""

    def setUp(self):
        environment = patch.dict('os.environ', {
            'CAMERA_ANOMALY_FEATURE_ENGINE': 'onnx', 'CAMERA_ANOMALY_DISTANCE_ENGINE': 'numpy',
            'CAMERA_ANOMALY_THREADS': '1'})
        environment.start()
        self.addCleanup(environment.stop)

    @staticmethod
    def _reference_features(key, tensor):
        import torch
        from app.cameras.feature_encoder import FeatureEncoder
        with torch.inference_mode():
            return FeatureEncoder(key).eval()(torch.from_numpy(tensor)).numpy()

    def assert_features_match(self, actual, expected):
        self.assertEqual(actual.shape, expected.shape)
        difference = actual - expected
        relative_l2 = float(np.linalg.norm(difference) / max(float(np.linalg.norm(expected)), 1e-12))
        normalized_max = float(np.abs(difference).max() / max(float(np.abs(expected).max()), 1e-12))
        self.assertLessEqual(relative_l2, 1e-4)
        self.assertLessEqual(normalized_max, 1e-4)

    def test_preparation_matches_torchvision_and_keeps_independent_contiguous_arrays(self):
        import math
        import torch
        from torchvision.transforms import functional as TF
        from app.cameras.roi import roi_bounds

        pixels = (np.arange(1280 * 960 * 3) % 256).astype(np.uint8).reshape(960, 1280, 3)
        stream = io.BytesIO()
        Image.fromarray(pixels).save(stream, format='JPEG', quality=90)
        jpeg = stream.getvalue()
        region = dict(x=.123, y=.111, width=.631, height=.733)
        for key, profile in MODELS.items():
            for mode in ('rgb', 'grayscale'):
                for draft in (False, True):
                    for roi in (None, region):
                        with self.subTest(model=key, mode=mode, draft=draft, roi=roi):
                            model = PatchCoreLite(model_key=key, input_size='768x432',
                                                  color_mode=mode, jpeg_draft=draft)
                            model.set_roi(roi)
                            actual = model._image(jpeg)
                            width, height = model.effective_input_size
                            image = Image.open(io.BytesIO(jpeg))
                            source_width, source_height = image.size
                            decode_mode = 'L' if mode == 'grayscale' else 'RGB'
                            target = image.size
                            if draft:
                                target = ((width, height) if roi is None else
                                          (math.ceil(width / roi['width']), math.ceil(height / roi['height'])))
                            if draft or mode == 'grayscale':
                                image.draft(decode_mode, target)
                            decoded_width, decoded_height = image.size
                            image = image.convert(decode_mode)
                            if roi is not None:
                                bounds = roi_bounds(source_width, source_height, roi)
                                image = image.crop((round(bounds[0] * decoded_width / source_width),
                                                    round(bounds[1] * decoded_height / source_height),
                                                    round(bounds[2] * decoded_width / source_width),
                                                    round(bounds[3] * decoded_height / source_height)))
                            image = TF.resize(image, [height, width])
                            expected = TF.to_tensor(image)
                            if mode == 'grayscale':
                                expected = expected.expand(3, -1, -1)
                            expected = TF.normalize(expected, profile.get('mean', (.485, .456, .406)),
                                                    profile.get('std', (.229, .224, .225))).unsqueeze(0).numpy()
                            np.testing.assert_array_equal(actual, expected)
                            self.assertTrue(actual.flags['C_CONTIGUOUS'])
                            self.assertEqual(actual.dtype, np.float32)
                            model._image(roi_jpeg(size=(1280, 960)))
                            np.testing.assert_array_equal(actual, expected)

    def test_onnx_encoders_match_pytorch_on_three_nonconstant_jpegs(self):
        for key in MODELS:
            model = PatchCoreLite(model_key=key)
            for index, jpeg in enumerate(textured_jpegs()):
                with self.subTest(model=key, frame=index):
                    expected = self._reference_features(key, model._image(jpeg))
                    self.assert_features_match(model.features(jpeg), expected)

    def test_onnx_encoders_match_pytorch_for_every_input_size(self):
        jpeg = noise_jpeg()
        for key in MODELS:
            model = PatchCoreLite(model_key=key)
            for size in INPUT_SIZES:
                with self.subTest(model=key, size=size):
                    model.set_input_size(size)
                    expected = self._reference_features(key, model._image(jpeg))
                    self.assert_features_match(model.features(jpeg), expected)

    def test_dynamic_onnx_encoders_match_pytorch_and_actual_roi_geometry(self):
        roi = dict(x=.25, y=.1, width=.5, height=.7)
        jpeg = roi_jpeg()
        for key, profile in MODELS.items():
            with self.subTest(model=key):
                fast = PatchCoreLite(model_key=key, distance_engine='faiss')
                fast.set_roi(roi)
                actual = fast.features(jpeg)
                expected = self._reference_features(key, fast._image(jpeg))
                self.assertEqual(fast.effective_input_size, (160, 168))
                heat_width, heat_height = heatmap_dimensions(*fast.effective_input_size, key)
                self.assertEqual(tuple(actual.shape), (heat_width * heat_height, profile['dimensions']))
                self.assert_features_match(actual, expected)
                fast.memory = np.ascontiguousarray(actual[:64])
                self.assertEqual(tuple(fast.score(actual)[1].shape), (heat_height, heat_width))


if __name__ == '__main__':
    unittest.main()
