"""Verify selection of test configurations from explicit runner cases."""

import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


PATH = Path(__file__).resolve().parents[1] / "common/test/tools/verification_snapshot.py"
spec = importlib.util.spec_from_file_location("workflow_snapshot", PATH)
snapshot = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = snapshot
spec.loader.exec_module(snapshot)


class RunnerConfigurationTest(unittest.TestCase):
    def test_unowned_directory_is_never_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(snapshot.shutil, "rmtree") as remove, \
                    mock.patch.object(snapshot.subprocess, "run") as git:
                with self.assertRaises(snapshot.ToolError):
                    snapshot.remove_worktree(root, root)
                remove.assert_not_called()
                git.assert_not_called()

    def test_prefixed_directory_without_owner_marker_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unrelated = root / (snapshot.WORKTREE_PREFIX + "user-data")
            unrelated.mkdir()
            with self.assertRaises(snapshot.ToolError):
                snapshot.remove_worktree(root, unrelated)
            self.assertTrue(unrelated.is_dir())

    def test_missing_directory_is_already_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot.remove_worktree(root, root / (snapshot.WORKTREE_PREFIX + "missing"))

    def test_sweep_preserves_rejected_directories_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / (snapshot.WORKTREE_PREFIX + "candidate")
            candidate.mkdir()
            (candidate / snapshot.OWNER_FILE).write_text("123", encoding="ascii")
            with mock.patch.object(snapshot, "pid_alive", return_value=False), \
                    mock.patch.object(snapshot, "remove_worktree", side_effect=snapshot.ToolError("refused")):
                self.assertEqual(snapshot.sweep_stale_worktrees(root, root), [])
            self.assertTrue(candidate.is_dir())

    def test_explicit_cases_select_only_target_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="workflow path ") as directory:
            root = Path(directory)
            runner = SimpleNamespace(find_compiler=lambda: "gcc", COMPILER_FLAGS=["-std=c99"],
                                     TEST_CASES=[("first.exe", ["test/first.c"], ["-DONE"]),
                                                 ("other.exe", ["test/other.c"])])
            def dependencies(command, **kwargs):
                target = "src/file\\ with\\ spaces.c" if "-DONE" in command else "src/other.c"
                return SimpleNamespace(returncode=0, stdout=f"test.o: {target}\n", stderr="")
            with mock.patch.object(snapshot.subprocess, "run", side_effect=dependencies):
                configs = snapshot.select_configs(root, runner, "src/file with spaces.c")
            self.assertEqual([item["exe"] for item in configs], ["first.exe"])
            self.assertIn("-DONE", configs[0]["base"])


if __name__ == "__main__":
    unittest.main()
