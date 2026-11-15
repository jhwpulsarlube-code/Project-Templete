#!/usr/bin/env python3
"""동작 변경의 red 증명과 구조 변경의 전처리 동일성을 재는 G16 도구.

    python -B test/tools/red_proof.py                       # 현재 작업 트리 변경(부모 HEAD)
    python -B test/tools/red_proof.py --commit <rev>        # 커밋 <rev>(부모 <rev>^)
    python -B test/tools/red_proof.py --preprocess [--commit <rev>]   # 전처리 동일성

red 증명은 부모의 제품 경로와 대상의 호스트 테스트 경로를 격리 임시 사본에 합쳐 러너의 TU 목록과
빌드 함수로 돌린다. 부모 사본은 git archive로 풀며 작업 트리·stash·worktree를 쓰지 않는다. 판정
집합은 대상 diff에서 새로 생기거나 조건·설명이 바뀐 단정의 (실행 파일, 설명)이며, 부모에서 실패한
`FAIL: <설명>` 집합과 정확히 같고 대상이 전부 통과할 때만 통과한다. 공용 헬퍼 안의 단정은 사람이
판정한다. 전처리 동일성은 러너 TU마다 부모·대상 제품 소스의 전처리 결과를 비교한다.
시제품 스위치는 부모·대상 트리마다 기존 검증기로 판정하고(switch_gate), 기본값이 아니면 판정을 그대로
보이되 요약 줄에 "증명으로 인정 안 됨"을 붙인다.
종료 코드: 0 통과, 1 도구·환경 오류, 2 거부(인자 오류도 2), 3 시제품 스위치 기본값 아님(증명으로 인정 안 됨).
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import argparse
import difflib
import io
import os
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import exhaustive_mutation as em  # noqa: E402
try:
    import switch_gate  # noqa: E402
except ImportError:  # project-specific optional gate
    switch_gate = None
import verification_snapshot as vs  # noqa: E402

TEMP_PREFIX = "plr-red-proof-"
# 호스트 테스트 경로: test/ 에서 도구·기준선·문서를 뺀 실행 코드(test/AGENTS.md)
NON_HOST_TEST_PREFIXES = ("test/tools/", "test/mutation_baselines/")
ARCHIVE_PATHS = [path for path in em.PRODUCT_PATHSPEC if not path.startswith(":")] + ["test"]
FAIL_LINE = re.compile(r"^FAIL: (.*?)\r?$")
SHOWN = 5          # 요약 한 줄에 풀어 쓰는 항목 수
DIFF_LINES = 120   # 거부 시 보여 주는 테스트 diff 줄 수


def is_host_test_path(path):
    return (path.startswith("test/") and not path.startswith(NON_HOST_TEST_PREFIXES)
            and not path.endswith(".md"))


def git_bytes(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


def display(data):
    """사람이 읽을 출력용. 판정 입력에는 쓰지 않는다."""
    return data.decode("utf-8", errors="replace")


def decode(data):
    """판정 입력용 엄격한 해석. 읽지 못하면 서로 다른 입력이 같아질 수 있으므로 도구 오류다."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise em.ToolError("UTF-8 이 아닌 판정 입력: {}".format(error)) from error


class GitTree:
    """커밋 하나의 내용. 읽기만 한다."""

    def __init__(self, root, revision):
        self.root = Path(root)
        try:
            self.sha = em.git("rev-parse", "--verify", revision + "^{commit}", cwd=self.root).strip()
        except subprocess.CalledProcessError as error:
            raise em.ToolError("리비전을 찾지 못했다: {}\n{}".format(revision, error.stderr))
        self.label = self.sha[:7]

    def host_tests(self):
        return sorted(path for path in vs.paths_z(self.root, "ls-tree", "-r", "--name-only",
                                                  self.sha, "--", "test")
                      if is_host_test_path(path))

    def read(self, path):
        return git_bytes("show", "{}:{}".format(self.sha, path), cwd=self.root)

    def materialize(self, destination):
        """제품 경로와 test/ 를 destination 에 푼다. 작업 트리는 건드리지 않는다."""
        names = [name for name in em.git("ls-tree", "-z", "--name-only", self.sha, "--",
                                         *ARCHIVE_PATHS, cwd=self.root).split("\0") if name]
        extract_archive(git_bytes("archive", "--format=tar", self.sha, "--", *names,
                                  cwd=self.root), destination)


