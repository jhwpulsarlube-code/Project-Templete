"""Reject direct Claude Write calls to system temp outside tool-owned state."""
import json
import fnmatch
import os
from pathlib import Path
import sys
import tempfile


def main():
    try:
        payload = json.load(sys.stdin)
        value = payload.get("tool_input", {}).get("file_path", "")
        if payload.get("tool_name") != "Write" or not value:
            return 0
        path = Path(value)
        if not path.is_absolute():
            return 0
        base = Path(tempfile.gettempdir()).resolve()
        try:
            relative = path.resolve().relative_to(base)
        except ValueError:
            return 0
        first = relative.parts[0].lower() if relative.parts else ""
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path.cwd()))
        config = project / ".claude" / "temp-write.project.json"
        patterns = json.loads(config.read_text(encoding="utf-8")).get("allow_temp_top_level", []) if config.exists() else []
        if any(fnmatch.fnmatchcase(first, pattern.lower()) for pattern in patterns):
            return 0
        print("Use .repo-temp/ for disposable work and .scratch/ for evidence; see docs/agents/temp-hygiene.md.", file=sys.stderr)
        return 2
    except (ValueError, OSError, TypeError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
