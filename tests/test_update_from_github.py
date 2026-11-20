"""scripts/update-from-github.sh against a local 'origin', without network, Docker or restarts."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'update-from-github.sh'
GIT = ['git', '-c', 'user.name=test', '-c', 'user.email=test@example.invalid']


def run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True, **kwargs)


class UpdateFromGitHubTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.origin, self.host = base / 'origin.git', base / 'host'
        source = base / 'source'
        # A minimal project: the script, its shared helpers and the files it reads.
        (source / 'scripts' / 'lib').mkdir(parents=True)
        for name in ('update-from-github.sh', 'lib/compose-common.sh', 'lib/setup-common.sh'):
            (source / 'scripts' / name).write_bytes((ROOT / 'scripts' / name).read_bytes())
        (source / 'scripts' / 'update-from-github.sh').chmod(0o755)
        (source / '.gitignore').write_text('.env\nruntime/*\n')
        (source / '.env.example').write_text('INFLUX_TOKEN=\nNEW_OPTIONAL=1\n')
        (source / 'docker-compose.yml').write_text('services: {x: {environment: [T=${INFLUX_TOKEN}, O=${NEW_OPTIONAL:-1}]}}\n')
        run(['git', 'init', '-q', '-b', 'main'], source)
        run(GIT + ['add', '-A'], source)
        run(GIT + ['commit', '-qm', 'old'], source)
        run(['git', 'clone', '-q', '--bare', str(source), str(self.origin)], base)
        run(['git', 'clone', '-q', str(self.origin), str(self.host)], base)
        # GitHub moves on: a new commit on top of 'old'.
        (source / 'README.md').write_text('new\n')
        run(GIT + ['add', '-A'], source)
        run(GIT + ['commit', '-qm', 'new'], source)
        run(['git', 'push', '-q', str(self.origin), 'main'], source)
        self.remote_head = run(['git', 'rev-parse', 'HEAD'], source).stdout.strip()

    def update(self, *args, check=True):
        environment = dict(os.environ, PATH=f"{self.directory.name}/bin:{os.environ['PATH']}")
        return subprocess.run([str(self.host / 'scripts' / 'update-from-github.sh'), *args], cwd=self.host,
                              capture_output=True, text=True, check=check, env=environment)

    def head(self):
        return run(['git', 'rev-parse', 'HEAD'], self.host).stdout.strip()

    def test_check_reports_without_changing_anything(self):
        (self.host / '.env').write_text('INFLUX_TOKEN=secret\nOLD_KEY=1\n')
        before = self.head()
        output = self.update('--check').stdout
        self.assertIn('Update: fast-forward', output)
        self.assertIn('optional keys', output)
        self.assertIn('NEW_OPTIONAL', output)
        self.assertIn('OLD_KEY', output)
        self.assertEqual(self.head(), before)
        self.assertFalse((self.host / 'runtime' / 'backups').exists() and any((self.host / 'runtime' / 'backups').iterdir()))

    def test_diverged_history_is_replaced_after_a_backup_and_local_files_survive(self):
        (self.host / 'local.txt').write_text('mine\n')
        run(GIT + ['commit', '-qam', 'local only', '--allow-empty'], self.host)
        with open(self.host / 'docker-compose.yml', 'a') as stream:
            stream.write('# edited on the device\n')
        (self.host / '.env').write_text('INFLUX_TOKEN=secret\n')
        (self.host / 'notes.txt').write_text('keep me\n')
        old_head = self.head()
        output = self.update('--no-restart').stdout
        self.assertIn('Update: replace', output)
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual((self.host / '.env').read_text(), 'INFLUX_TOKEN=secret\n')
        self.assertEqual((self.host / 'notes.txt').read_text(), 'keep me\n')
        backup = next((self.host / 'runtime' / 'backups').iterdir())
        self.assertEqual(oct(backup.stat().st_mode & 0o777), '0o700')
        self.assertEqual((backup / 'env').read_text(), 'INFLUX_TOKEN=secret\n')
        self.assertIn('edited on the device', (backup / 'local-changes.patch').read_text())
        self.assertTrue((backup / 'previous-commit.txt').read_text().startswith(old_head))
        self.assertIn(f'git reset --hard {old_head[:12]}', output)

    def test_missing_required_keys_stop_the_update_before_anything_changes(self):
        (self.host / '.env').write_text('OTHER=1\n')
        before = self.head()
        result = self.update(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('REQUIRED', result.stderr)
        self.assertIn('INFLUX_TOKEN', result.stderr)
        self.assertEqual(self.head(), before)

    def test_missing_env_stops_restart_but_allows_check(self):
        before = self.head()
        check = self.update('--check')
        self.assertIn('.env is missing', check.stderr)
        self.assertEqual(self.head(), before)

        result = self.update(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('create it from .env.example', result.stderr)
        self.assertEqual(self.head(), before)

    def test_fast_forward_replaces_backed_up_tracked_edits(self):
        (self.host / '.env').write_text('INFLUX_TOKEN=secret\n')
        with open(self.host / 'docker-compose.yml', 'a') as stream:
            stream.write('# local edit\n')

        self.update('--no-restart')

        self.assertEqual(self.head(), self.remote_head)
        self.assertNotIn('local edit', (self.host / 'docker-compose.yml').read_text())
        backup = next((self.host / 'runtime' / 'backups').iterdir())
        self.assertIn('local edit', (backup / 'local-changes.patch').read_text())

    def test_detached_update_moves_existing_main_to_remote_head(self):
        (self.host / '.env').write_text('INFLUX_TOKEN=secret\n')
        run(['git', 'checkout', '-q', '--detach', 'HEAD'], self.host)

        self.update('--no-restart')

        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual(run(['git', 'branch', '--show-current'], self.host).stdout.strip(), 'main')
        self.assertEqual(run(['git', 'rev-parse', 'main'], self.host).stdout.strip(), self.remote_head)


    def prepare_repo(self):
        script = ('source scripts/lib/setup-common.sh; RETRY_ATTEMPTS=1; RETRY_DELAY_SEC=0; '
                  f'PROJECT_DIR={self.host}; REPO_BRANCH=main; prepare_repo')
        return subprocess.run(['bash', '-c', script], cwd=self.host, capture_output=True, text=True, check=True)

    def test_setup_fast_forwards_a_clean_checkout(self):
        output = self.prepare_repo().stdout
        self.assertNotIn('running the updater', output)
        self.assertEqual(self.head(), self.remote_head)

    def test_setup_hands_a_squashed_history_to_the_updater(self):
        # rpi-setup.sh on a host cloned before the GitHub history was rewritten.
        (self.host / '.env').write_text('INFLUX_TOKEN=secret\n')
        run(GIT + ['commit', '-qm', 'pre-squash', '--allow-empty'], self.host)
        output = self.prepare_repo().stdout
        self.assertIn('running the updater', output)
        self.assertIn('Update: replace', output)
        self.assertEqual(self.head(), self.remote_head)
        self.assertEqual((self.host / '.env').read_text(), 'INFLUX_TOKEN=secret\n')
        self.assertTrue(any((self.host / 'runtime' / 'backups').iterdir()))


if __name__ == '__main__':
    unittest.main()