class WorkTree:
    """원본 작업 트리. 호스트 테스트는 추적 파일과 무시되지 않은 untracked 파일이다."""
    label = "작업 트리"

    def __init__(self, root):
        self.root = Path(root)

    def host_tests(self):
        paths = vs.paths_z(self.root, "ls-files", "--cached", "--others", "--exclude-standard",
                           "--", "test")
        return sorted(path for path in set(paths)
                      if is_host_test_path(path) and (self.root / path).is_file())

    def read(self, path):
        return (self.root / path).read_bytes()


def extract_archive(data, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            parts = member.name.replace("\\", "/").split("/")
            if (member.name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]
                    or not (member.isfile() or member.isdir())):
                raise em.ToolError("아카이브 항목을 풀 수 없다: {}".format(member.name))
        options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        archive.extractall(destination, members=members, **options)


def install_tests(tree, destination):
    """destination 의 호스트 테스트 경로를 지우고 tree 의 것으로 채운다. 제품 경로는 건드리지 않는다."""
    destination = Path(destination)
    for existing in sorted((destination / "test").rglob("*")):
        relative = existing.relative_to(destination).as_posix()
        if existing.is_file() and is_host_test_path(relative):
            existing.unlink()
    for path in tree.host_tests():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(tree.read(path))


@dataclass
class ExeResult:
    status: str                  # pass | assert_fail | build_fail | abnormal
    failed: list = field(default_factory=list)
    output: str = ""


@contextmanager
def captured_output():
    """fd 1·2 를 임시 파일로 돌려 자식 프로세스의 출력까지 모은다. 블록이 끝나면 box[0] 에 담긴다."""
    box = []
    sys.stdout.flush()
    sys.stderr.flush()
    saved = (os.dup(1), os.dup(2))
    with tempfile.TemporaryFile() as sink:
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            yield box
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            sink.seek(0)
            box.append(display(sink.read()))


def find_compiler(runner):
    try:
        return runner.find_compiler()
    except SystemExit as error:
        raise em.ToolError(str(error)) from error


def run_all(runner, root, progress=None):
    """러너의 TU 목록을 root 에서 빌드·실행해 실행 파일별 결과를 돌려준다."""
    gcc = find_compiler(runner)
    inspection_error = getattr(runner, "AssertionInspectionError", ())
    results = {}
    for executable, sources, *rest in runner.TEST_CASES:
        if progress:
            progress("{} 빌드·실행".format(executable))
        status = None
        code = None
        with captured_output() as box:
            try:
                code = runner.run_test(gcc, executable, sources, rest[0] if rest else None,
                                       root=Path(root))
            except subprocess.CalledProcessError:
                status = "build_fail"
            except inspection_error:
                status = "abnormal"
        output = box[0]
        failed = [found.group(1) for found in map(FAIL_LINE.match, output.splitlines()) if found]
        if status is None:
            if code == 0:
                status = "pass"
            else:
                status = "assert_fail" if code == 1 and failed else "abnormal"
        results[executable] = ExeResult(status, failed if status == "assert_fail" else [], output)
    return results


def labels(results, status):
    return [name for name, result in results.items() if result.status == status]


def failed_set(results):
    return {(name, description) for name, result in results.items() for description in result.failed}


def judged_assertions(runner, parent, target):
    """대상에서 새로 생기거나 조건·설명이 바뀐 단정의 (실행 파일, 설명) 집합과, 실행 파일에 연결되지
    않아 판정할 수 없는 (경로, 설명) 목록을 돌려준다. 설명이 리터럴이 아닌 단정은 러너와 같이 대상 밖이다."""
    if not hasattr(runner, "assertion_calls"):
        raise em.ToolError("러너가 assertion_calls 를 공개하지 않는다")
    executables = {}
    for name, sources, *_ in runner.TEST_CASES:
        for source in sources:
            executables.setdefault(posixpath.normpath(source), []).append(name)
    existing = set(parent.host_tests())
    judged, unmapped = set(), []
    for path in target.host_tests():
        if not path.endswith((".c", ".h")):
            continue
        before = Counter(runner.assertion_calls(decode(parent.read(path))) if path in existing else [])
        for call in runner.assertion_calls(decode(target.read(path))):
            if before[call] > 0:
                before[call] -= 1
            elif path in executables:
                judged.update((name, call[1]) for name in executables[path])
            else:
                unmapped.append((path, call[1]))
    return judged, unmapped


def command_for(commit, preprocess=False):
    return "python -B test/tools/red_proof.py{}{}".format(
        " --preprocess" if preprocess else "", " --commit " + commit if commit else "")


def trees_for(root, commit):
    """(대상, 부모). 커밋 미지정이면 대상은 작업 트리, 부모는 HEAD 다."""
    if commit:
        target = GitTree(root, commit)
        try:
            return target, GitTree(root, target.sha + "^")
        except em.ToolError:
            raise em.ToolError("부모 커밋이 없다: {}".format(commit))
    return WorkTree(root), GitTree(root, "HEAD")


