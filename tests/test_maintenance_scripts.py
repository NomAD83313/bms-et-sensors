"""Run maintenance scripts against local command stubs, never Docker or Docker Hub."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER_STUB = '''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['STUB_LOG'],'a') as stream:stream.write(json.dumps(args)+'\\n')
if args[0]=='compose' and 'config' in args:
    if '--services' in args:
        if os.environ.get('STUB_FAIL_CONFIG')=='1':sys.exit(1)
        print('dashboard')
        if '--profile' in args and '*' in args:
            print('camera-media\\ncamera-analysis\\ncamera-hub\\nmatter-server\\nmesskluppe-collector')
    else:print(json.dumps({'name':'test-stack'}))
elif args[:2]==['ps','-a']:
    print('media camera-media\\nanalysis camera-analysis\\nhub camera-hub\\nmatter matter-server\\nradio messkluppe-collector\\nold obsolete-role')
'''
CURL_STUB = '''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:];url=next(arg for arg in args if arg.startswith('https://'))
with open(os.environ['STUB_LOG'],'a') as stream:stream.write(json.dumps(args)+'\\n')
if '/users/login/' in url:
    payload=json.load(sys.stdin)
    if payload['password']!=os.environ['DOCKERHUB_PASS']:sys.exit(1)
    print(json.dumps({'token':'fixture-token'}))
elif '-X' in args and args[args.index('-X')+1]=='DELETE':pass
elif 'page=2' in url:
    print('not-json' if os.environ.get('STUB_BAD_PAGE')=='1' else json.dumps({'results':[{'name':'old-b'}],'next':None}))
else:
    print(json.dumps({'results':[{'name':'old-a'},{'name':'latest'}],'next':'https://hub.docker.com/v2/repositories/test/repo/tags/?page=2&size=2'}))
'''


class MaintenanceScriptTests(unittest.TestCase):
    def run_stubbed(self, script, arguments=(), variables=None):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, source in [('docker', DOCKER_STUB), ('curl', CURL_STUB)]:
                path = base / name
                path.write_text(source)
                path.chmod(0o755)
            log = base / 'calls.jsonl'
            environment = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'],
                               STUB_LOG=str(log), DOCKERHUB_USER='fixture', DOCKERHUB_PASS='quote"slash\\',
                               DOCKERHUB_NS='test', DOCKERHUB_REPOS='repo', DRY_RUN='0')
            environment.update(variables or {})
            result = subprocess.run(['bash', str(ROOT / 'scripts' / script), *arguments],
                                    cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_orphan_cleanup_preserves_every_optional_service(self):
        result, calls = self.run_stubbed('clean-docker.sh', ['--orphans-only'])
        self.assertEqual(result.returncode, 0, result.stderr)
        removals = [call for call in calls if call[:2] == ['rm', '-f']]
        self.assertEqual(removals, [['rm', '-f', 'old']])
        self.assertFalse(any('down' in call for call in calls))

    def test_config_failure_aborts_before_any_removal(self):
        result, calls = self.run_stubbed('clean-docker.sh', ['--orphans-only'], {'STUB_FAIL_CONFIG': '1'})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == 'rm' or 'down' in call for call in calls))

    def test_full_cleanup_uses_all_profiles_and_never_prunes_global_images(self):
        result, calls = self.run_stubbed('clean-docker.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        down = next(call for call in calls if 'down' in call)
        self.assertIn('*', down)
        self.assertFalse(any(call[:2] == ['image', 'prune'] for call in calls))

    def test_default_workflows_do_not_build_or_start_optional_apps(self):
        for script in ['build-local-all.sh', 'restart-local.sh']:
            with self.subTest(script=script):
                result, calls = self.run_stubbed(script)
                self.assertEqual(result.returncode, 0, result.stderr)
                for call in calls:
                    self.assertFalse({'matter-collector', 'camera-media', 'messkluppe-collector'} & set(call))

    def test_hub_cleanup_reads_all_pages_before_deleting_every_unprotected_tag(self):
        result, calls = self.run_stubbed('cleanup-dockerhub-tags.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        deletes = [call for call in calls if 'DELETE' in call]
        self.assertEqual([call[-1].rsplit('/', 2)[1] for call in deletes], ['old-a', 'old-b'])
        page_two = next(i for i, call in enumerate(calls) if 'page=2' in call[-1])
        first_delete = next(i for i, call in enumerate(calls) if 'DELETE' in call)
        self.assertLess(page_two, first_delete)

    def test_malformed_hub_page_aborts_before_deleting_tags(self):
        result, calls = self.run_stubbed('cleanup-dockerhub-tags.sh', variables={'STUB_BAD_PAGE': '1'})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('DELETE' in call for call in calls))


ONNX_DOCKER_STUB = '''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['STUB_LOG'],'a') as stream:stream.write(json.dumps(args)+'\\n')
if args[:2]==['image','inspect']:sys.exit(1)
if args[0]=='pull' and os.environ.get('STUB_PULL_FAILS')=='1':sys.exit(1)
'''


class CameraOnnxRuntimeTests(unittest.TestCase):
    def ensure(self, machine, variables=None):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, source in [('docker', ONNX_DOCKER_STUB), ('uname', f'#!/bin/sh\necho {machine}\n')]:
                (base / name).write_text(source)
                (base / name).chmod(0o755)
            log = base / 'calls.jsonl'
            environment = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'], STUB_LOG=str(log),
                               CAMERA_ONNX_RUNTIME_IMAGE='bms-onnx-xnnpack:local')
            environment.pop('CAMERA_ONNX_PREBUILT_IMAGE', None)
            environment.update(variables or {})
            subprocess.run(['bash', '-c', 'SCRIPT_DIR=scripts; source scripts/lib/compose-common.sh; bms_ensure_camera_onnx_runtime'],
                           cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15, check=True)
            return [json.loads(line) for line in log.read_text().splitlines()]

    def test_fresh_arm64_host_pulls_the_published_image_instead_of_compiling(self):
        calls = self.ensure('aarch64')
        self.assertIn(['pull', 'nomad375/bms-onnx-xnnpack:ort-1.31.1-arm64'], calls)
        self.assertIn(['tag', 'nomad375/bms-onnx-xnnpack:ort-1.31.1-arm64', 'bms-onnx-xnnpack:local'], calls)
        self.assertFalse(any(call[0] == 'build' for call in calls))

    def test_failed_pull_falls_back_to_a_local_compile(self):
        calls = self.ensure('aarch64', {'STUB_PULL_FAILS': '1'})
        self.assertEqual(calls[-1][:3], ['build', '-f', 'Dockerfile.camera-onnx-xnnpack'])

    def test_rebuild_and_non_arm_hosts_compile_without_pulling(self):
        for machine, variables in [('aarch64', {'CAMERA_ONNX_PREBUILT_IMAGE': ''}), ('x86_64', None)]:
            calls = self.ensure(machine, variables)
            self.assertFalse(any(call[0] == 'pull' for call in calls), machine)
            self.assertEqual(calls[-1][0], 'build')


IMAGE_SOURCE_DOCKER_STUB = '''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['STUB_LOG'],'a') as stream:stream.write(json.dumps(args)+'\\n')
if args[0]=='compose' and 'pull' in args and args[-1]==os.environ.get('STUB_UNPUBLISHED'):sys.exit(1)
'''


class ImageSourceTests(unittest.TestCase):
    def build(self, services, image_source, machine='aarch64', variables=None):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, source in [('docker', IMAGE_SOURCE_DOCKER_STUB), ('uname', f'#!/bin/sh\necho {machine}\n')]:
                (base / name).write_text(source)
                (base / name).chmod(0o755)
            log = base / 'calls.jsonl'
            environment = dict(os.environ, PATH=str(base) + os.pathsep + os.environ['PATH'], STUB_LOG=str(log),
                               BMS_IMAGE_SOURCE=image_source)
            environment.update(variables or {})
            result = subprocess.run(['bash', '-c', 'SCRIPT_DIR=scripts; source scripts/lib/compose-common.sh; '
                                     'bms_build_services "$@"', 'test', *services],
                                    cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    @staticmethod
    def compose_action(calls, action):
        return [call for call in calls if call[0] == 'compose' and action in call]

    def test_build_mode_compiles_every_service(self):
        result, calls = self.build(['graf-lite', 'mscl-collector'], 'build')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.compose_action(calls, 'pull'), [])
        self.assertEqual(self.compose_action(calls, 'build')[0][-2:], ['graf-lite', 'mscl-collector'])

    def test_pull_mode_downloads_and_builds_only_unpublished_services(self):
        result, calls = self.build(['--profile', 'cameras', 'camera-media', 'graf-lite', 'vibration-collector'],
                                   'pull', variables={'STUB_UNPUBLISHED': 'vibration-collector'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['pull', 'nomad375/bms-camera-media:latest'], calls)
        self.assertIn(['tag', 'nomad375/bms-camera-media:latest', 'bms-cameras:local'], calls)
        pulled = [call[-1] for call in self.compose_action(calls, 'pull')]
        self.assertEqual(pulled, ['graf-lite', 'vibration-collector'])
        builds = self.compose_action(calls, 'build')
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0][-1], 'vibration-collector')
        self.assertIn('--profile', builds[0])

    def test_pull_mode_builds_on_hosts_that_are_not_arm64(self):
        result, calls = self.build(['graf-lite'], 'pull', machine='x86_64')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('arm64', result.stderr)
        self.assertEqual(self.compose_action(calls, 'pull'), [])
        self.assertEqual(self.compose_action(calls, 'build')[0][-1], 'graf-lite')

    def test_unknown_image_source_stops_before_any_docker_call(self):
        result, calls = self.build(['graf-lite'], 'hub')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('build or pull', result.stderr)
        self.assertEqual(calls, [])
