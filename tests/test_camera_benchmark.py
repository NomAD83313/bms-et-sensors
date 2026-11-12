"""The camera stack is measured as a whole: comparable CPU and memory totals per sample."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'camera_benchmark', Path(__file__).resolve().parents[1] / 'scripts/benchmark-cameras.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_summary_sums_simultaneous_components(self):
        rows = [dict(component=name, sample=sample, cpu_percent=cpu, rss_mib=memory)
                for name, sample, cpu, memory in [('camera-media', 0, 10, 20), ('camera-analysis', 0, 30, 40),
                                                  ('camera-media', 1, 50, 60), ('camera-analysis', 1, 5, 10)]]
        result = benchmark.summarize(rows)
        self.assertEqual(result['cpu_mean_percent'], 47.5)
        self.assertEqual(result['cpu_peak_percent'], 55)
        self.assertEqual(result['rss_mean_mib'], 65)

    def test_required_services_must_run_and_the_relay_is_optional(self):
        def inspect(args, **_):
            state = {'camera-media': 'true 11', 'camera-analysis': 'true 12'}.get(args[-1], '')
            return subprocess.CompletedProcess(args, 0 if state else 1, stdout=state)

        with patch.object(benchmark.subprocess, 'run', side_effect=inspect), \
                patch.object(benchmark, 'cgroup_of', side_effect=lambda pid: Path(f'/cg/{pid}')), \
                patch.object(benchmark, 'run') as host:
            targets = benchmark.discover(include_pi_source=False)
        self.assertEqual(targets, [('camera-media', Path('/cg/11')), ('camera-analysis', Path('/cg/12'))])
        host.assert_not_called()

        def stopped(args, **_):
            return subprocess.CompletedProcess(args, 0, stdout='false 0')

        with patch.object(benchmark.subprocess, 'run', side_effect=stopped), self.assertRaises(RuntimeError):
            benchmark.discover(include_pi_source=False)


if __name__ == '__main__':
    unittest.main()
