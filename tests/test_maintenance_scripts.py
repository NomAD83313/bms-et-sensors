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
