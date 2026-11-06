"""Run common checks before project-owned shell extensions."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


def shell_command(arguments: list[str]) -> list[str] | str:
    if os.name != "nt":
        return arguments
    # MSYS sh expands unquoted wildcards in native Windows command lines.
    # Quote every argument, including those list2cmdline would leave bare.
    def quote(argument: str) -> str:
        escaped = re.sub(r'(\\*)"', lambda m: m[1] * 2 + '\\"', argument)
        escaped = re.sub(r'\\+$', lambda m: m[0] * 2, escaped)
        return '"' + escaped + '"'

    return " ".join(quote(argument) for argument in arguments)


def main() -> int:
    hook, *arguments = sys.argv[1:]
    directory = Path(__file__).resolve().parent
    # Buffer once so every check/extension receives the original bytes, even
    # after an earlier process consumes its input completely.
    data = sys.stdin.buffer.read()
    result = subprocess.run(
        [os.environ["PY"], str(directory / "common_checks.py"), hook, *arguments],
        input=data,
    )
    if result.returncode:
        return result.returncode
    extensions = directory / (hook + ".d")
    if extensions.is_dir():
        for extension in sorted(extensions.iterdir()):
            if extension.is_file():
                result = subprocess.run(
                    shell_command(["sh", str(extension), *arguments]), input=data,
                )
                if result.returncode:
                    return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
