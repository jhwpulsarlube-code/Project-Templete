#!/usr/bin/env python3
"""Record common RL78 capabilities and run optional project machine checks.

Run after cloning; --refresh updates .claude/machine.json and --nightly requires
cppcheck. Project-owned machine_checks.py provides capabilities() -> dict and
errors() -> list[str]. It may import machine_common for reusable diagnostics.
Project errors are checked on every invocation, including cached runs.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / ".claude" / "machine.json"
TOOLCHAIN_PATH = REPO_ROOT / ".claude" / "toolchain.local.json"
# Relative, so each worktree runs the hooks of its own checkout.
HOOKS_PATH = ".githooks"
CPPCHECK_DEFAULT = r"C:\Program Files\Cppcheck\cppcheck.exe"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
EGRESS_HOOK_NAME = "block_egress.py"
EGRESS_SHELL_TOOLS = {"Bash", "PowerShell"}
EGRESS_DENIED_TOOLS = ("WebSearch", "WebFetch")


def install_hooks(root: Path = REPO_ROOT) -> bool:
    """Point core.hooksPath at the tracked hooks; return False if another path is set."""
    current = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=root, capture_output=True, text=True,
    ).stdout.strip()
    if current == HOOKS_PATH:
        return True
    if current:
        configured = Path(current).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        if configured.exists():
            print(f"WARNING: core.hooksPath is already {current!r}; leaving it. "
                  f"Set it to {HOOKS_PATH!r} to enable the common hooks.")
            return False
        print(f"Replacing missing core.hooksPath {current!r} with {HOOKS_PATH!r}.")
    subprocess.run(["git", "config", "core.hooksPath", HOOKS_PATH], cwd=root, check=True)
    print(f"Installed git hooks: core.hooksPath = {HOOKS_PATH}")
    return True


def local_toolchain_path(name: str) -> str | None:
    """Read one optional path from the ignored local toolchain config."""
    try:
        value = json.loads(TOOLCHAIN_PATH.read_text(encoding="utf-8-sig")).get(name)  # PowerShell Set-Content adds a BOM
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def gcc_path() -> str | None:
    """The host-test compiler: CC, then the local toolchain file, then PATH."""
    configured = os.environ.get("CC")
    candidates = [configured] if configured else []
    candidates.extend([local_toolchain_path("gcc"), shutil.which("gcc")])
    return next((c for c in candidates if c and Path(c).is_file()), None)


def find_gcc() -> bool:
    """Return whether the host-test compiler expected by this repository exists."""
    return gcc_path() is not None


# The host runner's temporary prefix, so one antivirus exclusion covers both.
PROBE_PREFIX = "pulsarlube-host-tests-"
LAUNCH_HINT = ("Antivirus is likely blocking freshly built programs; "
               "check its log and exclusions (test/AGENTS.md \"머신 설정\").")


def launch_problem(gcc: str | None = None) -> str | None:
    """Build and run a minimal program where the host runner does; return why it cannot run, or None.

    An antivirus update can start blocking gcc output long after this machine was set up,
    so main() runs this on every invocation rather than recording it once.
    """
    gcc = gcc or gcc_path()
    if gcc is None:
        return None   # can_run_host_tests already reports the missing compiler
    with tempfile.TemporaryDirectory(prefix=PROBE_PREFIX, ignore_cleanup_errors=True) as directory:
        source = Path(directory) / "probe.c"
        program = Path(directory) / "probe.exe"
        source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        try:
            built = subprocess.run([gcc, str(source), "-o", str(program)],
                                   capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"gcc could not build a probe program ({error})"
        if built.returncode != 0:
            return f"gcc could not build a probe program: {built.stderr.strip()[-300:]}"
        try:
            ran = subprocess.run([str(program)], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"a freshly built program could not run ({error})"
    return None if ran.returncode == 0 else f"a freshly built program exited with {ran.returncode}"


def find_cc_rl() -> bool:
    """Return whether a Renesas CC-RL installation is available."""
    configured = local_toolchain_path("ccrl")
    return any(
        candidate and Path(candidate).is_file()
        for candidate in [configured, shutil.which("ccrl")]
    )


def find_cppcheck(default: str = CPPCHECK_DEFAULT) -> bool:
    """Return whether cppcheck is on PATH, at its default install path, or in the local toolchain config."""
    candidates = [shutil.which("cppcheck"), default, local_toolchain_path("cppcheck")]
    return any(
        candidate and not candidate.startswith("<") and Path(candidate).is_file()
        for candidate in candidates
    )


def find_post_link_deps() -> bool:
    """Return whether the Application-0 post-link step can import intelhex."""
    python = shutil.which("python")
    if python is None:
        return False
    try:
        result = subprocess.run([python, "-c", "import intelhex"],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def nightly_errors(config: dict[str, object], has_cppcheck: bool) -> list[str]:
    """Problems that make this machine unfit for the nightly check; empty for other machines."""
    if config.get("nightly") is True and not has_cppcheck:
        return [
            "nightly machine requires cppcheck: put it on PATH, install it to "
            f"{CPPCHECK_DEFAULT!r}, or set \"cppcheck\" in .claude/toolchain.local.json"
        ]
    return []


def find_cubesuite() -> bool:
    """Return whether the local toolchain config names the batch-capable CubeSuite+.exe.

    The GUI CubeSuiteW+.exe exits with no output under /bb, so it does not count.
    """
    configured = local_toolchain_path("cubesuite")
    return bool(
        configured
        and Path(configured).name.lower() == "cubesuite+.exe"
        and Path(configured).is_file()
    )


def read_settings(path: Path) -> dict | None:
    """Parse a Claude Code settings file; None if it is missing or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def runs_hook(command: str, hook_file: str) -> bool:
    """Whether the command runs python on the tracked hook file under $CLAUDE_PROJECT_DIR (not just names it)."""
    return isinstance(command, str) and re.fullmatch(
        rf'python(?:\.exe)?(?: -\w+)* "?\$CLAUDE_PROJECT_DIR/\.claude/hooks/{re.escape(hook_file)}"?', command.strip()
    ) is not None


