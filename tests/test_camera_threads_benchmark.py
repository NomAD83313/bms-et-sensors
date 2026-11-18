"""Thread benchmarks must reproduce preprocessing and reject ambiguous test schedules."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    'camera_threads_benchmark', Path(__file__).resolve().parents[1] / 'scripts/benchmark-camera-threads.py')
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)
REQUIRED = ['--frame', 'frame.jpg', '--baseline', 'baseline.npz', '--output', 'result.json']


class ThreadBenchmarkTests(unittest.TestCase):
    def test_explicit_preprocessing_and_model_are_preserved(self):
        args = BENCHMARK.parse_args(REQUIRED + [
            '--model', 'mobilenetv4-convsmall-x0.5', '--jpeg-draft', '1', '--top-k', '3',
            '--roi', '{"x":0.1,"y":0.2,"width":0.5,"height":0.6}', '--threads', '1', '4'])
        self.assertEqual(args.model, 'mobilenetv4-convsmall-x0.5')
        self.assertEqual((args.jpeg_draft, args.top_k, args.threads), (1, 3, [1, 4]))
        self.assertEqual(args.roi, dict(x=.1, y=.2, width=.5, height=.6))

    def test_invalid_roi_and_duplicate_thread_counts_are_rejected(self):
        for extra in [['--roi', '{"x":0.9,"y":0,"width":0.5,"height":1}'],
                      ['--threads', '1', '1'], ['--samples', '4']]:
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                BENCHMARK.parse_args(REQUIRED + extra)

    def test_percentile_accepts_a_single_measurement(self):
        self.assertEqual(BENCHMARK.percentile([12], .95), 12)
