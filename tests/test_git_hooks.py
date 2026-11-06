"""Exercise deployed dispatchers through Git's hook runner and real commits."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "common" / ".githooks"


class GitHooksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hook repo ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Hook Test")
        self.git("config", "user.email", "hook@example.invalid")
        self.git("config", "core.hooksPath", ".githooks")
        self.hooks = self.repo / ".githooks"
        if SOURCE.exists():
            shutil.copytree(SOURCE, self.hooks)

    def git(self, *args, **kwargs):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, **kwargs,
        )

    def extension(self, hook, name, body):
        directory = self.hooks / (hook + ".d")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(body, encoding="utf-8", newline="\n")

    def test_real_commit_runs_both_hooks_in_name_order(self):
        for hook in ("pre-commit", "post-commit"):
            for name in ("20 last", "10 first"):
                self.extension(hook, name, f"echo '{hook} {name}'\n")
        result = self.git("commit", "--allow-empty", "-m", "test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.decode().splitlines(), [
            "pre-commit 10 first", "pre-commit 20 last",
            "post-commit 10 first", "post-commit 20 last",
        ])

    def common_check(self, code):
        # Substitute a deployed check at the process boundary, allowing checks
        # to consume stdin or fail before the later drift-check ticket exists.
        (self.hooks / "common_checks.py").write_text(
            code, encoding="utf-8", newline="\n",
        )

    def run_hook(self, hook, *arguments, data=b""):
        source = self.repo / "input.bin"
        source.write_bytes(data)
        return self.git("hook", "run", f"--to-stdin={source}", hook, "--", *arguments)

    def test_every_process_receives_arguments_stdin_and_selected_python(self):
        data = b"first\x00line\r\nlast without newline"
        for hook in ("pre-commit", "post-commit"):
            with self.subTest(hook=hook):
                self.common_check(
                    "import sys\n"
                    "print('common:' + repr(sys.argv[1:]), flush=True)\n"
                    "sys.stdout.flush()\n"
                    "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
                )
                for name in ("10 first", "20 last"):
                    self.extension(hook, name,
                        '"$PY" -c \'import os, sys; '
                        'assert os.environ["PY"] in ("py", "python"); '
                        'print("extension:" + repr(sys.argv[1:]), flush=True); '
                        'sys.stdout.buffer.write(sys.stdin.buffer.read())\' "$@"\n')
                result = self.run_hook(hook, "space argument", "", "*", data=data)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = result.stdout + result.stderr
                self.assertEqual(output.count(data), 3)
                self.assertIn(
                    f"common:['{hook}', 'space argument', '', '*']".encode(), output,
                )
                self.assertEqual(output.count(b"extension:['space argument', '', '*']"), 2, output)
                self.assertLess(output.index(b"common:"), output.index(b"extension:"))

    def test_pre_commit_failure_blocks_remaining_extensions_and_commit(self):
        self.extension("pre-commit", "10 fail", "echo failed >&2\nexit 7\n")
        self.extension("pre-commit", "20 forbidden", "echo forbidden >&2\n")
        self.extension("post-commit", "10 forbidden", "echo forbidden >&2\n")
        result = self.git("commit", "--allow-empty", "-m", "blocked")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"failed", result.stderr)
        self.assertNotIn(b"forbidden", result.stderr)
        self.assertNotEqual(self.git("rev-parse", "--verify", "HEAD").returncode, 0)

    def test_common_check_failure_blocks_extensions(self):
        self.common_check("import sys\nprint('common failed')\nsys.exit(9)\n")
        self.extension("pre-commit", "10 forbidden", "echo forbidden\n")
        result = self.run_hook("pre-commit")
        self.assertEqual(result.returncode, 9)
        self.assertIn(b"common failed", result.stdout + result.stderr)
        self.assertNotIn(b"forbidden", result.stdout + result.stderr)

    def test_missing_or_empty_extensions_still_run_common_checks(self):
        self.common_check("print('common ran')\n")
        for hook in ("pre-commit", "post-commit"):
            for empty in (False, True):
                with self.subTest(hook=hook, empty=empty):
                    if empty:
                        (self.hooks / (hook + ".d")).mkdir()
                    result = self.run_hook(hook)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b"common ran", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
