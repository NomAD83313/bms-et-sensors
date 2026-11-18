"""Frame gate: skip full analysis only while the analysed area is unchanged."""
import io
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

try:
    import numpy as np
    from PIL import Image, ImageDraw
    from app.cameras.frame_gate import FrameGate, block_metrics, BLOCK
except ImportError:
    np = None

from app.cameras import anomaly_control, control
from tests.isolated_import import load_isolated_module
from tests.test_cameras_worker_quality import FakeModelGeometry

ROI = dict(x=.25, y=.25, width=.5, height=.5)


def scene(size=(640, 480), noise=0, seed=0, box=None, shade=0):
    """A deterministic textured scene; `box` is (x, y, width, height, darkening)."""
    generator = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(size[0]), np.arange(size[1]))
    pixels = np.stack([(x * 255 // size[0]), (y * 255 // size[1]), ((x + y) % 255)], axis=2).astype(np.int16) + shade
    if noise:
        pixels += generator.integers(-noise, noise + 1, pixels.shape)
    if box:
        bx, by, bw, bh, darken = box
        pixels[by:by + bh, bx:bx + bw] -= darken
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    stream = io.BytesIO()
    image.save(stream, format='JPEG', quality=92)
    return stream.getvalue()


@unittest.skipIf(np is None, 'Requires NumPy and Pillow')
class FrameGateTests(unittest.TestCase):
    def gate(self, **kwargs):
        self.now = [100.]
        return FrameGate(enabled=kwargs.pop('enabled', True), block_difference=3., mean_difference=1.2,
                         max_age=2., clock=lambda: self.now[0], **kwargs)

    def remember(self, gate, jpeg, roi=ROI):
        thumbnail = gate.thumbnail(jpeg, roi)
        gate.remember(thumbnail, ('score', 'heatmap'))
        return thumbnail

    def test_thumbnail_is_a_small_rgb_view_of_the_roi_only(self):
        thumbnail, source = FrameGate.thumbnail(scene(), ROI)
        self.assertEqual(source, (640, 480))
        self.assertEqual(thumbnail.dtype, np.uint8)
        self.assertEqual(thumbnail.shape, (144, 192, 3))
        self.assertEqual(thumbnail.shape[0], 144)
        outside_changed = scene(box=(0, 0, 100, 480, 150))
        np.testing.assert_array_equal(FrameGate.thumbnail(outside_changed, ROI)[0],
                                      FrameGate.thumbnail(scene(box=(0, 0, 100, 480, 0)), ROI)[0])

    def test_equal_luminance_color_changes_are_evaluated_in_rgb(self):
        def jpeg(color):
            stream = io.BytesIO()
            Image.new('RGB', (256, 256), color).save(stream, format='JPEG', quality=100, subsampling=0)
            return stream.getvalue()
        red, green = jpeg((255, 0, 0)), jpeg((0, 130, 0))
        gate = self.gate()
        self.remember(gate, red, roi=None)
        self.assertIsNone(gate.reusable(gate.thumbnail(green, None)))

    def test_partial_edge_blocks_keep_their_actual_area(self):
        for height, width in [(108, 192), (108, 191), (4, 3)]:
            for color in (False, True):
                with self.subTest(size=(height, width), rgb=color):
                    shape = (height, width, 3) if color else (height, width)
                    base = np.zeros(shape, dtype=np.uint8)
                    changed = base.copy()
                    changed[-1, -1] = 255
                    mean, largest = block_metrics(base, changed)
                    edge_area = ((height - 1) % BLOCK + 1) * ((width - 1) % BLOCK + 1)
                    self.assertAlmostEqual(mean, 255 / (height * width))
                    self.assertAlmostEqual(largest, 255 / edge_area)

    def test_bottom_strip_anomaly_forces_a_new_evaluation(self):
        base = np.zeros((108, 192), dtype=np.uint8)
        changed = base.copy()
        changed[104:108, 80:88] = 255
        gate = self.gate()
        gate.remember((base, (1920, 1080)), ('score', 'heatmap'))
        self.assertIsNone(gate.reusable((changed, (1920, 1080))))

    def test_undecodable_or_missing_frames_have_no_thumbnail(self):
        self.assertIsNone(FrameGate.thumbnail(b'not a jpeg', ROI))
        self.assertIsNone(FrameGate.thumbnail(b'\xff\xd8truncated\xff\xd9', ROI))

    def test_block_metrics_expose_small_objects_that_the_mean_hides(self):
        base = FrameGate.thumbnail(scene(), ROI)[0]
        small = FrameGate.thumbnail(scene(box=(300, 200, 8, 40, 90)), ROI)[0]
        mean, largest = block_metrics(base, small)
        self.assertLess(mean, 1.2)
        self.assertGreater(largest, 3)
        self.assertEqual(block_metrics(base, base), (0.0, 0.0))

    def test_static_frames_are_reused_and_changes_force_evaluation(self):
        gate = self.gate()
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(), ROI)), 'No reference yet')
        self.remember(gate, scene(seed=1, noise=2))
        for seed in (2, 3, 4):
            self.assertEqual(gate.reusable(gate.thumbnail(scene(seed=seed, noise=2), ROI)), ('score', 'heatmap'))
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(seed=5, noise=2, box=(300, 200, 12, 40, 90)), ROI)),
                          'A small object inside the area must be evaluated')
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(seed=6, noise=2, shade=12), ROI)),
                          'Lighting drift must be evaluated')

    def test_changes_outside_the_roi_are_ignored(self):
        gate = self.gate()
        self.remember(gate, scene())
        self.assertIsNotNone(gate.reusable(gate.thumbnail(scene(box=(0, 0, 100, 480, 150)), ROI)))

    def test_a_reference_older_than_the_maximum_age_is_re_evaluated(self):
        gate = self.gate()
        self.remember(gate, scene())
        self.now[0] += 1.9
        self.assertIsNotNone(gate.reusable(gate.thumbnail(scene(), ROI)))
        self.now[0] += .2
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(), ROI)))

    def test_source_geometry_change_is_never_reused(self):
        gate = self.gate()
        self.remember(gate, scene())
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(size=(800, 600)), ROI)))

    def test_reset_and_disabled_gates_never_reuse(self):
        gate = self.gate()
        self.remember(gate, scene())
        gate.reset()
        self.assertIsNone(gate.reusable(gate.thumbnail(scene(), ROI)))
        off = self.gate(enabled=False)
        self.remember(off, scene())
        self.assertIsNone(off.reusable(off.thumbnail(scene(), ROI)))

    def test_status_reports_recent_skip_ratio(self):
        gate = self.gate()
        for skipped in (True, True, True, False):
            gate.count(skipped)
        status = gate.status()
        self.assertEqual((status['frames_skipped'], status['frames_evaluated']), (3, 1))
        self.assertEqual(status['skip_ratio'], .75)

    def test_settings_are_validated(self):
        for name, value in (('CAMERA_ANOMALY_FRAME_GATE', '2'), ('CAMERA_ANOMALY_GATE_BLOCK_DIFF', '0'),
                            ('CAMERA_ANOMALY_GATE_MEAN_DIFF', 'x'), ('CAMERA_ANOMALY_GATE_MAX_AGE_SEC', '99')):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}), self.assertRaises(ValueError):
                FrameGate()
        with patch.dict(os.environ, {'CAMERA_ANOMALY_FRAME_GATE': '0'}):
            self.assertFalse(FrameGate().enabled)
        self.assertTrue(FrameGate().enabled)