def egress_hook_wired(settings: dict, hook_file: str = EGRESS_HOOK_NAME,
                      shell_tools: set[str] | None = None) -> bool:
    """Whether some PreToolUse entry runs the egress hook for both the Bash and PowerShell tools."""
    shell_tools = EGRESS_SHELL_TOOLS if shell_tools is None else shell_tools
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    entries = hooks.get("PreToolUse", [])
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        matcher = entry.get("matcher") or "*"
        if not isinstance(matcher, str) or not isinstance(entry.get("hooks", []), list):
            continue
        covered = shell_tools if matcher == "*" else set(matcher.split("|"))
        commands = [h.get("command", "") for h in entry.get("hooks", [])
                    if isinstance(h, dict) and h.get("type") == "command"]
        if shell_tools <= covered and any(runs_hook(c, hook_file) for c in commands):
            return True
    return False


def egress_errors(
    settings_path: Path | None = None,
    hook_path: Path | None = None,
    other_settings: list[Path] | None = None,
    *,
    shell_tools: set[str] | None = None,
    denied_tools: tuple[str, ...] = EGRESS_DENIED_TOOLS,
    disable_connectors: bool = True,
) -> list[str]:
    """Reasons the code-egress block is not switched on for this checkout; empty when it is.

    Checks the tracked settings and hook file, not the live session: a hook script that exists but is
    not wired, a deny list that lost WebFetch, or `disableAllHooks` in a user or local settings file
    each leave the block off while every file is still present.
    """
    settings_path = settings_path or SETTINGS_PATH
    hook_path = hook_path or REPO_ROOT / ".claude" / "hooks" / EGRESS_HOOK_NAME
    if other_settings is None:
        other_settings = [settings_path.with_name("settings.local.json"), Path.home() / ".claude" / "settings.json"]
    errors = []
    if not hook_path.is_file():
        errors.append(f"{hook_path.name} is missing from .claude/hooks (AGENTS.md \"Claude Code 사용 시\")")
    settings = read_settings(settings_path)
    if settings is None:
        errors.append(f"cannot read {settings_path.name}: the code-egress wiring lives there")
    else:
        if not egress_hook_wired(settings, hook_path.name, shell_tools):
            errors.append(f"{settings_path.name} must run {hook_path.name} as a PreToolUse hook")
        permissions = settings.get("permissions", {})
        deny = permissions.get("deny", []) if isinstance(permissions, dict) else []
        if not isinstance(deny, list):
            deny = []
        missing = [tool for tool in denied_tools if tool not in deny]
        if missing:
            errors.append(f"{settings_path.name} permissions.deny must list {', '.join(missing)}")
        if disable_connectors and settings.get("disableClaudeAiConnectors") is not True:
            errors.append(f"{settings_path.name} must set disableClaudeAiConnectors to true")
    for path in [settings_path, *other_settings]:
        other = read_settings(path)
        if other is None and path.exists() and path != settings_path:
            errors.append(f"cannot read {path}: it may set disableAllHooks")
        elif other and other.get("disableAllHooks") is True:
            errors.append(f"{path} sets disableAllHooks, which switches the code-egress hook off")
    return errors


