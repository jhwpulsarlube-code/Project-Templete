"""Preview consumer drift and route explicitly selected changes to their owners."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from sync import (PROJECT, RECORD, SETTINGS, Tree, clean_errors, compose, git,
                  operation_errors, path_errors, previous_files, safe_path)


COMMON_SETTINGS = '.claude/settings.common.json'


def show_diff(path: str, before: bytes | None, after: bytes | None) -> None:
    print('DIFF ' + path)
    if before == after:
        return
    try:
        old = (before or b'').decode('utf-8').splitlines(keepends=True)
        new = (after or b'').decode('utf-8').splitlines(keepends=True)
        print(''.join(difflib.unified_diff(old, new, fromfile='deployed/' + path,
                                         tofile='consumer/' + path)), end='\n')
    except UnicodeDecodeError:
        print('Binary bytes differ')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('consumer', type=Path)
    parser.add_argument('--common', action='append', default=[], metavar='PATH')
    parser.add_argument('--project', action='append', nargs=3, default=[],
                        metavar=('PATH', 'TARGET', 'INPUT'),
                        help='route a reviewed, extracted input to a project-owned extension')
    parser.add_argument('--settings-common', type=Path, help='reviewed common JSON input')
    parser.add_argument('--settings-project', type=Path, help='reviewed project JSON input')
    parser.add_argument('--apply', action='store_true', help='write the selected plan; default is preview')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    consumer = args.consumer.resolve()
    try:
        if Path(git(consumer, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != consumer:
            raise ValueError('Consumer must be the root of a Git working tree')
        source_git = (source / git(source, 'rev-parse', '--git-common-dir').decode().strip()).resolve()
        consumer_git = (consumer / git(consumer, 'rev-parse', '--git-common-dir').decode().strip()).resolve()
        if source_git == consumer_git:
            raise ValueError('Source and consumer must be separate repositories')
        errors = clean_errors(source, 'Source') + operation_errors(source) + operation_errors(consumer)
        if errors:
            raise ValueError('\n'.join(errors))
        current = Tree.read(consumer, 'HEAD')
        hashes = previous_files(current)
        if not hashes:
            raise ValueError('A valid deployment record is required')
        record = json.loads(current.content(RECORD) or b'{}')
        baseline = Tree.read(source, record['source_commit'])
        latest = Tree.read(source, 'HEAD')
        errors = path_errors(consumer, current, set(hashes) | {RECORD, PROJECT})
        if errors:
            raise ValueError('\n'.join(errors))
        if (consumer / RECORD).read_bytes() != current.content(RECORD):
            raise ValueError('Deployment record has uncommitted changes')
        changed: dict[str, bytes | None] = {}
        originals: dict[str, bytes] = {}
        for path, digest in sorted(hashes.items()):
            if path == SETTINGS:
                continue
            original = baseline.content('common/' + path)
            if original is None or hashlib.sha256(original).hexdigest() != digest:
                raise ValueError('Deployment record does not match source commit: ' + path)
            originals[path] = original
            target = consumer / path
            content = target.read_bytes() if target.is_file() else None
            if content != original:
                changed[path] = content
                show_diff(path, original, content)
        settings_inputs = {path: (consumer / path).read_bytes() for path in
                           (COMMON_SETTINGS, PROJECT)}
        # Preview must not execute a consumer-edited generator before review.
        settings_inputs['.claude/compose-settings.py'] = originals['.claude/compose-settings.py']
        expected = compose(settings_inputs)
        generated = (consumer / SETTINGS).read_bytes() if (consumer / SETTINGS).is_file() else None
        if generated != expected:
            changed[SETTINGS] = generated
            show_diff(SETTINGS, expected, generated)
        if not changed:
            print('No drift')
        writes: dict[tuple[Path, str], bytes | None] = {}
        selected = set()

        def select(path: str) -> None:
            if path not in changed:
                raise ValueError('Not a drifted deployment path: ' + path)
            if path in selected:
                raise ValueError('Path selected more than once: ' + path)
            selected.add(path)

        for path in args.common:
            select(path)
            if path == SETTINGS:
                raise ValueError('Use split settings inputs, never reverse-copy settings.json')
            existing = latest.content('common/' + path)
            if existing != originals[path] and existing != changed[path]:
                raise ValueError('Source changed since deployment; reconcile before selecting: ' + path)
            writes[source, 'common/' + path] = changed[path]
            print('COMMON ' + path)
        managed = set(hashes) | {RECORD, PROJECT, SETTINGS}
        managed.update(p.removeprefix('common/') for p in latest.entries if p.startswith('common/'))
        managed_aliases = {path.casefold() for path in managed}
        project_targets = set()
        for path, target, input_path in args.project:
            select(path)
            if path == SETTINGS:
                raise ValueError('Use --settings-project for generated settings')
            safe_path(target)
            # Keep portable ownership even on case-insensitive Windows volumes.
            if any(part.endswith((' ', '.')) for part in Path(target).parts):
                raise ValueError('Non-portable project target: ' + target)
            if target.casefold() in managed_aliases:
                raise ValueError('Project target must be a project-owned extension: ' + target)
            if target.casefold() in project_targets:
                raise ValueError('Duplicate project target: ' + target)
            project_targets.add(target.casefold())
            writes[consumer, target] = Path(input_path).read_bytes()
            print(f'PROJECT {path} -> {target}; connect and verify the extension before redeployment')
        if args.settings_common or args.settings_project:
            select(SETTINGS)
            common = (args.settings_common.read_bytes() if args.settings_common
                      else latest.content('common/' + COMMON_SETTINGS))
            if common is None:
                raise ValueError('Source common settings are missing')
            project = args.settings_project.read_bytes() if args.settings_project else settings_inputs[PROJECT]
            # Validate both candidates together with the same generator sync will deploy.
            generator = latest.content('common/.claude/compose-settings.py')
            if generator is None:
                raise ValueError('Source settings generator is missing')
            candidate = compose({'.claude/compose-settings.py': generator,
                                 COMMON_SETTINGS: common, PROJECT: project})
            if generated != candidate:
                raise ValueError('Split inputs do not reproduce settings.json bytes; review the candidates')
            if args.settings_common:
                existing = latest.content('common/' + COMMON_SETTINGS)
                if existing != originals[COMMON_SETTINGS] and existing != common:
                    raise ValueError('Source settings changed since deployment; reconcile first')
                key = (source, 'common/' + COMMON_SETTINGS)
                if key in writes:
                    raise ValueError('Common settings selected twice')
                writes[key] = common
                print('COMMON ' + COMMON_SETTINGS)
            if args.settings_project:
                writes[consumer, PROJECT] = project
                print('PROJECT ' + PROJECT)
        if args.apply and not selected:
            raise ValueError('--apply requires explicit file selections')
        for repo in (source, consumer):
            paths = [path for root, path in writes if root == repo]
            errors = path_errors(repo, Tree.read(repo, 'HEAD'), set(paths))
            if errors:
                raise ValueError('\n'.join(errors))
        for (repo, path), content in writes.items():
            target = repo / path
            show_diff(str(target), target.read_bytes() if target.is_file() else None, content)
        if args.apply:
            for (repo, path), content in writes.items():
                target = repo / path
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            print('Applied owner inputs only; commit source changes, then use sync and the deployment PR flow.')
        else:
            print('Preview only; select files and use --apply to write owner inputs.')
        print('Before consumer PR merge: run the normal drift check without bypass. See docs/reverse.md.')
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'reverse: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