@unittest.skipIf(np is None, 'Requires NumPy and Pillow')
class AdaptiveThresholdTests(unittest.TestCase):
    """Thresholds follow each camera's noise between the configured floor and the limit."""
    SOURCE = (1920, 1080)

    def gate(self, **kwargs):
        self.now = [100.]
        return FrameGate(enabled=True, block_difference=3., mean_difference=1.2, max_age=2.,
                         clock=lambda: self.now[0], adaptive=kwargs.pop('adaptive', True),
                         block_limit=12., mean_limit=4., **kwargs)

    @staticmethod
    def frame(noise, seed, shape=(108, 192, 3), level=120):
        generator = np.random.default_rng(seed)
        return np.clip(level + generator.normal(0, noise, shape), 0, 255).astype(np.uint8)

    def feed(self, gate, noise, count, normal=True, shape=(108, 192, 3), source=SOURCE):
        """Compare `count` frames with the reference, as the worker does, and report verdicts."""
        gate.remember((self.frame(noise, 0, shape), source), ('score', 'heatmap'))
        for index in range(count):
            gate.reusable((self.frame(noise, index + 1, shape), source))
            gate.observe(normal)

    def test_a_quiet_camera_keeps_the_configured_thresholds(self):
        gate = self.gate()
        self.feed(gate, noise=.2, count=120)
        self.assertEqual((gate.block_difference, gate.mean_difference), (3., 1.2))
        self.assertGreaterEqual(gate.status()['frame_gate_noise_samples'], 60)

    def test_a_noisy_camera_raises_its_thresholds_until_frames_are_skipped(self):
        gate = self.gate()
        noisy = (self.frame(1.5, 999), self.SOURCE)
        gate.remember((self.frame(1.5, 0), self.SOURCE), ('score', 'heatmap'))
        self.assertIsNone(gate.reusable(noisy), 'Before adaptation noise looks like a change')
        self.feed(gate, noise=1.5, count=120)
        self.assertGreater(gate.block_difference, 3.)
        self.assertLessEqual(gate.block_difference, 12.)
        self.assertEqual(gate.reusable(noisy), ('score', 'heatmap'))
        status = gate.status()
        self.assertEqual(status['frame_gate_block_threshold'], round(gate.block_difference, 2))
        self.assertTrue(status['frame_gate_adaptive'])

    def test_thresholds_never_exceed_the_limits(self):
        gate = self.gate()
        self.feed(gate, noise=40, count=120)
        self.assertEqual((gate.block_difference, gate.mean_difference), (12., 4.))

    def test_samples_need_a_normal_verdict(self):
        gate = self.gate()
        self.feed(gate, noise=3, count=120, normal=False)
        self.assertEqual(gate.status()['frame_gate_noise_samples'], 0)
        self.assertEqual(gate.block_difference, 3.)

    def test_a_new_area_or_source_starts_again_from_the_floor(self):
        gate = self.gate()
        self.feed(gate, noise=3, count=120)
        self.assertGreater(gate.block_difference, 3.)
        gate.remember((self.frame(3, 0, (60, 192, 3)), self.SOURCE), ('score', 'heatmap'))
        self.assertEqual((gate.block_difference, gate.mean_difference), (3., 1.2))
        self.assertEqual(len(gate.noise), 0)

    def test_expired_references_still_measure_noise(self):
        gate = self.gate()
        gate.remember((self.frame(3, 0), self.SOURCE), ('score', 'heatmap'))
        self.now[0] += 5
        self.assertIsNone(gate.reusable((self.frame(3, 1), self.SOURCE)), 'Too old to reuse')
        gate.observe(True)
        self.assertEqual(len(gate.noise), 1)

    def test_adaptation_can_be_switched_off(self):
        gate = self.gate(adaptive=False)
        self.feed(gate, noise=3, count=120)
        self.assertEqual((gate.block_difference, len(gate.noise)), (3., 0))
        with patch.dict(os.environ, {'CAMERA_ANOMALY_GATE_ADAPTIVE': '0'}):
            self.assertFalse(FrameGate(enabled=True).adaptive)
        with patch.dict(os.environ, {'CAMERA_ANOMALY_GATE_ADAPTIVE': 'yes'}), self.assertRaises(ValueError):
            FrameGate(enabled=True)


