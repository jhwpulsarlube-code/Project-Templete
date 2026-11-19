"""Tracked docs stay readable: no encoding damage and no broken relative links."""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'common/test/tools/check_encoding.py'
LINK = re.compile(r'\]\(([^)\s]+)\)')
FENCE = re.compile(r'```.*?```', re.S)
CODE_SPAN = re.compile(r'`[^`\n]*`')
SCHEME = re.compile(r'^[a-z][a-z0-9+.-]*:', re.I)


def load_checker():
    spec = importlib.util.spec_from_file_location('check_encoding', CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked(*patterns):
    out = subprocess.run(['git', 'ls-files', '-z', '--', *patterns], cwd=ROOT,
                         capture_output=True, check=True).stdout
    return [ROOT / name for name in out.decode('utf-8').split('\0') if name]


def broken_links(path):
    """Relative link targets in a Markdown file that do not exist, outside code."""
    text = path.read_text(encoding='utf-8-sig')
    text = CODE_SPAN.sub('', FENCE.sub('', text))
    broken = []
    for match in LINK.finditer(text):
        target = match.group(1).split('#', 1)[0]
        if target and not SCHEME.match(target) and not (path.parent / target).exists():
            broken.append(match.group(1))
    return broken


class DocsTest(unittest.TestCase):
    def test_tracked_text_has_no_encoding_damage(self):
        checker = load_checker()
        suffixes = tuple('*' + suffix for suffix in checker.TEXT_SUFFIXES)
        problems = []
        for path in tracked(*suffixes):
            for line, reason in checker.line_problems(path.read_bytes()):
                problems.append(f'{path.relative_to(ROOT).as_posix()}:{line}: {reason}')
        self.assertEqual(problems, [])

    def test_markdown_relative_links_resolve(self):
        problems = [f'{path.relative_to(ROOT).as_posix()}: {target}'
                    for path in tracked('*.md') for target in broken_links(path)]
        self.assertEqual(problems, [])

    def test_broken_link_is_reported_and_code_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / 'sample.md'
            (Path(folder) / 'ok.md').write_text('', encoding='utf-8')
            sample.write_text('[ok](ok.md#part) [bad](missing.md) `[x](nope.md)`\n'
                              '```\n[y](nope2.md)\n```\n[web](https://example.com)\n',
                              encoding='utf-8')
            self.assertEqual(broken_links(sample), ['missing.md'])


if __name__ == '__main__':
    unittest.main()
