"""Compare a distribution with its deployment record without a source checkout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


RECORD = '.workflow-deployment.json'
SETTINGS = '.claude/settings.json'
GENERATOR = '.claude/compose-settings.py'
COMMON = '.claude/settings.common.json'
PROJECT = '.claude/settings.project.json'


def deployment_hashes(content: bytes) -> dict[str, str]:
    record = json.loads(content)
    if not isinstance(record, dict) or not isinstance(record.get('source_commit'), str):
        raise ValueError('Invalid deployment record')
    if not re.fullmatch(r'[0-9a-f]{40,64}', record['source_commit']):
        raise ValueError('Invalid source commit in deployment record')
    hashes = record.get('files')
    if not isinstance(hashes, dict) or not {SETTINGS, GENERATOR, COMMON} <= hashes.keys():
        raise ValueError('Missing deployed paths in deployment record')
    for path, digest in hashes.items():
        parts = PurePosixPath(path).parts
        if (not parts or PurePosixPath(path).is_absolute() or '\\' in path or ':' in path
                or any(part in ('.', '..') or part.lower() == '.git' for part in parts)
                or PurePosixPath(path).as_posix() != path or path in (RECORD, PROJECT)):
            raise ValueError(f'Invalid deployment path: {path}')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError(f'Invalid deployment hash: {path}')
    return hashes


def generated_settings(files: dict[str, bytes]) -> bytes:
    # Execute only the validated deployed generator. Never rewrite user files.
    with tempfile.TemporaryDirectory(prefix='workflow drift ') as folder:
        directory = Path(folder)
        for path in (GENERATOR, COMMON, PROJECT):
            if path not in files:
                raise ValueError(f'Missing input: {path}')
            (directory / Path(path).name).write_bytes(files[path])
        result = subprocess.run([sys.executable, '-I', '-S', '-B',
                                 str(directory / 'compose-settings.py')], capture_output=True)
        if result.returncode:
            raise ValueError(result.stderr.decode('utf-8', errors='replace').strip())
        return (directory / 'settings.json').read_bytes()


def verify(files: dict[str, bytes], hashes: dict[str, str]) -> None:
    drift = [path for path, digest in hashes.items() if path != SETTINGS
             and (path not in files or hashlib.sha256(files[path]).hexdigest() != digest)]
    if drift:
        raise ValueError('Drifted distributions:\n' + '\n'.join(sorted(drift)))
    if files.get(SETTINGS) != generated_settings(files):
        raise ValueError(f'Drifted generated settings: {SETTINGS}; regenerate from project input')


def worktree_files(root: Path, paths: set[str]) -> dict[str, bytes]:
    files = {}
    for path in paths:
        target = root / path
        if target.resolve() != target.absolute() or target.is_symlink():
            raise ValueError(f'Redirected distribution path: {path}')
        if target.is_file():
            files[path] = target.read_bytes()
    return files


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(['git', '-C', str(root), *args], input=data, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout


def staged_files(root: Path) -> dict[str, bytes]:
    entries = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        info, name = entry.split(b'\t', 1)
        mode, sha, stage = info.decode().split()
        if stage != '0':
            raise ValueError('Resolve unmerged index entries before checking drift')
        entries[name.decode('utf-8')] = (mode, sha)
    if RECORD not in entries:
        raise ValueError(f'Missing staged deployment record: {RECORD}')
    record = git(root, 'cat-file', 'blob', entries[RECORD][1])
    hashes = deployment_hashes(record)
    paths = sorted((set(hashes) | {PROJECT, RECORD}) & entries.keys())
    for path in paths:
        if entries[path][0] not in ('100644', '100755'):
            raise ValueError(f'Not a regular staged distribution: {path}')
    output = git(root, 'cat-file', '--batch',
                 data=''.join(entries[path][1] + '\n' for path in paths).encode())
    files = {}
    offset = 0
    for path in paths:
        end = output.index(b'\n', offset)
        size = int(output[offset:end].split()[-1])
        offset = end + 1
        files[path] = output[offset:offset + size]
        offset += size + 1
    return files


def check_worktree(root: Path) -> int:
    try:
        hashes = deployment_hashes((root / RECORD).read_bytes())
        verify(worktree_files(root, set(hashes) | {PROJECT}), hashes)
        files = staged_files(root)
        verify(files, deployment_hashes(files[RECORD]))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f'workflow-drift: {error}', file=sys.stderr)
        print('Restore distributions or deploy reviewed source changes. Do not edit recorded hashes.\n'
              'Change settings.project.json and run python -B .claude/compose-settings.py for project settings.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(check_worktree(Path.cwd()))
