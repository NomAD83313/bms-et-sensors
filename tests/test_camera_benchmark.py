"""Camera selection and aggregation must preserve comparable CPU totals."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'camera_benchmark', Path(__file__).resolve().parents[1] / 'scripts/benchmark-cameras.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_single_camera_summary_sums_simultaneous_components(self):
        rows = [dict(camera='pi', sample=sample, cpu_percent=cpu, rss_mib=memory)
                for sample, cpu, memory in [(0, 10, 20), (0, 30, 40), (1, 50, 60), (1, 5, 10)]]
        result = benchmark.summarize(rows)
        self.assertEqual(set(result), {'pi'})
        self.assertEqual(result['pi']['cpu_mean_percent'], 47.5)
        self.assertEqual(result['pi']['cpu_peak_percent'], 55)
        self.assertEqual(result['pi']['rss_mean_mib'], 65)

    def test_wifi_selection_never_queries_pi_or_systemd(self):
        with patch.object(benchmark, 'run', side_effect=['container', '123'] * 3) as run, \
                patch.object(Path, 'read_text', return_value='0::/example\n'):
            targets = benchmark.discover(['wifi'])
        self.assertEqual([target[1] for target in targets], benchmark.SERVICES['wifi'])
        self.assertTrue(all(call.args[0] == 'docker' for call in run.call_args_list))
