"""Compose the consumer's Claude entrypoint using only the standard library."""

import json
from pathlib import Path
import sys


def unique_arrays(value):
    """Normalize nested arrays before comparing complete JSON members."""
    if isinstance(value, dict):
        return {key: unique_arrays(item) for key, item in value.items()}
    if isinstance(value, list):
        result = []
        seen = set()
        for item in value:
            item = unique_arrays(item)
            identity = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if identity not in seen:
                seen.add(identity)
                result.append(item)
        return result
    return value


def merge_settings(common, project, path="$"):
    """Merge settings objects; array members retain common-first order."""
    result = dict(common)
    for key, value in project.items():
        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_settings(result[key], value, f"{path}.{key}")
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = result[key] + value
        elif type(result[key]) is not type(value) or result[key] != value:
            raise ValueError(f"Conflicting setting at {path}.{key}: common and project values differ")
    return result


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate object key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def read_settings(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=reject_duplicate_keys,
                           parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("Settings must be a JSON object")
        return value
    except (OSError, ValueError) as error:
        raise ValueError(f"{path.name}: {error}") from error


def compose_settings(directory: Path) -> bytes:
    """Return deterministic UTF-8/LF bytes without writing the entrypoint."""
    common = read_settings(directory / "settings.common.json")
    project = read_settings(directory / "settings.project.json")
    return (json.dumps(unique_arrays(merge_settings(common, project)), ensure_ascii=False,
                       indent=2, sort_keys=True) + "\n").encode("utf-8")


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    try:
        content = compose_settings(directory)
        (directory / "settings.json").write_bytes(content)
    except (OSError, ValueError) as error:
        print(f"compose-settings: {error}", file=sys.stderr)
        sys.exit(1)
