"""Exercise the deployed machine entrypoint in disposable consumer repositories."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'common' / '.claude' / 'init-machine.py'


class MachineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='machine consumer ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.claude = self.root / '.claude'
        self.claude.mkdir()
        (self.root / '.githooks').mkdir()
        shutil.copyfile(SOURCE, self.claude / SOURCE.name)
        self.git('init', '-q')
        self.git('config', '--local', 'core.hooksPath', '.githooks')
        # Avoid the real compiler, antivirus and global Git configuration.
        self.extension('def capabilities(): return {}\ndef errors(): return []\n')
        self.harness = self.root / 'run.py'
        self.harness.write_text('''import runpy, shutil, sys
shutil.which = lambda name: None
sys.argv = ['init-machine.py'] + sys.argv[1:]
runpy.run_path('.claude/init-machine.py', run_name='__main__')
''')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def extension(self, source):
        (self.claude / 'machine_checks.py').write_text(source, encoding='utf-8')

    def run_machine(self, *args):
        return subprocess.run([sys.executable, '-I', '-S', str(self.harness), *args],
                              cwd=self.root, capture_output=True, text=True,
                              env={k: v for k, v in os.environ.items() if k != "CC"})

    def config(self):
        return json.loads((self.claude / 'machine.json').read_text())

    def test_absent_extension_and_common_capabilities(self):
        (self.claude / 'machine_checks.py').unlink()
        result = self.run_machine()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('can_run_cppcheck', self.config()['capabilities'])
        self.assertIn('has_cubesuite', self.config()['capabilities'])

    def test_extension_capabilities_and_live_errors_on_cached_run(self):
        self.extension('def capabilities(): return {"project_ready": True}\ndef errors(): return []\n')
        self.assertEqual(self.run_machine().returncode, 0)
        self.assertTrue(self.config()['capabilities']['project_ready'])
        self.extension('def capabilities(): return {"project_ready": False}\ndef errors(): return ["project wiring missing"]\n')
        result = self.run_machine()
        self.assertEqual(result.returncode, 1)
        self.assertIn('project wiring missing', result.stderr)
        self.assertFalse(self.config()['capabilities']['project_ready'])

    def test_collision_does_not_write_record_even_when_cached(self):
        self.assertEqual(self.run_machine().returncode, 0)
        original = (self.claude / 'machine.json').read_bytes()
        self.extension('def capabilities(): return {"can_run_cppcheck": True}\ndef errors(): return []\n')
        result = self.run_machine()
        self.assertEqual(result.returncode, 1)
        self.assertIn('overlap', result.stderr)
        self.assertEqual((self.claude / 'machine.json').read_bytes(), original)

    def test_missing_hooks_path_replaced_existing_path_preserved(self):
        self.git('config', 'core.hooksPath', 'missing hooks')
        result = self.run_machine()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Replacing missing', result.stdout)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath'), '.githooks')
        (self.root / 'custom hooks').mkdir()
        self.git('config', 'core.hooksPath', 'custom hooks')
        result = self.run_machine()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('WARNING', result.stdout)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath'), 'custom hooks')

    def test_invalid_extension_contract_fails_cleanly(self):
        self.extension('def capabilities(): return []\ndef errors(): return []\n')
        result = self.run_machine()
        self.assertEqual(result.returncode, 1)
        self.assertIn('must return a dict', result.stderr)

    def test_egress_policy_is_opt_in_and_diagnostics_reusable(self):
        self.extension('''from machine_common import egress_errors
def capabilities(): return {}
def errors(): return egress_errors(other_settings=[])
''')
        result = self.run_machine()
        self.assertEqual(result.returncode, 1)
        self.assertIn('code-egress wiring', result.stderr)
        hooks = self.claude / 'hooks'
        hooks.mkdir()
        (hooks / 'block_egress.py').touch()
        settings = {'hooks': {'PreToolUse': [{'matcher': 'Bash|PowerShell', 'hooks': [
            {'type': 'command', 'command': 'python "$CLAUDE_PROJECT_DIR/.claude/hooks/block_egress.py"'}]}]},
            'permissions': {'deny': ['WebFetch', 'WebSearch']}, 'disableClaudeAiConnectors': True}
        (self.claude / 'settings.json').write_text(json.dumps(settings))
        result = self.run_machine()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.claude / 'settings.local.json').write_text('{"disableAllHooks": true}')
        self.extension('''from machine_common import egress_errors
def capabilities(): return {}
def errors(): return egress_errors(other_settings=[__import__('pathlib').Path('.claude/settings.local.json')])
''')
        result = self.run_machine()
        self.assertEqual(result.returncode, 1)
        self.assertIn('disableAllHooks', result.stderr)

    def test_cubesuite_batch_executable_required(self):
        for name, expected in [('CubeSuiteW+.exe', False), ('CubeSuite+.exe', True)]:
            executable = self.root / name
            executable.touch()
            (self.claude / 'toolchain.local.json').write_text(json.dumps({'cubesuite': str(executable)}))
            result = self.run_machine('--refresh')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.config()['capabilities']['has_cubesuite'], expected)

    def test_compiler_launch_failure_warns_without_failing(self):
        self.harness.write_text("""import runpy, shutil, subprocess, sys
original_run = subprocess.run
shutil.which = lambda name: sys.executable if name == 'gcc' else None
def run(command, **kwargs):
    if command[0] == sys.executable:
        raise PermissionError('blocked by antivirus')
    return original_run(command, **kwargs)
subprocess.run = run
sys.argv = ['init-machine.py']
runpy.run_path('.claude/init-machine.py', run_name='__main__')
""")
        result = self.run_machine()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('WARNING', result.stdout)
        self.assertIn('Antivirus', result.stdout)

    def test_post_link_checks_path_python_import(self):
        for exit_code, expected in [(0, True), (1, False)]:
            self.harness.write_text("""import runpy, shutil, subprocess, sys
original_run = subprocess.run
shutil.which = lambda name: 'path-python' if name == 'python' else None
def run(command, **kwargs):
    if command[0] == 'path-python':
        assert command == ['path-python', '-c', 'import intelhex']
        assert kwargs['timeout'] == 30
        return subprocess.CompletedProcess(command, EXIT_CODE)
    return original_run(command, **kwargs)
subprocess.run = run
sys.argv = ['init-machine.py', '--refresh']
runpy.run_path('.claude/init-machine.py', run_name='__main__')
""".replace('EXIT_CODE', str(exit_code)))
            result = self.run_machine()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.config()['capabilities']['can_run_post_link'], expected)


if __name__ == '__main__':
    unittest.main()
