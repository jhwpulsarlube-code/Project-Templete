"""Exercise the deployed CLI in an isolated consumer, without user packages."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "common" / ".claude"


class ComposeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="consumer settings ")
        self.addCleanup(self.temporary.cleanup)
        self.consumer = Path(self.temporary.name)
        self.claude = self.consumer / ".claude"
        self.claude.mkdir()
        shutil.copyfile(SOURCE / "compose-settings.py", self.claude / "compose-settings.py")
        self.output = self.claude / "settings.json"

    def write_input(self, name, value):
        (self.claude / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def run_compose(self):
        return subprocess.run(
            [sys.executable, "-I", "-S", ".claude/compose-settings.py"],
            cwd=self.consumer, capture_output=True, text=True, encoding="utf-8",
        )

    def test_consumer_merges_nested_objects_and_permissions(self):
        self.write_input("settings.common.json", {
            "permissions": {"allow": ["Bash(git status)"], "deny": ["WebFetch"]},
            "env": {"COMMON": "yes"},
        })
        self.write_input("settings.project.json", {
            "permissions": {"allow": ["Bash(python test/run_tests.py)"], "deny": ["WebSearch"]},
            "env": {"PROJECT": "local"},
        })
        result = self.run_compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_bytes()), {
            "permissions": {
                "allow": ["Bash(git status)", "Bash(python test/run_tests.py)"],
                "deny": ["WebFetch", "WebSearch"],
            },
            "env": {"COMMON": "yes", "PROJECT": "local"},
        })

    def test_arrays_are_stable_unions_including_nested_hooks(self):
        hook = {"matcher": "Write", "hooks": [
            {"type": "command", "command": "python guard.py"},
            {"command": "python guard.py", "type": "command"},
        ]}
        self.write_input("settings.common.json", {
            "permissions": {"allow": ["Read", "Read"], "deny": ["WebFetch"]},
            "hooks": {"PreToolUse": [hook, hook]},
        })
        self.write_input("settings.project.json", {
            "permissions": {"allow": ["Read", "Write", "Write"], "deny": ["WebFetch", "WebSearch"]},
            "hooks": {"PreToolUse": [hook, {"matcher": "Bash", "hooks": []}]},
            "extra": [True, 1, False, 0, True],
        })
        result = self.run_compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.output.read_bytes()), {
            "permissions": {"allow": ["Read", "Write"], "deny": ["WebFetch", "WebSearch"]},
            "hooks": {"PreToolUse": [
                {"matcher": "Write", "hooks": [{"type": "command", "command": "python guard.py"}]},
                {"matcher": "Bash", "hooks": []},
            ]},
            "extra": [True, 1, False, 0],
        })

    def test_conflicts_report_json_path_and_leave_entrypoint_untouched(self):
        for common, project in [("default", "acceptEdits"), (True, 1), ({}, []), (None, False)]:
            with self.subTest(common=common, project=project):
                self.write_input("settings.common.json", {"permissions": {"defaultMode": common}})
                self.write_input("settings.project.json", {"permissions": {"defaultMode": project}})
                self.output.write_bytes(b"previous entrypoint\r\n")
                result = self.run_compose()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("$.permissions.defaultMode", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.output.read_bytes(), b"previous entrypoint\r\n")

    def test_invalid_inputs_fail_without_overwriting_entrypoint(self):
        for invalid in ['[]', 'null', '{broken', '{"env": {"A": 1, "A": 2}}', '{"value": NaN}']:
            for name in ["settings.common.json", "settings.project.json"]:
                with self.subTest(invalid=invalid, name=name):
                    self.write_input("settings.common.json", {})
                    self.write_input("settings.project.json", {})
                    (self.claude / name).write_text(invalid, encoding="utf-8")
                    self.output.write_bytes(b"previous\n")
                    result = self.run_compose()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(name, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(self.output.read_bytes(), b"previous\n")

    def test_missing_inputs_are_errors_even_without_existing_entrypoint(self):
        for name in ["settings.common.json", "settings.project.json"]:
            with self.subTest(name=name):
                self.write_input("settings.common.json", {})
                self.write_input("settings.project.json", {})
                (self.claude / name).unlink()
                result = self.run_compose()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(name, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.output.exists())

    def test_equal_scalars_and_empty_project_are_supported(self):
        common = {"enabled": True, "count": 1, "label": "same", "optional": None}
        self.write_input("settings.common.json", common)
        for project in [common, {}]:
            with self.subTest(project=project):
                self.write_input("settings.project.json", project)
                result = self.run_compose()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(self.output.read_bytes()), common)

    def test_shipped_common_regenerates_identical_bytes_in_isolated_consumer(self):
        shutil.copyfile(SOURCE / "settings.common.json", self.claude / "settings.common.json")
        self.write_input("settings.project.json", {
            "env": {"LABEL": "소비 프로젝트", "PATH_HINT": "C:\\작업\\repo"},
            "permissions": {"allow": ["Bash(python test/run_tests.py)"]},
        })
        result = self.run_compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.output.read_bytes()
        self.assertIn("소비 프로젝트".encode("utf-8"), first)
        self.assertNotIn(b"\r", first)
        self.assertFalse(first.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(first.endswith(b"\n"))
        settings = json.loads(first)
        self.assertEqual([item["matcher"] for item in settings["hooks"]["PreToolUse"]],
                         ["Skill", "Write", "Bash", "PowerShell", "Write"])
        self.assertEqual(settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"],
                         'python -B "$CLAUDE_PROJECT_DIR/.claude/hooks/inject_coding_standards.py"')
        self.assertEqual(settings["env"]["PATH_HINT"], "C:\\작업\\repo")
        self.output.write_bytes(b"manually modified settings")
        for _ in range(2):
            result = self.run_compose()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.output.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
