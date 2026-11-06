"""Review and reverse consumer changes through the public CLIs."""

import json
import subprocess
import sys
import unittest

import test_sync


class ReverseTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp
    git = test_sync.SyncTests.git
    commit = test_sync.SyncTests.commit
    sync = test_sync.SyncTests.sync
    snapshot = test_sync.SyncTests.snapshot

    def reverse(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.source / 'tools/reverse.py'),
                               str(self.consumer), *args], env=self.env,
                              capture_output=True, text=True, encoding='utf-8')

    def test_preview_and_selected_common_change(self):
        self.assertEqual(self.sync().returncode, 0)
        first = 'docs/agents/domain.md'
        second = 'docs/agents/triage-labels.md'
        (self.consumer / first).write_bytes(b'Common improvement\n')
        (self.consumer / second).write_bytes(b'Unselected change\n')
        before = self.snapshot(self.source)
        result = self.reverse()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Common improvement', result.stdout)
        self.assertIn(second, result.stdout)
        self.assertEqual(self.snapshot(self.source), before)
        result = self.reverse('--common', first, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.source / 'common' / first).read_bytes(), b'Common improvement\n')
        self.assertEqual((self.source / 'common' / second).read_bytes(), before['common/' + second])


    def deploy(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        branch = self.git(self.consumer, 'branch', '--show-current').stdout.decode().strip()
        self.git(self.consumer, 'switch', '-q', 'main')
        self.git(self.consumer, 'merge', '--ff-only', branch)
        self.git(self.consumer, 'branch', '-d', branch)
        self.git(self.consumer, 'push', '-q')
        self.git(self.consumer, 'config', 'core.hooksPath', '.githooks')

    def check_drift(self):
        return subprocess.run([sys.executable, '-B', str(self.consumer / '.githooks/check_drift.py')],
                              cwd=self.consumer.resolve(), env=self.env, capture_output=True)

    def redeploy(self):
        # A documented one-command bypass permits the intermediate drift commit.
        self.git(self.consumer, 'add', '.')
        subprocess.run(['git', '-C', str(self.consumer), 'commit', '-qm', 'intermediate'],
                       env=dict(self.env, WORKFLOW_SKIP_DRIFT_ONCE='1'), check=True, capture_output=True)
        self.git(self.consumer, 'push', '-q')
        preview = self.sync('--force', '--dry-run')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.check_drift()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git(self.consumer, 'commit', '--allow-empty', '-qm', 'normal gates pass')

    def test_common_change_redeployment_restores_normal_checks(self):
        self.deploy()
        path = 'docs/agents/domain.md'
        (self.consumer / path).write_bytes(b'Common improvement\n')
        self.assertNotEqual(self.check_drift().returncode, 0)
        result = self.reverse('--common', path, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.commit(self.source)
        self.redeploy()
        self.assertEqual((self.consumer / path).read_bytes(), b'Common improvement\n')

    def test_generated_settings_split_and_redeployment(self):
        self.deploy()
        generated = self.consumer / '.claude/settings.json'
        desired = json.loads(generated.read_bytes())
        desired['env']['SHARED'] = 'all'
        desired['env']['LOCAL'] = 'one'
        generated.write_bytes((json.dumps(desired, indent=2, sort_keys=True) + '\n').encode())
        common = json.loads((self.source / 'common/.claude/settings.common.json').read_bytes())
        common['env'] = {'SHARED': 'all'}
        common_input = self.root / 'common.json'
        common_input.write_text(json.dumps(common), encoding='utf-8')
        project_input = self.root / 'project.json'
        project_input.write_text('{"env":{"PROJECT":"yes","LOCAL":"one"}}', encoding='utf-8')
        record = (self.consumer / test_sync.RECORD).read_bytes()
        result = self.reverse('--common', '.claude/settings.json', '--apply')
        self.assertNotEqual(result.returncode, 0)
        result = self.reverse('--settings-common', str(common_input),
                              '--settings-project', str(project_input), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.consumer / test_sync.RECORD).read_bytes(), record)
        self.assertEqual(json.loads((self.source / 'common/.claude/settings.common.json').read_bytes())['env'],
                         {'SHARED': 'all'})
        self.commit(self.source)
        self.redeploy()
        self.assertEqual(json.loads(generated.read_bytes())['env'],
                         {'SHARED': 'all', 'PROJECT': 'yes', 'LOCAL': 'one'})

    def test_project_hook_extension_survives_redeployment(self):
        self.deploy()
        path = '.githooks/pre-commit'
        with (self.consumer / path).open('ab') as stream:
            stream.write(b'\necho project-specific >&2\n')
        payload = self.root / 'extension.sh'
        payload.write_bytes(b'echo project-specific >&2\n')
        target = '.githooks/pre-commit.d/20-project'
        result = self.reverse('--project', path, target, str(payload), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.redeploy()
        result = self.git(self.consumer, 'commit', '--allow-empty', '-qm', 'extension runs')
        self.assertIn(b'project-specific', result.stderr)

    def test_duplicate_project_targets_are_rejected_before_any_write(self):
        self.deploy()
        paths = ['docs/agents/domain.md', 'docs/agents/triage-labels.md']
        for path in paths:
            (self.consumer / path).write_bytes(b'Changed\n')
        payload = self.root / 'payload.md'
        payload.write_bytes(b'Project addition\n')
        before = self.snapshot(self.consumer)
        result = self.reverse('--project', paths[0], 'docs/project.md', str(payload),
                              '--project', paths[1], 'docs/project.md', str(payload), '--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(self.consumer), before)

    def test_invalid_selections_and_settings_preserve_owner_files(self):
        self.deploy()
        path = 'docs/agents/domain.md'
        (self.consumer / path).write_bytes(b'Consumer change\n')
        generated = self.consumer / '.claude/settings.json'
        desired = json.loads(generated.read_bytes())
        desired['env']['NEW'] = 'value'
        generated.write_bytes((json.dumps(desired, indent=2, sort_keys=True) + '\n').encode())
        payload = self.root / 'input.json'
        payload.write_bytes(b'{}\n')
        before_source = self.snapshot(self.source)
        before_consumer = self.snapshot(self.consumer)
        for options in [('--apply',), ('--common', '../outside', '--apply'),
                        ('--project', path, '../outside', str(payload), '--apply'),
                        ('--project', path, '.claude/settings.project.json', str(payload), '--apply'),
                        ('--project', path, '.WORKFLOW-DEPLOYMENT.JSON', str(payload), '--apply'),
                        ('--project', path, '.claude/settings.common.json.', str(payload), '--apply'),
                        ('--common', path, '--settings-project', str(payload), '--apply')]:
            with self.subTest(options=options):
                result = self.reverse(*options)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.snapshot(self.source), before_source)
                self.assertEqual(self.snapshot(self.consumer), before_consumer)

    def test_newer_source_conflict_is_not_overwritten(self):
        self.deploy()
        path = 'docs/agents/domain.md'
        (self.consumer / path).write_bytes(b'Consumer change\n')
        canonical = self.source / 'common' / path
        canonical.write_bytes(b'New source change\n')
        self.commit(self.source)
        result = self.reverse('--common', path, '--apply')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source changed since deployment', result.stderr)
        self.assertEqual(canonical.read_bytes(), b'New source change\n')

    def test_deletion_and_unreviewed_generator_preview(self):
        self.deploy()
        path = 'docs/agents/domain.md'
        (self.consumer / path).unlink()
        marker = self.root / 'executed'
        (self.consumer / '.claude/compose-settings.py').write_text(
            f'from pathlib import Path\nPath({str(marker)!r}).touch()\n', encoding='utf-8')
        result = self.reverse('--common', path, '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.source / 'common' / path).exists())
        self.assertFalse(marker.exists(), 'Preview executed an unreviewed consumer generator')

    def test_settings_split_validates_latest_common_before_writing(self):
        self.deploy()
        common = self.source / 'common/.claude/settings.common.json'
        value = json.loads(common.read_bytes())
        value['env'] = {'MODE': 'shared'}
        common.write_text(json.dumps(value), encoding='utf-8')
        self.commit(self.source)
        generated = self.consumer / '.claude/settings.json'
        desired = json.loads(generated.read_bytes())
        desired['env']['MODE'] = 'local'
        generated.write_bytes((json.dumps(desired, indent=2, sort_keys=True) + '\n').encode())
        project = self.root / 'project.json'
        project.write_bytes(b'{"env":{"PROJECT":"yes","MODE":"local"}}')
        original = (self.consumer / '.claude/settings.project.json').read_bytes()
        result = self.reverse('--settings-project', str(project), '--apply')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.consumer / '.claude/settings.project.json').read_bytes(), original)
