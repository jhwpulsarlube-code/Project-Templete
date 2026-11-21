"""Adapt Codex lifecycle events to the shared repository checks."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH_PREFIXES = ("*** Add File: ", "*** Update File: ", "*** Move to: ")


def shared_check(script, payload, cwd):
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / ".claude" / "hooks" / script)],
        input=json.dumps(payload), encoding="utf-8", capture_output=True, cwd=cwd,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT), "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    if result.returncode:
        return result.stderr.strip() or f"Shared workflow check failed: {script}"
    return None


def pre_tool_use(payload):
    tool = payload["tool_name"]
    arguments = payload["tool_input"]
    cwd = Path(arguments.get("workdir") or payload["cwd"])
    if tool == "Bash":
        return shared_check("guard_main_branch.py", payload, cwd)
    if tool == "apply_patch":
        for line in arguments["command"].splitlines():
            for prefix in PATCH_PATH_PREFIXES:
                if line.startswith(prefix):
                    path = (cwd / line[len(prefix):]).resolve()
                    write = {"tool_name": "Write", "tool_input": {"file_path": str(path)}}
                    for script in ("guard_scratch_layout.py", "guard_temp_write.py"):
                        if message := shared_check(script, write, cwd):
                            return message
                    break
    return None


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read())
        message = pre_tool_use(payload) if payload["hook_event_name"] == "PreToolUse" else None
    except (KeyError, OSError, TypeError, ValueError) as error:
        message = f"Unable to check the Codex workflow policy: {error}"
    if message:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