@unittest.skipIf(np is None, 'Requires NumPy and Pillow')
class WorkerFrameGateTests(unittest.TestCase):
    def build(self, root, environment=None):
        calls = []

        class Model(FakeModelGeometry):
            memory = None
            baseline_frames = 60
            threshold = 1
            source_size = (640, 480)
            effective_input_size = (320, 240)

            def load(self, path):
                self.memory = True

            def set_roi(self, roi):
                self.roi = roi

            def features(self, jpeg):
                calls.append('features')
                return 'patches'

            def score(self, features):
                calls.append('score')
                return .5, np.zeros((15, 20), dtype=np.float32)

        (root / 'baseline.npz').write_bytes(b'baseline')
        anomaly_control.write_json(root / 'session.json', dict(monitoring=True, multiplier=1, roi=ROI))
        module = types.ModuleType('patchcore_lite')
        module.PatchCoreLite, module.overlay = Model, Mock(return_value=b'heat')
        with patch.dict(os.environ, environment or {}), patch.object(anomaly_control, 'DATA', root), \
                patch.object(control, 'CONTROL', root / 'control'):
            worker_module = load_isolated_module('app.cameras.anomaly_worker', {
                'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})
            worker = worker_module.AnomalyWorker()
        frames = []
        worker.fetch_analysis_frame = lambda: (frames[0], None, 1)
        worker.validate_frame_token = Mock()
        return worker, calls, frames

    def analyze(self, worker):
        state = {}
        worker.analyze_frame(state, time.monotonic())
        return state

    def test_static_scene_is_evaluated_once_and_then_reuses_the_result(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            frames.append(scene(seed=1, noise=2))
            first = self.analyze(worker)
            self.assertFalse(first['frame_skipped'])
            for seed in (2, 3, 4):
                frames[0] = scene(seed=seed, noise=2)
                state = self.analyze(worker)
                self.assertTrue(state['frame_skipped'])
                self.assertEqual(state['state'], 'normal')
                self.assertEqual(state['score'], .5)
                self.assertEqual(state['feature_ms'], first['feature_ms'])
            self.assertEqual(calls, ['features', 'score'])
            self.assertEqual((state['frames_evaluated'], state['frames_skipped']), (1, 3))
            self.assertEqual(state['skip_ratio'], .75)

    def test_a_change_inside_the_area_is_fully_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            frames.append(scene(seed=1, noise=2))
            self.analyze(worker)
            frames[0] = scene(seed=2, noise=2, box=(300, 200, 12, 40, 90))
            state = self.analyze(worker)
            self.assertFalse(state['frame_skipped'])
            self.assertEqual(calls, ['features', 'score'] * 2)

    def test_the_reference_expires_so_slow_drift_is_re_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            now = [50.]
            worker.gate.clock = lambda: now[0]
            frames.append(scene(seed=1, noise=2))
            self.analyze(worker)
            now[0] += 2.5
            self.assertFalse(self.analyze(worker)['frame_skipped'])
            self.assertEqual(calls, ['features', 'score'] * 2)

    def test_commands_and_learning_reset_the_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            frames.append(scene(seed=1, noise=2))
            self.analyze(worker)
            worker.reset_detection_candidate()
            self.assertFalse(self.analyze(worker)['frame_skipped'])
            worker.learning, worker.monitoring = True, False
            worker.sessions = Mock()
            worker.finish_learning = Mock()
            before = len(calls)
            state = self.analyze(worker)
            self.assertNotIn('frame_skipped', state)
            self.assertEqual(calls[before:], ['features'])

    def test_source_geometry_change_is_evaluated_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            frames.append(scene(seed=1, noise=2))
            self.analyze(worker)
            frames[0] = scene(size=(800, 600), seed=1, noise=2)
            self.assertFalse(self.analyze(worker)['frame_skipped'])

    def test_the_gate_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory), {'CAMERA_ANOMALY_FRAME_GATE': '0'})
            frames.append(scene(seed=1, noise=2))
            for _ in range(3):
                self.assertFalse(self.analyze(worker).get('frame_skipped', False))
            self.assertEqual(calls, ['features', 'score'] * 3)

    def test_an_invalid_frame_token_is_still_rejected_before_it_becomes_the_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            worker.validate_frame_token = Mock(side_effect=ValueError('Preview frame invalidated'))
            frames.append(scene(seed=1, noise=2))
            with self.assertRaises(ValueError):
                self.analyze(worker)
            worker.validate_frame_token = Mock()
            self.assertFalse(self.analyze(worker)['frame_skipped'])

    def test_invalid_tokens_cannot_reuse_scores_or_advance_event_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            worker, calls, frames = self.build(Path(directory))
            frames.append(scene())
            self.analyze(worker)
            worker.validate_frame_token = Mock(side_effect=ValueError('Preview frame invalidated'))
            worker.monitor_frame = Mock()
            before = worker.gate.skipped
            with self.assertRaises(ValueError):
                self.analyze(worker)
            worker.validate_frame_token.assert_called_once()
            worker.monitor_frame.assert_not_called()
            self.assertEqual(worker.gate.skipped, before)
            self.assertEqual(calls, ['features', 'score'])


if __name__ == '__main__':
    unittest.main()
