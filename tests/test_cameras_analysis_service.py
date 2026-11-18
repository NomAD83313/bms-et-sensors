"""Shared analysis service: camera list validation, scheduling and per-camera isolation."""
import json
from concurrent.futures import Future
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
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


class InlineExecutor:
    """Deterministic execution for the scheduler's deadline and retry tests."""
    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self, **kwargs):
        pass


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
        return self.service.Scheduler(workers, clock=lambda: self.now[0], sleep=Mock(), executor=InlineExecutor())

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
        scheduler = self.service.Scheduler({}, pending=pending, factory=factory, clock=lambda: self.now[0],
                                           sleep=Mock(), executor=InlineExecutor())
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
                                           clock=lambda: self.now[0], sleep=Mock(), executor=InlineExecutor())
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


class ConcurrentSchedulerTests(unittest.TestCase):
    def test_blocked_training_or_network_step_does_not_stall_or_overlap_cameras(self):
        for state in ('training', 'unavailable'):
            with self.subTest(state=state):
                service = load_service()
                entered, release, healthy_ran = threading.Event(), threading.Event(), threading.Event()
                calls = []
                healthy_threads = set()

                def blocked_step():
                    calls.append('blocked')
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError('Test did not release the camera')
                    return .2

                def healthy_step():
                    calls.append('healthy')
                    healthy_threads.add(threading.get_ident())
                    healthy_ran.set()
                    return .01

                scheduler = service.Scheduler(dict(
                    blocked=types.SimpleNamespace(step=blocked_step, state={'state': state}),
                    healthy=types.SimpleNamespace(step=healthy_step, state={'state': 'normal'})))
                try:
                    scheduler.run_once()
                    self.assertTrue(entered.wait(3))
                    self.assertTrue(healthy_ran.wait(3), 'A blocked camera must not hold the healthy one')
                    for _ in range(3):
                        healthy_ran.clear()
                        deadline = time.monotonic() + 3
                        while not healthy_ran.is_set() and time.monotonic() < deadline:
                            scheduler.run_once()
                            healthy_ran.wait(.01)
                        self.assertTrue(healthy_ran.is_set())
                    self.assertEqual(calls.count('blocked'), 1, 'A camera must never have overlapping steps')
                    self.assertEqual(len(healthy_threads), 1, 'Reuse the camera thread and its native allocator cache')
                finally:
                    release.set()
                    scheduler.close()

    def test_slow_pending_initialization_does_not_block_active_cameras(self):
        service = load_service()
        entered, release, healthy_ran = threading.Event(), threading.Event(), threading.Event()

        def factory(**kwargs):
            entered.set()
            release.wait(5)
            return types.SimpleNamespace(step=lambda: .2, state={'state': 'normal'})

        scheduler = service.Scheduler(dict(healthy=types.SimpleNamespace(
            step=lambda: healthy_ran.set() or .2, state={'state': 'normal'})),
            pending=[camera('late')], factory=factory)
        scheduler.next_start = 0
        try:
            scheduler.run_once()
            self.assertTrue(entered.wait(3))
            self.assertTrue(healthy_ran.wait(3))
        finally:
            release.set()
            scheduler.close()


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


class RegistryReloadTests(unittest.TestCase):
    def test_delete_waits_for_current_frame_then_releases_only_that_worker(self):
        service = load_service()
        removed = types.SimpleNamespace(step=Mock(return_value=.2), state={'state': 'normal'}, close=Mock())
        kept = types.SimpleNamespace(step=Mock(return_value=.2), state={'state': 'learning'}, close=Mock())
        entries = [camera('second')]
        future = Future()
        scheduler = service.Scheduler({'first': removed, 'second': kept}, executor=InlineExecutor(),
                                      clock=lambda: 0, camera_loader=lambda: entries)
        scheduler.in_flight['first'] = future
        scheduler.run_once()
        removed.close.assert_not_called()
        removed.step.assert_not_called()
        kept.step.assert_called_once()
        future.set_result((.2, 0))
        scheduler.run_once()
        removed.close.assert_called_once()
        kept.close.assert_not_called()
        self.assertNotIn('first', scheduler.workers)
        self.assertNotIn('first', scheduler.known_ids)
        self.assertNotIn('first', scheduler.due)

    def test_delete_during_initialization_releases_the_new_worker(self):
        service = load_service()
        created = types.SimpleNamespace(close=Mock())
        future = Future()
        scheduler = service.Scheduler({}, [camera('first')], executor=InlineExecutor(),
                                      camera_loader=lambda: [])
        scheduler.starting = future
        future.set_result(({'first': created}, [camera('second')]))
        scheduler.run_once()
        created.close.assert_called_once()
        self.assertEqual(scheduler.workers, {})
        self.assertEqual(scheduler.pending, [])

    def test_adding_camera_keeps_the_existing_worker_and_learning_state(self):
        service = load_service()
        original = types.SimpleNamespace(step=Mock(return_value=.2), state={'state':'learning'})
        created = types.SimpleNamespace(step=Mock(return_value=.2), state={'state':'untrained'})
        entries = [camera('pi')]
        now = [0.]
        factory = Mock(return_value=created)
        scheduler = service.Scheduler({'pi':original}, factory=factory, clock=lambda:now[0],
                                      executor=InlineExecutor(), camera_loader=lambda:entries)
        scheduler.run_once()
        entries.append(camera('new-camera'))
        now[0] = 2.1
        scheduler.run_once()
        self.assertIs(scheduler.workers['pi'],original)
        self.assertIs(scheduler.workers['new-camera'],created)
        self.assertEqual(original.state['state'],'learning')
        factory.assert_called_once()

    def test_bad_registry_does_not_stop_active_workers(self):
        service = load_service()
        worker = types.SimpleNamespace(step=Mock(return_value=.2), state={'state':'normal'})
        loader = Mock(side_effect=ValueError('incomplete write'))
        scheduler = service.Scheduler({'pi':worker}, executor=InlineExecutor(), camera_loader=loader)
        with self.assertLogs('camera-analysis',level='WARNING'):
            scheduler.run_once()
        worker.step.assert_called_once()
