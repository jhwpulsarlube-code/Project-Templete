"""Common Git hook checks, runnable from a standalone consumer."""

from pathlib import Path
import os
import sys

from check_drift import check_worktree


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == 'pre-commit':
        if os.environ.get('WORKFLOW_SKIP_DRIFT_ONCE') == '1':
            print('workflow-drift: one-command bypass active. PR merge requires a normal check '
                  'without WORKFLOW_SKIP_DRIFT_ONCE.', file=sys.stderr)
            return 0
        return check_worktree(Path.cwd())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