def detect_capabilities() -> dict[str, bool]:
    """Detect only the capabilities relevant to this repository."""
    has_gcc = find_gcc()
    has_cc_rl = find_cc_rl()
    return {
        "can_run_host_tests": has_gcc and (REPO_ROOT / "test" / "run_tests.py").is_file(),
        "can_build_product": has_cc_rl and any(REPO_ROOT.glob("*.mtpj")),
        "can_verify_cc_rl_hex": has_cc_rl,
        "can_run_post_link": find_post_link_deps(),
        "has_cubesuite": find_cubesuite(),
        "can_run_cppcheck": find_cppcheck(),
    }


def load_config() -> dict[str, object]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read {CONFIG_PATH.relative_to(REPO_ROOT)}: {error}") from error


def print_capabilities(config: dict[str, object], prefix: str) -> None:
    capabilities = config["capabilities"]
    assert isinstance(capabilities, dict)
    print(prefix)
    for name, available in capabilities.items():
        print(f"  {'yes' if available else 'no ':3} {name}")
    if capabilities.get("can_run_post_link") is False:
        print("  note: can_run_post_link is no: `pip install intelhex` for the python on PATH,")
        print("        then rerun with --refresh. Until then CS+ builds report a failed")
        print("        Application-0 project even though compile and link succeed.")


def project_checks() -> tuple[dict, list[str]]:
    """Load only this checkout's optional extension, without requiring packages."""
    path = REPO_ROOT / ".claude" / "machine_checks.py"
    if not path.exists():
        return {}, []
    spec = importlib.util.spec_from_file_location("machine_checks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    capabilities = module.capabilities()
    errors = module.errors()
    if not isinstance(capabilities, dict) or not all(isinstance(k, str) for k in capabilities):
        raise ValueError("machine_checks.capabilities() must return a dict with string keys")
    if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
        raise ValueError("machine_checks.errors() must return list[str]")
    return capabilities, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-detect and replace the existing local capability record",
    )
    parser.add_argument(
        "--nightly",
        action="store_true",
        help="record this machine as a nightly-check machine (cppcheck becomes required)",
    )
    args = parser.parse_args()

    install_hooks()
    problem = launch_problem()
    if problem:
        print(f"WARNING: {problem}. {LAUNCH_HINT}")
    previous = load_config() if CONFIG_PATH.exists() else {}
    cached = bool(previous) and not args.refresh and not args.nightly
    common = detect_capabilities()
    try:
        project, errors = project_checks()
        collisions = common.keys() & project.keys()
        if collisions:
            raise ValueError("project capability keys overlap common keys: " + ", ".join(sorted(collisions)))
    except Exception as error:
        print(f"error: machine_checks: {error}", file=sys.stderr)
        return 1
    config = dict(previous) if cached else {
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "capabilities": common,
    }
    # Cached common detections retain their original timestamp; extension results
    # are always current and removed extension keys do not survive a later run.
    if cached:
        config["capabilities"] = {key: previous["capabilities"].get(key, value)
                                  for key, value in common.items()}
    config["capabilities"].update(project)
    if args.nightly or previous.get("nightly") is True:
        config["nightly"] = True
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print_capabilities(config, "Already initialized; use --refresh to re-detect." if cached
                       else "Initialized local repository capabilities.")
    errors.extend(nightly_errors(config, find_cppcheck()))
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.modules["machine_common"] = sys.modules[__name__]
    sys.exit(main())
