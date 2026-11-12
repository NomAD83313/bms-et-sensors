"""Shared analysis service: camera list validation, scheduling and per-camera isolation."""
import json
import logging
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from app.cameras import anomaly_control, control
from tests.isolated_import import load_isolated_module
from tests.test_cameras_worker_quality import FakeModelGeometry


def load_service(worker_module=None):
    """The service module with the (flat-import) worker replaced by a stub or a real isolated copy."""
    if worker_module is None:
        worker_module = types.ModuleType('anomaly_worker')
        worker_module.AnomalyWorker = Mock()
        worker_module.WorkerLocked = type('WorkerLocked', (RuntimeError,), {})
    return load_isolated_module('app.cameras.analysis_service', {'anomaly_worker': worker_module})


def camera(identifier='pi', **extra):
    return dict(id=identifier, data=f'/anomaly/{identifier}', control=f'/control/{identifier}',
                preview='http://127.0.0.1:8083/api/live.jpg', **extra)


class CameraListTests(unittest.TestCase):
    def setUp(self):
        self.service = load_service()

    def test_valid_lists_are_accepted_from_text_or_data(self):
        listed = [camera('pi', recording_mode='anomaly', settings=dict(interval_sec=.3, model=dict(top_k=5))),
                  camera('wifi')]
        self.assertEqual(self.service.parse_cameras(json.dumps(listed)), listed)
        self.assertEqual(self.service.parse_cameras(listed), listed)

    def test_invalid_lists_are_rejected_with_a_readable_reason(self):
        bad = {
            'not json': '{',
            'empty': [],
            'not a list': dict(id='pi'),
            'not an object': ['pi'],
            'upper case id': [camera('Pi')],
            'long id': [camera('a' * 17)],
            'empty id': [camera('')],
            'duplicate id': [camera('pi'), camera('pi')],
            'missing data': [{k: v for k, v in camera().items() if k != 'data'}],
            'remote preview': [dict(camera(), preview='http://example.com/api/live.jpg')],
            'settings not an object': [camera(settings=[])],
        }
        for name, value in bad.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.service.parse_cameras(value)

    def test_the_list_comes_from_a_file_or_the_environment(self):
        listed = [camera('pi')]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cameras.json'
            path.write_text(json.dumps(listed))
            self.assertEqual(self.service.load_cameras({'CAMERA_ANALYSIS_CONFIG': str(path)}), listed)
        self.assertEqual(self.service.load_cameras({'CAMERA_ANALYSIS_CAMERAS': json.dumps(listed)}), listed)
        with self.assertRaises(ValueError):
            self.service.load_cameras({})


    def test_cameras_generated_from_the_registry_are_appended_and_cannot_reuse_an_id(self):
        base = [camera('pi')]
        with tempfile.TemporaryDirectory() as directory:
            extra = Path(directory) / 'analysis-extra.json'
            environment = {'CAMERA_ANALYSIS_CAMERAS': json.dumps(base), 'CAMERA_ANALYSIS_EXTRA_FILE': str(extra)}
            self.assertEqual(self.service.load_cameras(environment), base, 'A missing file adds nothing')
            extra.write_text('[]')
            self.assertEqual(self.service.load_cameras(environment), base, 'An empty list adds nothing')
            extra.write_text(json.dumps([camera('garage')]))
            self.assertEqual([c['id'] for c in self.service.load_cameras(environment)], ['pi', 'garage'])
            extra.write_text(json.dumps([camera('pi')]))
            with self.assertRaises(ValueError):
                self.service.load_cameras(environment)
            extra.write_text('{')
            with self.assertRaises(ValueError):
                self.service.load_cameras(environment)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.service = load_service()
        self.now = [0.0]
        self.steps = []

    def worker(self, name, wait=.2, duration=0.0, state='normal', fail=False):
        def step():
            self.steps.append((name, round(self.now[0], 3)))
            self.now[0] += duration
            if fail:
                raise RuntimeError('boom')
            return wait
        return types.SimpleNamespace(step=step, state=dict(state=state))

    def scheduler(self, **workers):
        return self.service.Scheduler(workers, clock=lambda: self.now[0], sleep=Mock())

    def test_each_camera_is_stepped_when_due_and_the_sleep_reaches_the_next_one(self):
        scheduler = self.scheduler(a=self.worker('a', wait=.2, duration=.1), b=self.worker('b', wait=.1, duration=.05))
        sleep = scheduler.run_once()
        self.assertEqual(self.steps, [('a', 0.0), ('b', 0.1)])
        self.assertAlmostEqual(self.now[0], .15)
        # a is due at .1 + .2 = .3, b at .15 + .1 = .25
        self.assertAlmostEqual(sleep, .1, places=6)
        self.now[0] += sleep
        scheduler.run_once()
        self.assertEqual(self.steps[2][0], 'b')

    def test_an_unavailable_camera_is_retried_slowly_and_does_not_block_the_others(self):
        scheduler = self.scheduler(down=self.worker('down', wait=.2, state='unavailable'), up=self.worker('up', wait=.2))
        scheduler.run_once()
        self.now[0] += .2
        scheduler.run_once()
        self.assertEqual([name for name, _ in self.steps], ['down', 'up', 'up'])
        self.now[0] = self.service.OFFLINE_RETRY_SECONDS + .01
        scheduler.run_once()
        self.assertIn('down', [name for name, _ in self.steps[3:]])

    def test_a_failing_camera_is_isolated_and_retried_later(self):
        scheduler = self.scheduler(bad=self.worker('bad', fail=True), good=self.worker('good', wait=.2))
        with self.assertLogs('camera-analysis', level='ERROR'):
            sleep = scheduler.run_once()
        self.assertEqual([name for name, _ in self.steps], ['bad', 'good'])
        self.assertAlmostEqual(sleep, .2)
        self.now[0] += .2
        scheduler.run_once()
        self.assertEqual([name for name, _ in self.steps], ['bad', 'good', 'good'])

    def test_an_empty_scheduler_waits_and_picks_up_cameras_that_become_free(self):
        started = []

        def factory(**kwargs):
            started.append(kwargs['name'])
            return self.worker(kwargs['name'], wait=.2)

        pending = [camera('late')]
        scheduler = self.service.Scheduler({}, pending=pending, factory=factory, clock=lambda: self.now[0], sleep=Mock())
        self.assertEqual(scheduler.run_once(), self.service.START_RETRY_SECONDS)
        self.assertEqual(started, [])
        self.now[0] += self.service.START_RETRY_SECONDS + .1
        scheduler.run_once()
        self.assertEqual(started, ['late'])
        self.assertEqual([name for name, _ in self.steps], ['late'])
        self.assertEqual(scheduler.pending, [])

    def test_a_camera_that_stays_locked_is_retried_every_interval_not_every_round(self):
        attempts = []

        def factory(**kwargs):
            attempts.append(self.now[0])
            raise self.service.WorkerLocked('owned elsewhere')

        scheduler = self.service.Scheduler({}, pending=[camera('busy')], factory=factory,
                                           clock=lambda: self.now[0], sleep=Mock())
        for _ in range(3):
            self.now[0] += 4
            scheduler.run_once()
        self.assertEqual(len(attempts), 1, 'Retries are spaced by START_RETRY_SECONDS')
        self.assertEqual([entry['id'] for entry in scheduler.pending], ['busy'])

    def test_run_sleeps_between_rounds(self):
        scheduler = self.scheduler(a=self.worker('a', wait=.2))
        scheduler.sleep = Mock(side_effect=KeyboardInterrupt)
        with patch.object(self.service, 'ALIVE_FILE', Path(tempfile.gettempdir()) / 'test-camera-analysis.alive'):
            with self.assertRaises(KeyboardInterrupt):
                scheduler.run()
        scheduler.sleep.assert_called_once_with(.2)


