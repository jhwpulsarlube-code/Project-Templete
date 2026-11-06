"""Plan and commit the complete common layer into a consumer repository."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


RECORD = '.workflow-deployment.json'
SETTINGS = '.claude/settings.json'
PROJECT = '.claude/settings.project.json'


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    if result.returncode:
        raise ValueError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')


@dataclass
class Tree:
    repo: Path
    entries: dict[str, tuple[str, str]]

    def contents(self, paths: list[str]) -> dict[str, bytes]:
        """Read regular blobs in one Git process, including binary bytes."""
        for path in paths:
            if self.entries[path][0] not in ('100644', '100755'):
                raise ValueError(f'Not a regular tracked file: {path}')
        result = subprocess.run(['git', '-C', str(self.repo), 'cat-file', '--batch'],
                                input=''.join(self.entries[p][1] + '\n' for p in paths).encode(),
                                capture_output=True, check=True)
        offset = 0
        contents = {}
        for path in paths:
            end = result.stdout.index(b'\n', offset)
            size = int(result.stdout[offset:end].split()[-1])
            offset = end + 1
            contents[path] = result.stdout[offset:offset + size]
            offset += size + 1
        return contents

    @classmethod
    def read(cls, repo: Path, ref: str) -> Tree:
        entries = {}
        for entry in git(repo, 'ls-tree', '-rz', ref).split(b'\0'):
            if entry:
                info, name = entry.split(b'\t', 1)
                mode, _, sha = info.decode().split()
                entries[name.decode('utf-8')] = (mode, sha)
        return cls(repo, entries)

    def content(self, path: str) -> bytes | None:
        if path not in self.entries:
            return None
        mode, sha = self.entries[path]
        if mode not in ('100644', '100755'):
            raise ValueError(f'Not a regular tracked file: {path}')
        return git(self.repo, 'cat-file', 'blob', sha)


def compose(files: dict[str, bytes]) -> bytes:
    # Run the very same generator as consumers, away from both working trees.
    with tempfile.TemporaryDirectory(prefix='workflow settings ') as folder:
        directory = Path(folder)
        for name in ('compose-settings.py', 'settings.common.json', 'settings.project.json'):
            (directory / name).write_bytes(files['.claude/' + name])
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
                                 str(directory / 'compose-settings.py')], capture_output=True)
        if result.returncode:
            raise ValueError(result.stderr.decode('utf-8', errors='replace').strip())
        return (directory / 'settings.json').read_bytes()


def latest_base(consumer: Path) -> tuple[str, str]:
    remote = git(consumer, 'ls-remote', '--symref', 'origin', 'HEAD').decode()
    branch = re.search(r'^ref: refs/heads/(.+)\tHEAD$', remote, re.M)
    sha = re.search(r'^([0-9a-f]{40,64})\tHEAD$', remote, re.M)
    if not branch or not sha:
        raise ValueError('Cannot identify latest default branch from origin HEAD')
    commit = sha[1]
    try:
        git(consumer, 'cat-file', '-e', commit + '^{commit}')
    except ValueError as error:
        raise ValueError(f'Latest origin/{branch[1]} ({commit}) is unavailable locally; run git fetch origin') from error
    return branch[1], commit


def clean_errors(repo: Path, label: str) -> list[str]:
    status = git(repo, 'status', '--porcelain=v1', '--untracked-files=all').decode('utf-8', errors='replace')
    return [f'{label} working tree is dirty:\n{status.rstrip()}'] if status else []


def operation_errors(repo: Path) -> list[str]:
    errors = []
    for name in ('index.lock', 'HEAD.lock', 'MERGE_HEAD', 'CHERRY_PICK_HEAD',
                 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'):
        path = Path(git(repo, 'rev-parse', '--git-path', name).decode().strip())
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            errors.append(f'Consumer has an active Git operation or lock: {name}')
    return errors


def deployment_files(source_tree: Tree, consumer_tree: Tree, sha: str) -> dict[str, bytes]:
    files = {name.removeprefix('common/'): content for name, content in
             source_tree.contents([name for name in source_tree.entries if name.startswith('common/')]).items()}
    if not files:
        raise ValueError('No committed common assets')
    for path in files:
        safe_path(path)
        if path in (PROJECT, SETTINGS, RECORD):
            raise ValueError(f'Common asset uses a reserved path: {path}')
    project = consumer_tree.content(PROJECT)
    if project is None:
        raise ValueError(f'Missing project input: {PROJECT}')
    files[SETTINGS] = compose({**files, PROJECT: project})
    files[RECORD] = json_bytes({'source_commit': sha, 'files': {
        path: hashlib.sha256(content).hexdigest() for path, content in files.items()}})
    return files


def verify(consumer: Path, files: dict[str, bytes], deletions: list[str]) -> None:
    """Normal byte comparison and regeneration, with no force bypass."""
    drift = [path for path, content in files.items()
             if not (consumer / path).is_file() or (consumer / path).read_bytes() != content]
    drift.extend(path for path in deletions
                 if (consumer / path).exists() or (consumer / path).is_symlink())
    generated = compose({path: (consumer / path).read_bytes() for path in (
        '.claude/compose-settings.py', '.claude/settings.common.json', PROJECT)})
    if (consumer / SETTINGS).read_bytes() != generated:
        drift.append(SETTINGS)
    if drift:
        raise ValueError('Post-deployment drift:\n' + '\n'.join(sorted(set(drift))))


def previous_files(tree: Tree) -> dict[str, str]:
    content = tree.content(RECORD)
    if content is None:
        return {}
    try:
        record = json.loads(content)
        if not re.fullmatch(r'[0-9a-f]{40,64}', record['source_commit']):
            raise ValueError('Invalid source commit')
        hashes = record['files']
        if not isinstance(hashes, dict) or SETTINGS not in hashes:
            raise ValueError('Missing deployed paths')
        for path, digest in hashes.items():
            safe_path(path)
            if path == RECORD or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError(f'Invalid hash: {path}')
        return hashes
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError(f'{RECORD}: invalid deployment record: {error}') from error


def safe_path(path: str) -> None:
    parts = PurePosixPath(path).parts
    if (not parts or PurePosixPath(path).is_absolute() or '\\' in path or ':' in path
            or any(part in ('.', '..') or part.lower() == '.git' for part in parts)
            or PurePosixPath(path).as_posix() != path):
        raise ValueError(f'Unsafe deployment path: {path}')


def path_errors(consumer: Path, base: Tree, paths: set[str]) -> list[str]:
    errors = set()
    for path in paths:
        safe_path(path)
        target = consumer / path
        if target.exists() and not target.is_file():
            errors.add(f'Not a regular file: {path}')
        for candidate in (target, *target.parents):
            if candidate == consumer:
                break
            relative = candidate.relative_to(consumer).as_posix()
            if candidate.is_symlink() or candidate.resolve() != candidate.absolute():
                errors.add(f'Symlink or redirected path: {relative}')
            if candidate != target and candidate.exists() and not candidate.is_dir():
                errors.add(f'Parent is not a directory: {relative}')
            if relative in base.entries and (candidate != target or base.entries[relative][0] not in ('100644', '100755')):
                errors.add(f'Incompatible tracked path: {relative}')
        prefix = path + '/'
        if any(name.startswith(prefix) for name in base.entries):
            errors.add(f'Path contains project files: {path}')
    return sorted(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('consumer', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='show the complete plan without writing')
    parser.add_argument('--force', action='store_true', help='overwrite reviewed file conflicts only')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    consumer = args.consumer.resolve()
    try:
        if Path(git(consumer, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != consumer:
            raise ValueError('Consumer must be the root of a Git working tree')
        source_dir = (source / git(source, 'rev-parse', '--git-common-dir').decode().strip()).resolve()
        consumer_dir = (consumer / git(consumer, 'rev-parse', '--git-common-dir').decode().strip()).resolve()
        if source_dir == consumer_dir:
            raise ValueError('Source and consumer must be separate repositories')
        errors = clean_errors(source, 'Source') + clean_errors(consumer, 'Consumer') + operation_errors(consumer)
        try:
            default, base = latest_base(consumer)
        except ValueError as error:
            errors.append(str(error))
        if errors:
            raise ValueError('\n'.join(errors))
        sha = git(source, 'rev-parse', 'HEAD').decode().strip()
        tree = Tree.read(source, sha)
        base_tree = Tree.read(consumer, base)
        files = deployment_files(tree, base_tree, sha)
        previous = previous_files(base_tree)
        head = git(consumer, 'rev-parse', 'HEAD').decode().strip()
        current_tree = Tree.read(consumer, head)
        branch = 'workflow-sync/' + sha[:12]
        current = git(consumer, 'branch', '--show-current').decode().strip()
        if current == branch:
            expected = deployment_files(tree, current_tree, sha)
            actual = current_tree.contents([path for path in expected if path in current_tree.entries])
            if actual == expected and git(consumer, 'merge-base', base, head).decode().strip() == base:
                verify(consumer, expected, sorted(set(previous) - set(expected)))
                print('No changes')
                return 0
        changes = {}
        blocked = []
        structural = path_errors(consumer, base_tree, set(files) | set(previous))
        if structural:
            raise ValueError('\n'.join(structural))
        old_files = base_tree.contents([path for path in set(files) | set(previous)
                                        if path in base_tree.entries])
        uncertain = set()
        # Git can call a CRLF checkout clean while storing an LF blob. Compare
        # checkout bytes only if both file and applicable attributes match.
        # Otherwise require explicit force and materialize the canonical bytes.
        for path in old_files:
            attributes = [str(parent / '.gitattributes') for parent in PurePosixPath(path).parents]
            same_attributes = all(current_tree.entries.get(attr) == base_tree.entries.get(attr)
                                  for attr in attributes)
            if current_tree.entries.get(path) == base_tree.entries[path] and same_attributes:
                old_files[path] = (consumer / path).read_bytes()
            elif path != RECORD:
                uncertain.add(path)
        generated_before = None
        if SETTINGS in previous:
            generator_paths = ('.claude/compose-settings.py', '.claude/settings.common.json')
            if all(path in previous and path in old_files
                   and hashlib.sha256(old_files[path]).hexdigest() == previous[path]
                   for path in generator_paths):
                project_before = base_tree.content(PROJECT)
                if project_before is not None:
                    generated_before = compose({**old_files, PROJECT: project_before})
        for path, content in sorted(files.items()):
            old = old_files.get(path)
            target = consumer / path
            if path in uncertain:
                blocked.append(path)
            if path not in current_tree.entries and target.is_file() and target.read_bytes() != content:
                blocked.append(path)
            if path != RECORD:
                if path in previous:
                    if path == SETTINGS:
                        if old is None or generated_before is None or old != generated_before:
                            blocked.append(path)
                    elif old is None or hashlib.sha256(old).hexdigest() != previous[path]:
                        blocked.append(path)
                elif old is not None:
                    if old != content:
                        blocked.append(path)
                    elif path not in uncertain:
                        print('ADOPT ' + path)
            mode = tree.entries.get('common/' + path, ('100644', ''))[0]
            old_mode = base_tree.entries.get(path, ('100644', ''))[0]
            if old != content or old_mode != mode or path in uncertain:
                changes[path] = content
                print(('ADD ' if old is None else 'UPDATE ') + path)
        deletions = sorted(set(previous) - set(files))
        for path in deletions:
            old = old_files.get(path)
            if path in uncertain or old is None or hashlib.sha256(old).hexdigest() != previous[path]:
                blocked.append(path)
            print('DELETE ' + path)
        for path in sorted(set(blocked)):
            print('BLOCK ' + path)
        print(f'Base origin/{default} {base}\nBranch {branch}')
        if blocked and not args.force:
            raise ValueError('Conflicting or drifted paths; review the BLOCK list and use --force explicitly')
        if not changes and not deletions:
            print('No changes')
            return 0
        ref = 'refs/heads/' + branch
        refs = git(consumer, 'for-each-ref', '--format=%(refname)', 'refs/heads').decode().splitlines()
        conflicts = [existing for existing in refs
                     if existing == ref or ref.startswith(existing + '/') or existing.startswith(ref + '/')]
        if conflicts:
            raise ValueError(f'Deployment branch cannot be created ({branch}): ' + ', '.join(conflicts))
        lock = Path(git(consumer, 'rev-parse', '--git-path', ref + '.lock').decode().strip())
        if not lock.is_absolute():
            lock = consumer / lock
        if lock.exists():
            raise ValueError(f'Deployment branch is locked: {ref}.lock')
        if args.dry_run:
            return 0
        git(consumer, 'switch', '--no-overwrite-ignore', '-c', branch, base)
        for path in deletions:
            (consumer / path).unlink(missing_ok=True)
        for path, content in changes.items():
            target = consumer / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            mode = tree.entries.get('common/' + path, ('100644', ''))[0]
            if os.name != 'nt':
                target.chmod(0o755 if mode == '100755' else 0o644)
        verify(consumer, files, deletions)
        staged_paths = list(changes) + [path for path in deletions if path in base_tree.entries]
        git(consumer, 'add', '-A', '-f', '--', *staged_paths)
        for mode, flag in [('100644', '-x'), ('100755', '+x')]:
            paths = [path for path in changes
                     if tree.entries.get('common/' + path, ('100644', ''))[0] == mode]
            if paths:
                git(consumer, 'update-index', '--chmod=' + flag, '--', *paths)
        staged = Tree.read(consumer, git(consumer, 'write-tree').decode().strip())
        if staged.contents(list(files)) != files:
            raise ValueError('Git attributes or filters changed deployed bytes in the index')
        git(consumer, 'commit', '-m', f'Deploy workflow assets from {sha}')
        commit = git(consumer, 'rev-parse', 'HEAD').decode().strip()
        try:
            verify(consumer, files, deletions)
            if Tree.read(consumer, commit).entries != staged.entries or clean_errors(consumer, 'Consumer'):
                raise ValueError('Git hooks changed the deployment commit or working tree')
        except (OSError, ValueError) as error:
            # Retract only our new branch tip with a compare-and-swap. Keep all
            # index/worktree changes for review; never reset or delete files.
            git(consumer, 'update-ref', 'refs/heads/' + branch, base, commit)
            raise ValueError(f'Post-commit verification failed; deployment commit retracted, '
                             f'working changes preserved: {error}') from error
        print('Committed ' + commit)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f'sync: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
