"""Verify standalone distributions through real consumer commits."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


COMMON = Path(__file__).resolve().parents[1] / 'common'
RECORD = '.workflow-deployment.json'


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='standalone consumer ')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('WORKFLOW_SKIP_DRIFT_ONCE', None)
        self.env.pop('PYTHONDONTWRITEBYTECODE', None)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Drift Test')
        self.git('config', 'user.email', 'drift@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        # The deployed tree contains neither the source repository nor user skills.
        shutil.copytree(COMMON / '.githooks', self.repo / '.githooks',
                        ignore=shutil.ignore_patterns('__pycache__'))
        directory = self.repo / '.claude'
        directory.mkdir()
        shutil.copyfile(COMMON / '.claude/compose-settings.py', directory / 'compose-settings.py')
        (directory / 'settings.common.json').write_bytes(b'{"env":{"COMMON":"yes"}}\n')
        (directory / 'settings.project.json').write_bytes(b'{}\n')
        self.generate()
        files = {p.relative_to(self.repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.repo.rglob('*') if p.is_file()
                 and '.git' not in p.parts and p.name != 'settings.project.json'}
        (self.repo / RECORD).write_text(json.dumps({'source_commit': '1' * 40, 'files': files}),
                                       encoding='utf-8')
        self.git('config', 'core.hooksPath', '.githooks')
        self.git('add', '.')

    def git(self, *args, **kwargs):
        return subprocess.run(['git', '-C', str(self.repo), *args],
                              env=kwargs.pop('env', self.env), capture_output=True, **kwargs)

    def generate(self):
        subprocess.run([sys.executable, '-I', '-S', '-B',
                        str(self.repo / '.claude/compose-settings.py')],
                       env=self.env, capture_output=True, check=True)

    def commit(self, **kwargs):
        return self.git('commit', '--allow-empty', '-qm', 'consumer change', **kwargs)

    def test_modified_distribution_blocks_commit_before_project_checks(self):
        extension = self.repo / '.githooks/pre-commit.d/10-check'
        extension.parent.mkdir()
        extension.write_bytes(b'echo project-check-ran >&2\n')
        (self.repo / '.claude/settings.common.json').write_bytes(b'{}\n')
        self.git('add', '.')
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'.claude/settings.common.json', result.stderr)
        self.assertNotIn(b'project-check-ran', result.stderr)
        self.assertNotEqual(self.git('rev-parse', '--verify', 'HEAD').returncode, 0)

    def test_partial_staging_cannot_commit_stale_settings(self):
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / '.claude/settings.project.json').write_bytes(b'{"env":{"PROJECT":"new"}}\n')
        self.generate()
        self.git('add', '.claude/settings.project.json')
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'.claude/settings.json', result.stderr)

    def test_one_command_bypass_keeps_project_checks_and_does_not_persist(self):
        (self.repo / '.claude/settings.common.json').write_bytes(b'{}\n')
        self.git('add', '.')
        extension = self.repo / '.githooks/pre-commit.d/10-check'
        extension.parent.mkdir()
        extension.write_bytes(b'echo project-check-ran >&2\n')
        result = self.commit(env=dict(self.env, WORKFLOW_SKIP_DRIFT_ONCE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'project-check-ran', result.stderr)
        self.assertIn(b'PR', result.stderr)
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'.claude/settings.common.json', result.stderr)

    def test_last_deployment_state_passes_without_latest_source_access(self):
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('status', '--porcelain').stdout, b'')

    def test_deleted_and_line_ending_changed_distributions_block(self):
        path = self.repo / '.claude/settings.common.json'
        original = path.read_bytes()
        for content in (None, original.replace(b'\n', b'\r\n')):
            with self.subTest(content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                self.git('add', '-A')
                result = self.commit()
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'.claude/settings.common.json', result.stderr)

    def test_project_input_requires_regeneration_then_commits_without_record_changes(self):
        record = (self.repo / RECORD).read_bytes()
        (self.repo / '.claude/settings.project.json').write_bytes(b'{"env":{"PROJECT":"new"}}\n')
        self.git('add', '.')
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'.claude/settings.json', result.stderr)
        self.generate()
        self.git('add', '.')
        result = self.commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / RECORD).read_bytes(), record)

    def test_direct_settings_edit_and_line_ending_changes_block_without_rewriting(self):
        path = self.repo / '.claude/settings.json'
        original = path.read_bytes()
        for content in (b'{}\n', original.replace(b'\n', b'\r\n')):
            with self.subTest(content=content):
                path.write_bytes(content)
                self.git('add', '.')
                result = self.commit()
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'.claude/settings.json', result.stderr)
                self.assertEqual(path.read_bytes(), content)

    def test_staged_distribution_edit_blocks_even_with_restored_worktree(self):
        path = self.repo / '.claude/settings.common.json'
        original = path.read_bytes()
        path.write_bytes(b'{}\n')
        self.git('add', '.')
        path.write_bytes(original)
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'.claude/settings.common.json', result.stderr)

    def test_missing_and_invalid_record_fail_closed(self):
        path = self.repo / RECORD
        original = path.read_bytes()
        for content in (None, b'{}', b'null', b'{invalid'):
            with self.subTest(content=content):
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                self.git('add', '-A')
                result = self.commit()
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'workflow-drift:', result.stderr)
                path.write_bytes(original)

    def test_settings_conflict_blocks_without_overwriting_generated_file(self):
        path = self.repo / '.claude/settings.json'
        original = path.read_bytes()
        (self.repo / '.claude/settings.project.json').write_bytes(b'{"env":{"COMMON":"conflict"}}\n')
        self.git('add', '.')
        result = self.commit()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'Conflicting setting', result.stderr)
        self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