def count_line(results):
    return "실행 {}개 — 통과 {}, 단정 실패 {}, 빌드 실패 {}, 비정상 종료 {}".format(
        len(results), *(len(labels(results, status)) for status in
                        ("pass", "assert_fail", "build_fail", "abnormal")))


def listed(items, shown=SHOWN):
    text = ", ".join(items[:shown])
    return text + (" 외 {}".format(len(items) - shown) if len(items) > shown else "")


def pairs(items):
    return ["{}: {!r}".format(name, description) for name, description in sorted(items)]


def check_switches(trees, progress):
    """(이름, 트리) 마다 시제품 스위치를 판정하고 기본값이 아니면 바로 경고한다."""
    if switch_gate is None:
        return []
    checks = [switch_gate.check(tree, label) for label, tree in trees]
    for check in checks:
        if not check.accepted:
            progress(switch_gate.warning(check))
    return checks


def not_proof(summary, switches):
    """기본값이 아닌 트리가 있으면 요약 줄 머리에 증명 불인정을 붙인다. 원래 판정은 뒤에 남긴다."""
    if switch_gate is None:
        return summary
    names = switch_gate.rejected(switches)
    if not names:
        return summary
    head, _, rest = summary.partition(": ")
    return "{}: {}(시제품 스위치 기본값 아님: {}) — {}".format(
        head, switch_gate.NOT_PROOF, ", ".join(names), rest)


def switch_lines(switches):
    if switch_gate is None:
        return []
    return ["", "## 시제품 스위치"] + ["- {}: 검증기 종료 {} — {}".format(
        c.label, c.code, "기본값" if c.accepted else "기본값 아님, " + switch_gate.NOT_PROOF)
        for c in switches]


@dataclass
class RedReport:
    command: str
    target: str
    parent_sha: str
    judged: set
    unmapped: list
    parent_run: dict
    target_run: dict
    switches: list = field(default_factory=list)

    @property
    def admitted(self):
        return switch_gate is None or not switch_gate.rejected(self.switches)

    @property
    def reasons(self):
        """거부 사유를 (이름, 설명 목록) 로. 비어 있으면 통과다."""
        failed = failed_set(self.parent_run)
        reasons = []
        if labels(self.parent_run, "build_fail"):
            reasons.append(("빌드 실패", labels(self.parent_run, "build_fail")))
        if labels(self.parent_run, "abnormal"):
            reasons.append(("비정상 종료", labels(self.parent_run, "abnormal")))
        if not self.judged and not self.unmapped:
            reasons.append(("판정 집합이 비었다", ["새로 생기거나 바뀐 단정이 없다"]))
        if self.judged - failed:
            reasons.append(("부모에서 통과한 판정 단정", pairs(self.judged - failed)))
        if failed - self.judged:
            reasons.append(("판정 집합 밖에서 실패한 단정", pairs(failed - self.judged)))
        if self.unmapped:
            reasons.append(("실행 파일에 연결되지 않아 판정할 수 없는 단정(공용 헬퍼 등, 사람이 판정)",
                            ["{}: {!r}".format(path, text) for path, text in self.unmapped]))
        if not all(result.status == "pass" for result in self.target_run.values()):
            reasons.append(("대상에서 실패", [
                "{} ({})".format(name, result.status)
                for name, result in self.target_run.items() if result.status != "pass"]))
        return reasons

    @property
    def passed(self):
        return not self.reasons

    def summary(self):
        return not_proof(self.verdict_summary(), self.switches)

    def verdict_summary(self):
        if self.passed:
            return ("Red-proof: 통과 — 부모 {} 제품에서 판정 단정 {}개만 실패({}), 대상 {}개 실행 "
                    "전부 통과 — `{}`".format(
                        self.parent_sha[:7], len(self.judged), listed(pairs(self.judged)),
                        len(self.target_run), self.command))
        return "Red-proof: 거부 — {} — `{}`".format(
            ", ".join("{} {}".format(name, len(items)) for name, items in self.reasons),
            self.command)

    def format(self):
        lines = ["# red 증명: {} / 부모 {}".format(self.target, self.parent_sha[:12]), "",
                 "판정 집합 {}개".format(len(self.judged))]
        lines += ["  - " + item for item in pairs(self.judged)]
        lines += ["", "## 부모 제품 + 대상 테스트", "- " + count_line(self.parent_run)]
        failed = failed_set(self.parent_run)
        lines += ["  - 실패 {}".format(item) for item in pairs(failed)]
        lines += ["", "## 대상 제품 + 대상 테스트", "- " + count_line(self.target_run), ""]
        for name, items in self.reasons:
            lines.append("거부: {}".format(name))
            lines += ["  - " + item for item in items]
        for name, result in self.parent_run.items():
            if result.status == "build_fail":
                lines += ["", "빌드 출력 ({}):".format(name)] + result.output.splitlines()[-15:]
        lines += switch_lines(self.switches)
        lines += ["", self.summary(), ""]
        return "\n".join(lines)


