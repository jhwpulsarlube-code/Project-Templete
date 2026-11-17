"""Behavior and portability of the remaining deployed workflow assets."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

COMMON = Path(__file__).resolve().parents[1] / "common"


class CommonAssetsTest(unittest.TestCase):
    def test_project_policy_changes_invalidate_measurement_evidence(self):
        sys.path.insert(0, str(COMMON / 'test/tools'))
        import exhaustive_mutation as em
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy = root / 'docs/agents/host-test-project.md'
            policy.parent.mkdir(parents=True)
            policy.write_text('### Safety policy\n', encoding='utf-8')
            config = root / 'test/host-test.project.json'
            config.parent.mkdir()
            config.write_text('{"extra_includes": []}', encoding='utf-8')
            before = em.capture_review_context(root, [], 'fixture compiler')
            config.write_text('{"extra_includes": ["include"]}', encoding='utf-8')
            changed = em.capture_review_context(root, [], 'fixture compiler')
            self.assertNotEqual(before, changed)
            policy.write_text('### Changed safety policy\n', encoding='utf-8')
            self.assertNotEqual(changed, em.capture_review_context(root, [], 'fixture compiler'))

    def test_branch_guard_combines_repository_selection_and_quoted_refs(self):
        spec = importlib.util.spec_from_file_location("guard", COMMON / ".claude/hooks/guard_main_branch.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for command, branch, expected in [
            ('git -C other commit -m message', 'main', False),
            ('cd other; git commit -m message', 'main', False),
            ('git push origin "HEAD:main"', 'feature', True),
            ('git branch -f "main" HEAD', 'feature', True),
            ('@"\n$(git push origin HEAD:main)\n"@', 'feature', True),
            ("@'\ngit push origin HEAD:main\n'@", 'feature', False),
        ]:
            with self.subTest(command=command):
                result = guard.verdict({"tool_name": "PowerShell", "tool_input": {"command": command}}, branch, lambda path: "feature")
                self.assertEqual(bool(result), expected)

    def test_temp_exceptions_are_project_owned(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / '.claude').mkdir()
            payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(Path(tempfile.gettempdir()) / "codex" / "example.txt")}})
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
            def run():
                return subprocess.run([sys.executable, '-B', str(COMMON / '.claude/hooks/guard_temp_write.py')], input=payload, text=True, capture_output=True, env=env)
            self.assertEqual(run().returncode, 2)
            (project / '.claude/temp-write.project.json').write_text(json.dumps({"allow_temp_top_level": ["codex"]}))
            self.assertEqual(run().returncode, 0)

    def test_scratch_issue_tracker_policy_is_project_owned(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / '.claude').mkdir()
            payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(project / ".scratch/feature/issues/01-ticket.md")}})
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
            def run():
                return subprocess.run([sys.executable, '-B', str(COMMON / '.claude/hooks/guard_scratch_layout.py')], input=payload.encode('utf-8'), capture_output=True, env=env)
            config = project / '.claude/scratch-layout.project.json'
            self.assertEqual(run().returncode, 0)
            config.write_text(json.dumps({"issue_tracker": "local"}))
            self.assertEqual(run().returncode, 0)
            config.write_text(json.dumps({"issue_tracker": "github"}))
            self.assertEqual(run().returncode, 2)
            config.write_text(json.dumps({"issue_tracker": "other"}))
            self.assertEqual(run().returncode, 2)

    def test_encoding_check_skips_only_declared_binary_files(self):
        sys.path.insert(0, str(COMMON / 'test/tools'))
        import check_encoding
        env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        for attributes, reported in [(b'fill.bin binary\n', False), (b'* -text\n', True), (b'', True)]:
            with self.subTest(attributes=attributes), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                subprocess.run(['git', 'init', '-q', str(root)], check=True, env=env)
                (root / '.gitattributes').write_bytes(attributes)
                # 0xFF fill has no NUL, so Git's content detection calls it text.
                (root / 'fill.bin').write_bytes(b'\xff' * 64)
                subprocess.run(['git', '-C', str(root), 'add', '.'], check=True, env=env)
                paths = {path for path, _, _ in check_encoding.all_problems(root)}
                self.assertEqual('fill.bin' in paths, reported)

    def test_rule_keys_are_not_counted_as_line_citations(self):
        sys.path.insert(0, str(COMMON / 'test/tools'))
        import check_ticket_links
        found = check_ticket_links.CITATION.findall('sensor/sensor.c:10.7 key, sensor/sensor.c:10 and lubx.h:58.')
        self.assertEqual(found, ['sensor/sensor.c:10', 'lubx.h:58'])

    def test_stack_depth_rejects_a_build_without_main(self):
        sys.path.insert(0, str(COMMON / 'test/tools'))
        import stack_depth
        with tempfile.TemporaryDirectory() as folder:
            build = Path(folder)
            (build / 'work.asm').write_text('_work:\n\t.STACK _work = 4\n\tret\n', encoding='utf-8')
            with self.assertRaises(stack_depth.StackDepthError):
                stack_depth.analyze(build)
            (build / 'main.asm').write_text('_main:\n\t.STACK _main = 2\n\tcall !!_work\n', encoding='utf-8')
            self.assertEqual(stack_depth.analyze(build)['main'][0], 6)

    def test_skill_links_resolve_in_deployment(self):
        for path in (COMMON / 'docs/agents').rglob('*.md'):
            self.assertNotIn('install.py', path.read_text(encoding='utf-8'), str(path))
        for path in (COMMON / '.claude/skills').rglob('*.md'):
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('install.py', text)
            self.assertNotIn('seeds/', text)
            for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', text):
                if '://' not in link:
                    self.assertTrue((path.parent / link).exists(), (path, link))

    def test_checkout_preserves_canonical_bytes_and_root_attributes(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            shutil.copytree(COMMON, repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            (repo / '.gitattributes').write_bytes(b'* text eol=crlf\n')
            def git(*args):
                subprocess.run(['git', '-C', folder, *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'core.autocrlf', 'true')
            git('add', '.')
            for path in COMMON.rglob('*'):
                if path.is_file() and "__pycache__" not in path.parts:
                    (repo / path.relative_to(COMMON)).unlink()
            git('checkout-index', '-a', '-f')
            for path in COMMON.rglob('*'):
                if path.is_file() and "__pycache__" not in path.parts:
                    self.assertEqual(path.read_bytes(), (repo / path.relative_to(COMMON)).read_bytes(), str(path))
            self.assertEqual((repo / '.gitattributes').read_bytes(), b'* text eol=crlf\n')


if __name__ == '__main__':
    unittest.main()
