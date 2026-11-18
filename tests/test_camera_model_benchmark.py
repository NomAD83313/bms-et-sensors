"""Protect model timing comparisons from stale private banks and invalid ROIs."""
import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    'camera_model_benchmark', Path(__file__).resolve().parents[1] / 'scripts/benchmark-camera-models.py')
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class ModelBenchmarkTests(unittest.TestCase):
    def test_roi_rejects_outside_coordinates_and_accepts_full_frame(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            BENCHMARK.parse_roi('{"x":0.9,"y":0,"width":0.5,"height":1}')
        self.assertIsNone(BENCHMARK.parse_roi('{"x":0,"y":0,"width":1,"height":1}'))

    def test_private_bank_cannot_be_reused_for_different_frames_or_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            BENCHMARK.validate_workload(work, {'roi': None}, [b'first', b'second'])
            BENCHMARK.validate_workload(work, {'roi': None}, [b'first', b'second'])
            for config, frames in [({'roi': None}, [b'changed', b'second']),
                                   ({'roi': 'changed'}, [b'first', b'second'])]:
                with self.assertRaisesRegex(ValueError, 'new --work'):
                    BENCHMARK.validate_workload(work, config, frames)

    def test_unverifiable_existing_banks_are_not_blessed_with_a_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            bank = work / 'banks' / 'model'
            bank.mkdir(parents=True)
            (bank / 'baseline.npz').touch()
            with self.assertRaisesRegex(ValueError, 'no workload manifest'):
                BENCHMARK.validate_workload(work, {}, [b'frame'])
            self.assertFalse((work / 'workload.json').exists())