def run_red_proof(root=em.ROOT, commit=None, progress=None):
    root = Path(root)
    progress = progress or (lambda message: None)
    target, parent = trees_for(root, commit)
    with em.untouched(root), tempfile.TemporaryDirectory(
            prefix=TEMP_PREFIX, ignore_cleanup_errors=True) as temp:
        temp = Path(temp)
        progress("부모 {} 의 제품에 대상의 호스트 테스트를 합쳐 격리 사본을 만든다".format(parent.label))
        parent.materialize(temp / "parent")
        install_tests(target, temp / "parent")
        target_root = root
        if commit:
            target.materialize(temp / "target")
            target_root = temp / "target"
        switches = check_switches([("부모", temp / "parent"), ("대상", target_root)], progress)
        runner = vs.load_runner(target_root)
        judged, unmapped = judged_assertions(runner, parent, target)
        progress("부모 제품 + 대상 테스트")
        parent_run = run_all(runner, temp / "parent", progress)
        progress("대상 제품 + 대상 테스트")
        target_run = run_all(runner, target_root, progress)
    return RedReport(command_for(commit), target.label, parent.sha, judged, unmapped,
                     parent_run, target_run, switches)


def normalized_preprocess(data):
    """바이트 그대로 비교한다. 줄 끝 공백과 빈 줄만 정리한다."""
    return [line.rstrip() for line in data.splitlines() if line.strip()]


def preprocess_units(runner, root, flags, gcc):
    """러너 TU 의 제품 소스를 같은 옵션으로 전처리한다. 키는 (소스, 컴파일 정의)다."""
    units = {}
    for _name, sources, *rest in runner.TEST_CASES:
        definitions = tuple(rest[0]) if rest and rest[0] else ()
        for source in sources:
            if source.startswith("test/") or (source, definitions) in units:
                continue
            result = subprocess.run([str(gcc), *flags, *definitions, "-E", "-P", source],
                                    cwd=root, capture_output=True)
            if result.returncode:
                raise em.ToolError("전처리 실패: {}\n{}".format(source, display(result.stderr)[-2000:]))
            units[(source, definitions)] = normalized_preprocess(result.stdout)
    return units


def unit_label(unit):
    return " ".join((unit[0], *unit[1]))


def test_changes(parent, target):
    """부모와 대상의 호스트 테스트 경로 차이를 (경로, 부모 바이트, 대상 바이트) 로. 없는 쪽은 None."""
    old, new = set(parent.host_tests()), set(target.host_tests())
    changes = []
    for path in sorted(old | new):
        before = parent.read(path) if path in old else None
        after = target.read(path) if path in new else None
        if before != after:
            changes.append((path, before, after))
    return changes


def test_diff(changes):
    lines = []
    for path, before, after in changes:
        lines += difflib.unified_diff(
            display(before or b"").splitlines(), display(after or b"").splitlines(),
            "a/" + path, "b/" + path, lineterm="")
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + ["... 외 {}줄".format(len(lines) - DIFF_LINES)]
    return lines


