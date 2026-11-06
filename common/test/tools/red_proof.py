#!/usr/bin/env python3
"""동작 변경 커밋의 red 증명: 현재 test/ 가 부모의 제품 소스에서 단정으로 실패하는지 잰다.

    python -B test/tools/red_proof.py [--parent HEAD]     # 커밋 전: 부모는 HEAD
    python -B test/tools/red_proof.py --parent HEAD~1     # 커밋 후 검증

부모의 detached worktree 에 현재 작업 트리의 test/ 만 덮어(추적 변경·삭제·untracked) 사본의
test/run_tests.py 를 돌리고, 현재 작업 트리의 test/run_tests.py 도 돌린다. 판정(test/AGENTS.md):
부모에서 단정이 실패하고 빌드 실패·비정상 종료가 없으며 현재에서 전부 통과해야 red 다.
실패한 단정이 이번 test/ 변경 줄 밖이면 "변경 밖" 으로 표시한다 — 무관한 단정인지는 사람이 본다.
원본 작업 트리는 실행 전후 제품 파일 해시와 git status 로 무접촉을 확인한다.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import argparse
import os
import posixpath
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import exhaustive_mutation as em  # noqa: E402
import verification_snapshot as vs  # noqa: E402

# test/run_tests.py 와 test/test_harness.h 의 출력 형식
STATUS = re.compile(r"^(PASS|BUILD FAIL|TEST FAIL): (\S+)")
ASSERTION = re.compile(r"^FAIL (.+?):(\d+)  (.*)$")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
NOT_ASSERTION = ("build_fail", "abnormal")


@dataclass(frozen=True)
class Assertion:
    label: str      # 러너의 실행 이름, 예: test_motor[explr]
    file: str       # 저장소 기준 경로
    line: int
    message: str


@dataclass
class RunResult:
    statuses: dict      # 실행 이름 -> pass | build_fail | assert_fail | abnormal
    assertions: list
    output: str

    @property
    def all_passed(self):
        return all(status == "pass" for status in self.statuses.values())

    def labels(self, *statuses):
        return [label for label, status in self.statuses.items() if status in statuses]


def repo_path(path, tree):
    """__FILE__ 경로를 tree 기준 저장소 경로로. tree 밖이면 그대로 둔다."""
    path = posixpath.normpath(path.replace("\\", "/"))
    base = posixpath.normpath(str(tree).replace("\\", "/")) + "/"
    return path[len(base):] if path.casefold().startswith(base.casefold()) else path


def parse_run(output, code, tree):
    """러너 출력을 실행별 상태와 실패한 단정으로. 단정은 그 뒤의 TEST FAIL 실행에 붙인다.
    TEST FAIL 앞에 하네스의 "FAILED:" 요약이 없으면 충돌·timeout 이므로 비정상 종료다."""
    statuses, assertions, pending, summary = {}, [], [], False
    for line in output.splitlines():
        status = STATUS.match(line)
        if status:
            kind, label = status.groups()
            if kind == "PASS":
                statuses[label] = "pass"
            elif kind == "BUILD FAIL":
                statuses[label] = "build_fail"
            else:
                statuses[label] = "assert_fail" if summary else "abnormal"
                assertions += [Assertion(label, repo_path(f, tree), int(n), m)
                               for f, n, m in pending]
            pending, summary = [], False
            continue
        found = ASSERTION.match(line)
        if found:
            pending.append(found.groups())
        elif line.startswith("FAILED: "):
            summary = True
    if not statuses:
        raise em.ToolError("러너 출력에 실행 결과가 없다:\n" + output[-2000:])
    result = RunResult(statuses, assertions, output)
    if (code == 0) != result.all_passed:
        raise em.ToolError("러너 종료 코드 {} 와 출력의 실행 결과가 맞지 않는다:\n{}".format(
            code, output[-2000:]))
    return result


def changed_lines(diff):
    """`git diff --unified=0` 의 새 쪽 줄 번호. 삭제만 있는 파일·hunk 는 빠진다."""
    lines, current = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            name = line[4:]
            current = name[2:] if name.startswith("b/") else None
            continue
        hunk = HUNK.match(line)
        if hunk and current:
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            if count:
                lines.setdefault(current, set()).update(range(start, start + count))
    return lines


def verdict(parent, current):
    if not current.all_passed:
        return "current_fails"
    if parent.all_passed:
        return "no_red"
    if not parent.assertions:
        return "non_assertion_only"
    return "mixed" if parent.labels(*NOT_ASSERTION) else "red"


@dataclass
class Report:
    command: str
    parent: str
    parent_sha: str
    synced: list        # 부모 사본에 덮거나 지운 test/ 경로
    changed: dict       # 부모 대비 test/ 의 변경 줄
    parent_run: RunResult
    current_run: RunResult

    @property
    def verdict(self):
        return verdict(self.parent_run, self.current_run)

    @property
    def outside(self):
        return [a for a in self.parent_run.assertions
                if a.line not in self.changed.get(a.file, ())]


def locations(assertions):
    """단정 위치를 처음 나온 순서로 중복 없이."""
    return list(dict.fromkeys("{}:{}".format(a.file, a.line) for a in assertions))


def non_assertion_counts(run):
    return "빌드 실패 {}, 비정상 종료 {}".format(len(run.labels("build_fail")),
                                           len(run.labels("abnormal")))


def summary(report):
    """커밋 메시지에 붙일 한 줄."""
    parent, current = report.parent_run, report.current_run
    short = report.parent_sha[:7]
    kind = report.verdict
    if kind == "red":
        places = locations(parent.assertions)
        shown = ", ".join(places[:5]) + (" 외 {}".format(len(places) - 5) if len(places) > 5
                                         else "")
        outside = len(locations(report.outside))
        if outside:
            shown += ", 변경 밖 {}".format(outside)
        return ("red 증명: `{}` — 부모 {} 에서 단정 {}개 실패({}), 현재 제품에서 {}개 실행 "
                "전부 통과".format(report.command, short, len(places), shown,
                                len(current.statuses)))
    reason = {
        "no_red": "부모에서 실패한 것이 없다",
        "non_assertion_only": "부모에서 단정이 아닌 실패만: " + non_assertion_counts(parent),
        "mixed": "부모에서 단정 실패와 함께 단정이 아닌 실패: " + non_assertion_counts(parent),
        "current_fails": "현재 제품에서 실패: " + ", ".join(current.labels(
            "build_fail", "assert_fail", "abnormal")),
    }[kind]
    return "red 증명 아님({}): `{}`, 부모 {}".format(reason, report.command, short)


def format_run(run, changed):
    lines = ["- 실행 {}개: 통과 {}, 단정 실패 {}, 빌드 실패 {}, 비정상 종료 {}".format(
        len(run.statuses), *(len(run.labels(s)) for s in
                             ("pass", "assert_fail", "build_fail", "abnormal")))]
    for status, title in (("build_fail", "빌드 실패"), ("abnormal", "비정상 종료")):
        if run.labels(status):
            lines.append("- {}: {}".format(title, ", ".join(run.labels(status))))
    if run.assertions:
        lines.append("- 실패한 단정:")
        for a in run.assertions:
            where = "변경 줄" if a.line in changed.get(a.file, ()) else "변경 밖"
            lines.append("  - {} {}:{}  {} ({})".format(a.label, a.file, a.line, a.message,
                                                     where))
    return lines


def format_report(report):
    lines = ["# red 증명: 부모 {} ({})".format(report.parent, report.parent_sha[:12]), "",
             "부모 사본에 덮은 test/ 경로 {}개{}".format(
                 len(report.synced), ": " + ", ".join(report.synced) if report.synced else ""),
             "", "## 부모 제품 + 현재 test/"]
    lines += format_run(report.parent_run, report.changed)
    lines += ["", "## 현재 제품 + 현재 test/"]
    lines += format_run(report.current_run, report.changed)
    lines += ["", "요약: " + summary(report), ""]
    return "\n".join(lines)


def changed_test_lines(root, parent, untracked):
    changed = changed_lines(em.git("-c", "core.quotepath=off", "diff", "--unified=0",
                                   "--no-renames", parent, "--", "test", cwd=root))
    for path in untracked:
        count = len((root / path).read_bytes().splitlines())
        changed[path] = set(range(1, count + 1))
    return changed


def run_runner(tree, out):
    env = dict(os.environ, PLR_HOSTTEST_OUT=out, PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-B", str(tree / "test" / "run_tests.py")],
                            cwd=tree, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return parse_run(result.stdout + result.stderr, result.returncode, tree)


def run(parent="HEAD", root=em.ROOT, progress=None):
    """root 의 작업 트리를 현재로, parent 리비전을 부모로 삼아 red 증명을 잰다."""
    root = Path(root)
    progress = progress or (lambda message: None)
    try:
        parent_sha = em.git("rev-parse", "--verify", parent + "^{commit}", cwd=root).strip()
    except subprocess.CalledProcessError as error:
        raise em.ToolError("부모 리비전을 찾지 못했다: {}\n{}".format(parent, error.stderr))
    with em.untouched(root):
        with tempfile.TemporaryDirectory(prefix="plr-red-proof-") as out, \
                vs.prepare(root, parent_sha, vs.PARENT_PRODUCT_CURRENT_TESTS) as snapshot:
            changed = changed_test_lines(root, parent_sha, snapshot.untracked)
            progress("부모 {} 사본에 test/ 경로 {}개를 덮고 두 러너를 돌린다".format(
                parent_sha[:7], len(snapshot.copied)))
            with ThreadPoolExecutor(max_workers=2) as pool:
                parent_run = pool.submit(run_runner, snapshot.path, os.path.join(out, "parent"))
                current_run = pool.submit(run_runner, root, os.path.join(out, "current"))
                parent_run, current_run = parent_run.result(), current_run.result()
    return Report(command="python -B test/tools/red_proof.py --parent {}".format(parent),
                  parent=parent, parent_sha=parent_sha, synced=snapshot.copied, changed=changed,
                  parent_run=parent_run, current_run=current_run)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parent", default="HEAD",
                        help="부모 리비전(기본 HEAD — 커밋 전. 커밋 후 검증이면 HEAD~1)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        report = run(args.parent,
                     progress=lambda message: print(message, file=sys.stderr, flush=True))
    except (em.ToolError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", None) or ""
        print("도구 오류 — 실행 무효: {}\n{}".format(error, detail).rstrip(), file=sys.stderr)
        return 1
    sys.stdout.write(format_report(report))
    return 0 if report.verdict == "red" else 2


if __name__ == "__main__":
    sys.exit(main())
