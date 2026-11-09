"""Verify scene-test metrics and detect duplicate JPEG leakage across splits."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('camera_quality', Path(__file__).resolve().parents[1] / 'scripts/benchmark-camera-quality.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


class QualityMetricTests(unittest.TestCase):
    def test_shortlist_drops_slower_duplicates_without_losing_quality_tradeoffs(self):
        rows = [dict(model='slow', events_detected=5, false_positives=0, hand_frames_detected=8, mean_ms=1000),
                dict(model='balanced', events_detected=5, false_positives=0, hand_frames_detected=8, mean_ms=500),
                dict(model='fast', events_detected=5, false_positives=1, hand_frames_detected=8, mean_ms=200)]
        for row in rows:
            row.update(events_total=5, normal_frames=100)
        ranked, selected = quality.rank_models(rows)
        self.assertEqual(ranked[0]['model'], 'balanced')
        self.assertEqual(selected, ['balanced', 'fast'])

    def test_shortlist_excludes_silent_and_noisy_models(self):
        rows = [dict(model='silent', events_detected=0, events_total=5,
                     false_positives=0, normal_frames=100, hand_frames_detected=0, mean_ms=1),
                dict(model='noisy', events_detected=5, events_total=5,
                     false_positives=80, normal_frames=100, hand_frames_detected=9, mean_ms=1)]
        _, selected = quality.rank_models(rows)
        self.assertEqual(selected, [])

    def test_events_are_counted_once_and_auc_uses_scores(self):
        records = [dict(kind='hand', episode='one', score=3., alarm=True),
                   dict(kind='hand', episode='one', score=1., alarm=False),
                   dict(kind='hand', episode='two', score=4., alarm=True),
                   dict(kind='normal', score=.5, alarm=False),
                   dict(kind='normal', score=2.5, alarm=True)]
        result = quality.quality_metrics(records)
        self.assertEqual((result['events_detected'], result['events_total']), (2, 2))
        self.assertEqual(result['hand_frames_detected'], 2)
        self.assertEqual(result['false_positives'], 1)
        self.assertAlmostEqual(result['auroc'], 5/6)
        with self.assertRaises(ValueError):
            quality.quality_metrics(records[:3])

    def test_auc_ties_count_half_and_hashes_detect_copied_training_images(self):
        metrics = quality.quality_metrics([dict(kind='hand', episode='one', score=1., alarm=False),
                                           dict(kind='normal', score=1., alarm=False)])
        self.assertEqual(metrics['auroc'], .5)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / str(i) for i in range(3)]
            for path, data in zip(paths, (b'training', b'calibration', b'evaluation')):
                path.write_bytes(data)
            policy = dict(training=[str(paths[0])], calibration=[str(paths[1])], test=[dict(path=str(paths[2]))])
            quality.validate_partitions(policy)
            paths[2].write_bytes(b'training')
            with self.assertRaises(ValueError):
                quality.validate_partitions(policy)