@dataclass
class IdentityReport:
    command: str
    target: str
    parent_sha: str
    units: int
    different: list
    changes: list
    target_run: dict = None
    switches: list = field(default_factory=list)

    @property
    def admitted(self):
        return switch_gate is None or not switch_gate.rejected(self.switches)

    @property
    def outcome(self):
        if not self.different:
            return "identical"
        if self.changes:
            return "reclassify"
        if self.target_run is not None and not all(
                result.status == "pass" for result in self.target_run.values()):
            return "target_fails"
        return "tests_unchanged_pass"

    @property
    def passed(self):
        return self.outcome in ("identical", "tests_unchanged_pass")

    def summary(self):
        return not_proof(self.verdict_summary(), self.switches)

    def verdict_summary(self):
        short = self.parent_sha[:7]
        kind = self.outcome
        if kind == "identical":
            return "Preprocess-identity: 전처리 동일 — 부모 {} 와 TU {}개의 제품 전처리 결과가 같다 — `{}`".format(
                short, self.units, self.command)
        if kind == "tests_unchanged_pass":
            return ("Preprocess-identity: 테스트 불변 통과 — 부모 {} 와 전처리가 다른 TU {}개({}), 호스트 테스트 "
                    "경로 변경 없이 대상 {}개 실행 전부 통과 — `{}`".format(
                        short, len(self.different), listed(self.different), len(self.target_run),
                        self.command))
        if kind == "reclassify":
            return ("Preprocess-identity: 거부 — 전처리가 다르고 호스트 테스트 경로가 {}개 바뀌었다. 동작 변경으로 "
                    "재분류해야 한다 — `{}`".format(len(self.changes), self.command))
        return "Preprocess-identity: 거부 — 전처리가 다르고 대상 테스트가 실패한다 — `{}`".format(
            self.command)

    def format(self):
        lines = ["# 전처리 동일성: {} / 부모 {}".format(self.target, self.parent_sha[:12]), "",
                 "TU {}개 중 전처리 결과가 다른 것 {}개".format(self.units, len(self.different))]
        lines += ["  - " + item for item in self.different]
        if self.changes:
            lines += ["", "호스트 테스트 경로 변경 {}개{}".format(
                len(self.changes), " — 시임의 기계적 조정인지는 사람이 아래 diff 로 판단한다"
                if self.different else " — 구조 변경은 테스트 diff 가 0이어야 한다")]
            lines += ["  - " + path for path, _before, _after in self.changes]
            lines += test_diff(self.changes)
        if self.target_run is not None:
            lines += ["", "## 대상 제품 + 대상 테스트", "- " + count_line(self.target_run)]
        lines += switch_lines(self.switches)
        lines += ["", self.summary(), ""]
        return "\n".join(lines)


def run_identity(root=em.ROOT, commit=None, progress=None):
    root = Path(root)
    progress = progress or (lambda message: None)
    target, parent = trees_for(root, commit)
    with em.untouched(root), tempfile.TemporaryDirectory(
            prefix=TEMP_PREFIX, ignore_cleanup_errors=True) as temp:
        temp = Path(temp)
        progress("부모 {} 을 격리 사본으로 푼다".format(parent.label))
        parent.materialize(temp / "parent")
        target_root = root
        if commit:
            target.materialize(temp / "target")
            target_root = temp / "target"
        switches = check_switches([("부모", temp / "parent"), ("대상", target_root)], progress)
        target_runner = vs.load_runner(target_root)
        if not hasattr(target_runner, "COMPILER_FLAGS"):
            raise em.ToolError("러너가 COMPILER_FLAGS 를 공개하지 않는다")
        flags = list(target_runner.COMPILER_FLAGS)
        gcc = find_compiler(target_runner)
        progress("부모와 대상 제품 소스를 같은 옵션으로 전처리한다")
        before = preprocess_units(vs.load_runner(temp / "parent"), temp / "parent", flags, gcc)
        after = preprocess_units(target_runner, target_root, flags, gcc)
        if not before and not after:
            raise em.ToolError("전처리로 비교할 제품 TU 가 없다")
        different = [unit_label(unit) + (" (부모에 없음)" if unit not in before else
                                         " (대상에 없음)" if unit not in after else "")
                     for unit in sorted(set(before) | set(after))
                     if before.get(unit) != after.get(unit)]
        changes, target_run = test_changes(parent, target), None
        if different and not changes:
            progress("호스트 테스트 경로 변경이 없으므로 대상 테스트를 돌린다")
            target_run = run_all(target_runner, target_root, progress)
    return IdentityReport(command_for(commit, True), target.label, parent.sha,
                          len(set(before) | set(after)), different, changes, target_run, switches)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", metavar="REV",
                        help="대상 커밋(부모는 REV^). 지정하지 않으면 현재 작업 트리 변경(부모 HEAD)")
    parser.add_argument("--preprocess", action="store_true",
                        help="red 증명 대신 러너 TU 단위 전처리 동일성을 잰다(구조 변경)")
    return parser.parse_args(argv)


def main(argv=None, root=em.ROOT):
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    def progress(message):
        print(message, file=sys.stderr, flush=True)

    try:
        runner = run_identity if args.preprocess else run_red_proof
        report = runner(root, args.commit, progress)
    except (em.ToolError, subprocess.CalledProcessError, OSError, tarfile.TarError) as error:
        detail = getattr(error, "stderr", None) or ""
        print("도구 오류 — 실행 무효: {}\n{}".format(error, detail).rstrip(), file=sys.stderr)
        return 1
    sys.stdout.write(report.format())
    if not report.admitted:
        return 3
    return 0 if report.passed else 2


if __name__ == "__main__":
    sys.exit(main())
