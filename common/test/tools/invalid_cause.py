#!/usr/bin/env python3
"""전수 변이 보고서의 invalid 변이마다 컴파일 오류의 원인을 낸다.

    python -B test/tools/invalid_cause.py <report.json> [--revision HEAD] [--limit N]

invalid 변이는 죽음도 생존도 아니다. 원인(항상 거짓·참 비교, 빈 본문, 쓰지 않는 변수 …)을 문서에 적으려면 그 변이 줄을
넣어 실제로 빌드해 봐야 한다. 이 도구는 측정과 같은 격리 사본(revision 사본 + 현재 변경분)에서 변이 줄을 하나씩
넣어 invalid 로 본 구성의 게이트 플래그로 컴파일하고, 첫 오류를 원인별로 묶어 출력한다. 원본 작업 트리는 건드리지 않는다
(실행 전후 상태가 다르면 오류).

종료 코드: 0 모든 invalid 변이의 컴파일 실패를 재현했다, 1 재현하지 못한 변이가 있다(빌드가 통과했다 — invalid 사유가 컴파일이 아니다),
2 입력 오류(보고서의 원래 줄이 현재 소스와 다르다 = 측정 뒤 소스가 바뀌었다, 변이 없는 원본이 컴파일되지 않는다 포함). 한 줄에 닫히지 않는 변이(줄 바꿈을 가로지르는 구간)는 지원하지 않는다.
절차는 docs/agents/mutation-tool-reference.md "invalid 원인".
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import exhaustive_mutation as em  # noqa: E402

LOCATION = re.compile(r"^.*?:\d+:\d+: ")
WARNING_TAG = re.compile(r"\[(-W[\w=-]+)\]\s*$")


class InputError(Exception):
    pass


def load_invalid(path):
    """보고서에서 (target, invalid 결과 목록)을 읽는다. 측정이 유효하지 않으면 입력 오류다."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        target, results = report["target"], report["results"]
        if report["valid"] is not True:
            raise ValueError("valid 가 true 가 아니다")
        return target, [r for r in results if r["status"] == "invalid"], report["configs"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InputError("{}: 보고서를 읽을 수 없다 ({}: {})".format(
            path, type(error).__name__, error)) from error


def replace_line(source, line, mutated):
    """source 의 line(1부터) 줄을 mutated 로 바꾼다. 원래 줄 끝(CRLF·LF)은 유지한다."""
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        raise InputError("변이 줄 {} 이 파일 범위({}줄) 밖이다".format(line, len(lines)))
    eol = "\r" if lines[line - 1].endswith("\r") else ""
    lines[line - 1] = mutated.rstrip("\r") + eol
    return "\n".join(lines)


def first_error(stderr):
    """컴파일러 stderr 의 첫 error 줄(없으면 None)."""
    for text in stderr.splitlines():
        if " error: " in text or text.startswith("error: "):
            return text.strip()
    return None


def cause_of(error_line):
    """오류 줄을 원인 이름으로 줄인다: 경고 플래그가 있으면 그것, 없으면 위치를 뗀 메시지."""
    tag = WARNING_TAG.search(error_line)
    if tag:
        return tag.group(1)
    message = error_line.split(" error: ", 1)[-1]
    return LOCATION.sub("", message).strip()


def check_original(source, result):
    """보고서가 적은 원래 줄이 현재 소스의 그 줄과 같아야 한다. 다르면 측정 뒤에 소스가 바뀐 것이라 줄 번호가 어긋난다."""
    lines = source.split("\n")
    line = result["line"]
    if not 1 <= line <= len(lines) or lines[line - 1].strip() != result["original"].strip():
        raise InputError("{}: 줄 {} 이 측정 때와 다르다 — 측정 뒤 소스가 바뀌었으니 다시 측정한다".format(result["id"], line))


def build(worktree, out, config):
    return subprocess.run(config["base"] + ["-o", str(Path(out) / config["exe"])], cwd=worktree,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def check_baseline_builds(worktree, out, config):
    """변이를 넣지 않은 원본이 컴파일돼야 한다. 안 되면 invalid 원인을 가를 수 없다(원본의 오류가 섞인다)."""
    result = build(worktree, out, config)
    if result.returncode:
        raise InputError("변이 없는 원본이 {} 로 컴파일되지 않는다: {}".format(
            config["name"], first_error(result.stderr) or result.stderr.strip()[-200:]))


def compile_mutant(worktree, out, config, target, original, result):
    """변이 줄을 넣어 config 로 컴파일한다. 원인 문자열 또는 None(빌드 통과)을 돌려준다."""
    path = worktree / target
    path.write_bytes(replace_line(original, result["line"], result["mutated"]).encode("utf-8"))
    try:
        build_result = build(worktree, out, config)
    finally:
        path.write_bytes(original.encode("utf-8"))
    if build_result.returncode == 0:
        return None
    found = first_error(build_result.stderr)
    return cause_of(found) if found else "오류 줄 없음: " + build_result.stderr.strip()[-200:]


def summarize(rows):
    """rows 는 (id, line, 원인 또는 None). 원인별 개수(재현 못 한 것은 키 None)."""
    return Counter(cause for _, _, cause in rows)


def format_rows(rows, mutated):
    lines = ["M{:03d} 줄 {}: {}  ← {}".format(i, line, mutated[i].strip()[:80], cause or "재현 못 함(빌드 통과)")
             for i, line, cause in rows]
    lines += ["", "원인별:"]
    for cause, count in sorted(summarize(rows).items(), key=lambda pair: (-pair[1], str(pair[0]))):
        lines.append("  {}건  {}".format(count, cause or "재현 못 함(빌드 통과)"))
    return "\n".join(lines)


def run(report_path, revision, limit=None):
    target, invalid, _ = load_invalid(report_path)
    invalid = invalid[:limit] if limit else invalid
    if not invalid:
        return [], {}
    rows, mutated = [], {}
    with em.untouched(), em.current_snapshot(revision, target) as prepared, \
            tempfile.TemporaryDirectory(prefix="plr-invalid-cause-") as out:
        original = (prepared.path / target).read_bytes().decode("utf-8")
        by_name = {c["name"]: c for c in prepared.configs}
        checked = set()
        for result in invalid:
            names = [n for n in result["invalid_in"] if n in by_name]
            if not names:
                raise InputError("{}: invalid_in 의 구성이 현재 선정 구성에 없다".format(result["id"]))
            check_original(original, result)
            if names[0] not in checked:
                check_baseline_builds(prepared.path, out, by_name[names[0]])
                checked.add(names[0])
            number = int(str(result["id"]).lstrip("M") or 0)
            mutated[number] = result["mutated"]
            cause = compile_mutant(prepared.path, out, by_name[names[0]], target, original, result)
            rows.append((number, result["line"], cause))
    return rows, mutated


def main(argv=None):
    parser = argparse.ArgumentParser(description="invalid 변이의 컴파일 오류 원인을 낸다")
    parser.add_argument("report", help="exhaustive_mutation.py 의 --json 보고서")
    parser.add_argument("--revision", default="HEAD", help="사본의 기준 커밋(기본 HEAD, 현재 변경분을 얹는다)")
    parser.add_argument("--limit", type=int, help="앞에서 N건만(점검용)")
    args = parser.parse_args(argv)
    try:
        rows, mutated = run(args.report, args.revision, args.limit)
    except InputError as error:
        print("입력 오류: {}".format(error), file=sys.stderr)
        return 2
    except em.ToolError as error:
        print("도구 오류: {}".format(error), file=sys.stderr)
        return 2
    if not rows:
        print("invalid 변이가 없다")
        return 0
    print(format_rows(rows, mutated))
    return 1 if any(cause is None for _, _, cause in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
