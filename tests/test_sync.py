"""Exercise manual deployment through its CLI and real isolated Git repos."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECORD = '.workflow-deployment.json'


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workflow sync ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.consumer = self.root / 'consumer'
        self.remote = self.root / 'remote.git'
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE='1')
        for repo in (self.source, self.consumer):
            repo.mkdir()
            self.git(repo, 'init', '-q', '-b', 'main')
            self.git(repo, 'config', 'user.name', 'Sync Test')
            self.git(repo, 'config', 'user.email', 'sync@example.invalid')
            self.git(repo, 'config', 'core.autocrlf', 'false')
        shutil.copytree(ROOT / 'common', self.source / 'common',
                        ignore=shutil.ignore_patterns('__pycache__'))
        if (ROOT / 'tools').exists():
            shutil.copytree(ROOT / 'tools', self.source / 'tools',
                            ignore=shutil.ignore_patterns('__pycache__'))
        self.commit(self.source)
        executable = []
        for entry in self.git(ROOT, 'ls-tree', '-rz', 'HEAD', 'common').stdout.split(b'\0'):
            if entry.startswith(b'100755 '):
                executable.append(entry.split(b'\t', 1)[1].decode())
        if executable:
            self.git(self.source, 'update-index', '--chmod=+x', '--', *executable)
            self.git(self.source, 'commit', '--amend', '--no-edit', '-q')
        (self.consumer / '.claude').mkdir()
        (self.consumer / '.claude/settings.project.json').write_bytes(b'{"env":{"PROJECT":"yes"}}\n')
        (self.consumer / 'AGENTS.md').write_bytes(b'Project owned\n')
        self.commit(self.consumer)
        self.git(self.root, 'init', '--bare', '-q', '-b', 'main', str(self.remote))
        self.git(self.consumer, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.consumer, 'push', '-qu', 'origin', 'main')

    def git(self, repo, *args, check=True):
        return subprocess.run(['git', '-C', str(repo), *args], env=self.env,
                              capture_output=True, check=check)

    def commit(self, repo):
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-qm', 'fixture')

    def sync(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', '-B',
                               str(self.source / 'tools/sync.py'), str(self.consumer), *args],
                              env=self.env, capture_output=True, text=True, encoding='utf-8')

    def snapshot(self, repo):
        return {p.relative_to(repo).as_posix(): p.read_bytes()
                for p in repo.rglob('*') if p.is_file() and '.git' not in p.relative_to(repo).parts}

    def test_dry_run_plans_all_assets_settings_and_record_without_changes(self):
        before = self.snapshot(self.consumer)
        head = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        refs = self.git(self.consumer, 'show-ref').stdout
        result = self.sync('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.snapshot(self.source / 'common'):
            self.assertIn('ADD ' + path, result.stdout)
        self.assertIn('ADD .claude/settings.json', result.stdout)
        self.assertIn('ADD ' + RECORD, result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, head)
        self.assertEqual(self.git(self.consumer, 'show-ref').stdout, refs)

    def test_first_deployment_is_one_commit_and_retry_has_no_changes(self):
        parent = self.git(self.consumer, 'rev-parse', 'HEAD').stdout.strip().decode()
        source_sha = self.git(self.source, 'rev-parse', 'HEAD').stdout.strip().decode()
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.consumer, 'branch', '--show-current').stdout.strip().decode(),
                         'workflow-sync/' + source_sha[:12])
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD^').stdout.strip().decode(), parent)
        for path, content in self.snapshot(self.source / 'common').items():
            self.assertEqual((self.consumer / path).read_bytes(), content, path)
        self.assertEqual((self.consumer / 'AGENTS.md').read_bytes(), b'Project owned\n')
        settings = json.loads((self.consumer / '.claude/settings.json').read_bytes())
        self.assertEqual(settings['env']['PROJECT'], 'yes')
        record = json.loads((self.consumer / RECORD).read_bytes())
        self.assertEqual(record['source_commit'], source_sha)
        self.assertEqual(set(record['files']), set(self.snapshot(self.source / 'common')) | {'.claude/settings.json'})
        for path, digest in record['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.consumer / path).read_bytes()).hexdigest())
        self.assertEqual(self.git(self.consumer, 'status', '--porcelain').stdout, b'')
        for entry in self.git(self.source, 'ls-tree', '-rz', 'HEAD', 'common').stdout.split(b'\0'):
            if entry.startswith(b'100755 '):
                path = entry.split(b'\t', 1)[1].decode().removeprefix('common/')
                self.assertTrue(self.git(self.consumer, 'ls-tree', 'HEAD', path).stdout.startswith(b'100755 '))
                if os.name != 'nt':
                    self.assertTrue(os.access(self.consumer / path, os.X_OK))
        head = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        before = self.snapshot(self.consumer)
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No changes', result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, head)

    def test_identical_files_are_adopted_and_conflicts_require_force(self):
        same = '.claude/compose-settings.py'
        conflict = 'docs/agents/domain.md'
        (self.consumer / same).write_bytes((self.source / 'common' / same).read_bytes())
        target = self.consumer / conflict
        target.parent.mkdir(parents=True)
        target.write_bytes(b'project change\r\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        before = self.snapshot(self.consumer)
        for flags in [(), ('--dry-run',)]:
            result = self.sync(*flags)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('BLOCK ' + conflict, result.stdout)
            self.assertIn('ADOPT ' + same, result.stdout)
            self.assertEqual(self.snapshot(self.consumer), before)
            self.assertEqual(self.git(self.consumer, 'branch', '--show-current').stdout.strip(), b'main')
        preview = self.sync('--force', '--dry-run')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), (self.source / 'common' / conflict).read_bytes())

    def test_force_does_not_bypass_dirty_trees_or_unavailable_default_branch(self):
        for repo in (self.source, self.consumer):
            dirty = repo / 'uncommitted.txt'
            dirty.write_bytes(b'pending\n')
        before = self.snapshot(self.consumer)
        result = self.sync('--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source working tree is dirty', result.stderr)
        self.assertIn('Consumer working tree is dirty', result.stderr)
        self.assertIn('uncommitted.txt', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        for repo in (self.source, self.consumer):
            (repo / 'uncommitted.txt').unlink()
        self.git(self.consumer, 'remote', 'set-url', 'origin', str(self.root / 'missing.git'))
        before = self.snapshot(self.consumer)
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('origin', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)

    def test_latest_default_is_required_and_plan_uses_its_project_input(self):
        other = self.root / 'other'
        self.git(self.root, 'clone', '-q', str(self.remote), str(other))
        self.git(other, 'config', 'user.name', 'Other')
        self.git(other, 'config', 'user.email', 'other@example.invalid')
        (other / '.claude/settings.project.json').write_bytes(b'{"env":{"LATEST":"yes"}}\n')
        self.commit(other)
        self.git(other, 'push', '-q')
        before = self.snapshot(self.consumer)
        result = self.sync('--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('git fetch origin', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.git(self.consumer, 'fetch', '-q', 'origin')
        self.git(self.consumer, 'switch', '-qc', 'feature')
        (self.consumer / '.claude/settings.project.json').write_bytes(b'{"env":{"FEATURE":"yes"}}\n')
        self.commit(self.consumer)
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.consumer / '.claude/settings.json').read_bytes())['env'], {'LATEST': 'yes'})
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD^').stdout,
                         self.git(other, 'rev-parse', 'HEAD').stdout)

    def test_ignored_file_conflicts_are_planned_and_require_force(self):
        (self.consumer / '.gitignore').write_bytes(b'/docs/\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        target = self.consumer / 'docs/agents/domain.md'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'ignored project content\n')
        before = self.snapshot(self.consumer)
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BLOCK docs/agents/domain.md', result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), (self.source / 'common/docs/agents/domain.md').read_bytes())

    def test_directory_obstructions_are_refused_before_creating_a_branch(self):
        (self.consumer / 'docs').write_bytes(b'project owned file\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        before = self.snapshot(self.consumer)
        refs = self.git(self.consumer, 'show-ref').stdout
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('docs', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.assertEqual(self.git(self.consumer, 'show-ref').stdout, refs)

    def test_existing_branch_or_index_lock_is_refused_without_changes(self):
        sha = self.git(self.source, 'rev-parse', 'HEAD').stdout.strip().decode()
        branch = 'workflow-sync/' + sha[:12]
        self.git(self.consumer, 'branch', branch)
        before = self.snapshot(self.consumer)
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(branch, result.stderr)
        self.git(self.consumer, 'branch', '-d', branch)
        self.git(self.consumer, 'branch', 'workflow-sync')
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refs/heads/workflow-sync', result.stderr)
        self.git(self.consumer, 'branch', '-d', 'workflow-sync')
        branch_lock = self.consumer / '.git/refs/heads' / (branch + '.lock')
        branch_lock.parent.mkdir(parents=True, exist_ok=True)
        branch_lock.write_bytes(b'locked\n')
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(branch + '.lock', result.stderr)
        branch_lock.unlink()
        lock = self.consumer / '.git/index.lock'
        lock.write_bytes(b'held by another process\n')
        result = self.sync('--force', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('index.lock', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.assertEqual(self.git(self.consumer, 'branch', '--show-current').stdout.strip(), b'main')

    def test_plan_includes_updates_deletions_and_all_drifted_paths(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = self.consumer / 'docs/agents/domain.md'
        removed = self.consumer / 'docs/agents/triage-labels.md'
        changed.write_bytes(b'committed consumer edit\r\n')
        removed.unlink()
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        (self.source / 'common/docs/agents/triage-labels.md').unlink()
        (self.source / 'common/docs/agents/domain.md').write_bytes(b'new canonical content\n')
        self.commit(self.source)
        before = self.snapshot(self.consumer)
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        for line in ('UPDATE docs/agents/domain.md', 'DELETE docs/agents/triage-labels.md',
                     'BLOCK docs/agents/domain.md', 'BLOCK docs/agents/triage-labels.md',
                     'UPDATE ' + RECORD):
            self.assertIn(line, result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(removed.exists())
        self.assertEqual(changed.read_bytes(), b'new canonical content\n')
        self.assertEqual((self.consumer / 'AGENTS.md').read_bytes(), b'Project owned\n')

    def test_settings_conflict_fails_before_branch_creation(self):
        common = json.loads((self.source / 'common/.claude/settings.common.json').read_bytes())
        common['env'] = {'CONFLICT': 'common'}
        (self.source / 'common/.claude/settings.common.json').write_text(json.dumps(common), encoding='utf-8')
        self.commit(self.source)
        (self.consumer / '.claude/settings.project.json').write_bytes(b'{"env":{"CONFLICT":"project"}}\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        before = self.snapshot(self.consumer)
        refs = self.git(self.consumer, 'show-ref').stdout
        result = self.sync('--force')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('$.env.CONFLICT', result.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        self.assertEqual(self.git(self.consumer, 'show-ref').stdout, refs)

    def test_git_normalization_does_not_hide_first_deployment_byte_conflict(self):
        path = 'docs/agents/domain.md'
        canonical = (self.source / 'common' / path).read_bytes()
        target = self.consumer / path
        target.parent.mkdir(parents=True)
        (self.consumer / '.gitattributes').write_bytes(b'*.md text eol=crlf\n')
        target.write_bytes(canonical.replace(b'\n', b'\r\n'))
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        self.assertEqual(self.git(self.consumer, 'status', '--porcelain').stdout, b'')
        before = target.read_bytes()
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BLOCK ' + path, result.stdout)
        self.assertEqual(target.read_bytes(), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), canonical)

    def test_default_checkout_bytes_cannot_be_inferred_from_a_different_feature_file(self):
        path = 'docs/agents/domain.md'
        canonical = (self.source / 'common' / path).read_bytes()
        target = self.consumer / path
        target.parent.mkdir(parents=True)
        (self.consumer / '.gitattributes').write_bytes(b'*.md text eol=crlf\n')
        target.write_bytes(canonical.replace(b'\n', b'\r\n'))
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        self.git(self.consumer, 'switch', '-qc', 'feature')
        target.write_bytes(b'feature edit\r\n')
        self.commit(self.consumer)
        before = self.snapshot(self.consumer)
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BLOCK ' + path, result.stdout)
        self.assertNotIn('ADOPT ' + path, result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), canonical)

    def test_hook_mutation_cannot_leave_an_unverified_deployment_commit(self):
        extension = self.consumer / '.githooks/pre-commit.d/10-edit'
        extension.parent.mkdir(parents=True)
        extension.write_bytes(b"printf 'hook edit\\n' > docs/agents/domain.md\ngit add docs/agents/domain.md\n")
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q')
        self.git(self.consumer, 'config', 'core.hooksPath', '.githooks')
        base = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Post-commit verification failed', result.stderr)
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, base)
        self.assertEqual((self.consumer / 'docs/agents/domain.md').read_bytes(), b'hook edit\n')

    def test_hook_cannot_restore_an_ignored_retired_asset(self):
        retired = 'docs/agents/domain.md'
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.consumer / '.gitignore').write_bytes((retired + '\n').encode())
        extension = self.consumer / '.githooks/post-commit.d/10-restore'
        extension.parent.mkdir(parents=True)
        extension.write_bytes(("printf 'restored by hook\\n' > " + retired + '\n').encode())
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        self.git(self.consumer, 'config', 'core.hooksPath', '.githooks')
        (self.source / 'common' / retired).unlink()
        self.commit(self.source)
        base = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        result = self.sync()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('Post-commit verification failed', result.stderr)
        self.assertIn(retired, result.stderr)
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, base)
        self.assertEqual((self.consumer / retired).read_bytes(), b'restored by hook\n')

    def test_retry_detects_an_ignored_retired_asset_restored_after_deployment(self):
        retired = 'docs/agents/domain.md'
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.consumer / '.gitignore').write_bytes((retired + '\n').encode())
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        (self.source / 'common' / retired).unlink()
        self.commit(self.source)
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.consumer / retired).write_bytes(b'restored after deployment\n')
        before = self.snapshot(self.consumer)
        head = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        for flags in [('--dry-run',), ('--force',)]:
            result = self.sync(*flags)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(retired, result.stderr)
            self.assertEqual(self.snapshot(self.consumer), before)
            self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, head)

    def test_forced_update_protects_all_drift_and_preserves_project_files(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        changed = 'docs/agents/domain.md'
        missing = 'docs/agents/triage-labels.md'
        retired_changed = 'docs/agents/issue-tracker.md'
        retired_missing = 'tools/issue-status.ps1'
        for path in (changed, retired_changed):
            (self.consumer / path).write_bytes(b'consumer change\r\n')
        for path in (missing, retired_missing):
            (self.consumer / path).unlink()
        project_files = {
            'docs/agents/project.md': b'project documentation\n',
            '.githooks/pre-commit.d/10-project': b'exit 0\n',
            '.claude/machine_checks.py': b'def capabilities(): return {}\n',
        }
        for path, content in project_files.items():
            target = self.consumer / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        base = self.git(self.consumer, 'rev-parse', 'HEAD').stdout.strip()
        for path in (retired_changed, retired_missing):
            (self.source / 'common' / path).unlink()
        (self.source / 'common' / changed).write_bytes(b'updated canonical asset\n')
        (self.source / 'common/docs/agents/new.md').write_bytes(b'new canonical asset\n')
        common = self.source / 'common/.claude/settings.common.json'
        settings = json.loads(common.read_bytes())
        settings.setdefault('env', {})['COMMON_REVISION'] = 'updated'
        common.write_text(json.dumps(settings), encoding='utf-8')
        self.commit(self.source)
        before = self.snapshot(self.consumer)
        refs = self.git(self.consumer, 'show-ref').stdout
        for flags in [('--dry-run',), ()]:
            result = self.sync(*flags)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual({line for line in result.stdout.splitlines() if line.startswith('BLOCK ')},
                             {'BLOCK ' + path for path in (changed, missing, retired_changed, retired_missing)})
            self.assertEqual(self.snapshot(self.consumer), before)
            self.assertEqual(self.git(self.consumer, 'show-ref').stdout, refs)
        preview = self.sync('--force', '--dry-run')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        def plan(output):
            return [line for line in output.splitlines()
                    if line.startswith(('ADD ', 'UPDATE ', 'DELETE ', 'BLOCK ', 'ADOPT '))]

        self.assertEqual(plan(result.stdout), plan(preview.stdout))
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD^').stdout.strip(), base)
        for path, content in project_files.items():
            self.assertEqual((self.consumer / path).read_bytes(), content)
        for path in (retired_changed, retired_missing):
            self.assertFalse((self.consumer / path).exists())
        for path, content in self.snapshot(self.source / 'common').items():
            self.assertEqual((self.consumer / path).read_bytes(), content, path)
        settings = json.loads((self.consumer / '.claude/settings.json').read_bytes())
        self.assertEqual(settings['env']['COMMON_REVISION'], 'updated')
        record = json.loads((self.consumer / RECORD).read_bytes())
        self.assertEqual(record['source_commit'], self.git(self.source, 'rev-parse', 'HEAD').stdout.strip().decode())
        self.assertEqual(set(record['files']), set(self.snapshot(self.source / 'common')) | {'.claude/settings.json'})
        for path, digest in record['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.consumer / path).read_bytes()).hexdigest())
        head = self.git(self.consumer, 'rev-parse', 'HEAD').stdout
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No changes', result.stdout)
        self.assertEqual(self.git(self.consumer, 'rev-parse', 'HEAD').stdout, head)

    def test_project_input_without_regeneration_blocks_settings(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.consumer / '.claude/settings.project.json').write_bytes(b'{"env":{"NEW_INPUT":"yes"}}\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        (self.source / 'change.txt').write_bytes(b'new source revision\n')
        self.commit(self.source)
        before = self.snapshot(self.consumer)
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('BLOCK .claude/settings.json', result.stdout)
        self.assertEqual(self.snapshot(self.consumer), before)
        result = self.sync('--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads((self.consumer / '.claude/settings.json').read_bytes())
        self.assertEqual(settings['env']['NEW_INPUT'], 'yes')

    def test_generated_settings_use_current_project_input_instead_of_record_hash(self):
        result = self.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.consumer / '.claude/settings.project.json').write_bytes(b'{"env":{"NEW_INPUT":"yes"}}\n')
        generated = subprocess.run([sys.executable, '-I', '-S', '-B', '.claude/compose-settings.py'],
                                   cwd=self.consumer, env=self.env, capture_output=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        (self.source / 'change.txt').write_bytes(b'new source revision\n')
        self.commit(self.source)
        result = self.sync('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('BLOCK .claude/settings.json', result.stdout)
        (self.consumer / '.claude/settings.json').write_bytes(b'{"manually":"edited"}\n')
        self.commit(self.consumer)
        self.git(self.consumer, 'push', '-q', 'origin', 'HEAD:main')
        result = self.sync('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('BLOCK .claude/settings.json', result.stdout)


if __name__ == '__main__':
    unittest.main()