class WorkerSettingsAndLockTests(unittest.TestCase):
    def module(self, root, created):
        class Model(FakeModelGeometry):
            memory = True
            baseline_frames = 60
            threshold = 1

            def __init__(self, **options):
                created.append(options)

            def load(self, path):
                self.memory = True

            def set_roi(self, roi):
                self.roi = roi

        module = types.ModuleType('patchcore_lite')
        module.PatchCoreLite, module.overlay = Model, Mock()
        with patch.object(anomaly_control, 'DATA', root / 'default'), patch.object(control, 'CONTROL', root / 'ctl'):
            return load_isolated_module('app.cameras.anomaly_worker', {
                'control': control, 'anomaly_control': anomaly_control, 'patchcore_lite': module})

    def test_per_camera_settings_override_the_environment_and_reach_the_model(self):
        created = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, created)
            with patch.dict(os.environ, {'CAMERA_ANOMALY_INTERVAL_SEC': '.5', 'CAMERA_ANOMALY_CONFIRM_FRAMES': '3'}):
                plain = module.AnomalyWorker(data=root / 'plain')
                tuned = module.AnomalyWorker(data=root / 'tuned', settings=dict(
                    interval_sec=.3, confirm_frames=1, normal_frames=40, model=dict(top_k=5, jpeg_draft=1)))
            self.assertEqual((plain.interval, plain.confirmation_frames), (.5, 3))
            self.assertEqual((tuned.interval, tuned.confirmation_frames, tuned.sample_count), (.3, 1, 40))
            self.assertEqual(created, [{}, dict(top_k=5, jpeg_draft=1)])

    def test_workers_log_under_their_camera_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, [])
            named = module.AnomalyWorker(data=root / 'x', name='pi')
            self.assertEqual(named.log.name, 'camera-anomaly.pi')
            self.assertEqual(module.AnomalyWorker(data=root / 'y').log.name, 'camera-anomaly')

    def test_a_camera_that_stays_unreachable_is_logged_once_a_minute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, [])
            worker = module.AnomalyWorker(data=root / 'cam', name='cam')
            clock = [1000.0]
            error = OSError('refused')
            with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]), \
                    self.assertLogs('camera-anomaly.cam', level='WARNING') as logs:
                for _ in range(5):
                    worker.handle_error(error)
                    clock[0] += 2
                self.assertEqual(len(logs.records), 1, 'Repeats within a minute are not logged again')
                clock[0] += module.ERROR_LOG_INTERVAL_SECONDS
                worker.handle_error(error)
                worker.handle_error(ValueError('different'))
            self.assertEqual(len(logs.records), 3, 'A new minute or a different error is logged')
            self.assertEqual(worker.state['state'], 'unavailable')

    def test_an_exclusive_worker_refuses_a_camera_directory_that_is_already_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, [])
            first = module.AnomalyWorker(data=root / 'cam', exclusive=True)
            with self.assertRaises(module.WorkerLocked):
                module.AnomalyWorker(data=root / 'cam', exclusive=True)
            other = module.AnomalyWorker(data=root / 'other', exclusive=True)
            module.AnomalyWorker(data=root / 'cam')  # not exclusive: used by tests and tools
            first.close()
            again = module.AnomalyWorker(data=root / 'cam', exclusive=True)
            again.close()
            other.close()

    def test_a_per_camera_container_waits_for_the_lock_instead_of_exiting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, [])
            holder = module.AnomalyWorker(data=root / 'default', exclusive=True)

            class Stop(BaseException):
                pass

            with patch.object(module, 'DATA', root / 'default'), \
                    patch.object(module.time, 'sleep', side_effect=Stop) as sleep, \
                    self.assertLogs('camera-anomaly', level='WARNING') as logs:
                with self.assertRaises(Stop):
                    module.main()
            sleep.assert_called_once_with(module.LOCK_RETRY_SECONDS)
            self.assertIn('waiting for it to stop', logs.output[0])
            holder.close()

    def test_build_workers_skips_locked_or_broken_cameras_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.module(root, [])
            cameras = [dict(camera(name), data=str(root / name), control=str(root / name / 'c'))
                       for name in ('one', 'two', 'three')]
            held = module.AnomalyWorker(data=root / 'two', exclusive=True)

            def factory(**kwargs):
                if kwargs['name'] == 'three':
                    raise RuntimeError('model missing')
                return module.AnomalyWorker(**kwargs)

            service = load_service(module)
            with self.assertLogs('camera-analysis', level='WARNING') as logs:
                workers, failed = service.build_workers(cameras, factory=factory)
            self.assertEqual(sorted(workers), ['one'])
            self.assertEqual([entry['id'] for entry in failed], ['two', 'three'])
            self.assertEqual(len(logs.records), 2)
            held.close()
            workers['one'].close()


if __name__ == '__main__':
    unittest.main()
