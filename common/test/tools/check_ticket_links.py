#!/usr/bin/env python3
"""`.scratch/` 마크다운의 깨진 상대 링크를 찾는다.

티켓은 `.scratch/<feature>/issues/` 에 있어 `docs/agents/` 문서에서 복사한 `[워크플로우](host-test-workflow.md)`
같은 링크가 열리지 않는다. 링크의 기준은 링크를 담은 파일의 디렉터리다.

    python -B test/tools/check_ticket_links.py              # staged 변경의 추가된 줄만 (pre-commit)
    python -B test/tools/check_ticket_links.py --all        # `.scratch/**/*.md` 전체 (점검용)

staged 모드는 추가·수정한 줄의 링크만 본다. 이미 깨져 있던 옛 문서가 그 문서를 건드린 커밋을 막지 않게 하려는 것이다.
외부 링크(`://`·`mailto:`)와 `#앵커`만 있는 링크, 코드 블록·인라인 코드 안의 링크는 보지 않는다.
staged 모드는 작업 트리가 아니라 index 의 본문을 읽는다. stage 하지 않은 수정이 깨진 staged 링크를 가리지 못한다.
종료 코드: 0 깨진 링크 없음, 1 있음, 2 도구 오류(파일 읽기·UTF-8 디코딩·Git 실행 실패).

같은 추가된 줄에서 커밋을 붙이지 않은 제품 줄 번호(`system.c:472`)도 찾는다(`staged_citations`). pre-commit 은 이것을
막지 않고 알리기만 한다 — 규칙은 CODING_STANDARDS.md "코드 인용".
"""
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ".scratch"
LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
CODE_SPAN = re.compile(r"`[^`\n]*`")
FENCE = re.compile(r"^\s*(```|~~~)")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
CITATION = re.compile(r"(?<![\w./-])(?:[\w-]+/)*[\w-]+\.[ch]:\d+")


def links_in(text, only_lines=None):
    """[(줄 번호, 대상)]. only_lines 가 있으면 그 줄 번호만 본다."""
    found = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or (only_lines is not None and number not in only_lines):
            continue
        for match in LINK.finditer(CODE_SPAN.sub("", line)):
            found.append((number, match.group(1)))
    return found


def citations_in(text, only_lines=None):
    """[(줄 번호, `파일.c:줄`)] — 커밋을 붙이지 않은 제품 줄 번호 인용(CODING_STANDARDS.md "코드 인용").
    코드 블록 안(컴파일러 출력 인용)은 보지 않는다. 인라인 코드 안은 본다."""
    found = []
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or (only_lines is not None and number not in only_lines):
            continue
        found += [(number, match.group(0)) for match in CITATION.finditer(line)]
    return found


def resolves(root, path, target):
    """링크 대상이 열리는가. 외부 링크와 앵커만 있는 링크는 열린다고 본다."""
    if "://" in target or target.startswith(("mailto:", "#")):
        return True
    local = unquote(target.split("#", 1)[0])
    return (Path(root) / path).parent.joinpath(local).exists()


def broken_links(root, path, text, only_lines=None):
    return [(number, target) for number, target in links_in(text, only_lines)
            if not resolves(root, path, target)]


def staged_added_lines(root):
    """{경로: {추가된 줄 번호}} — `.scratch/` 아래 staged 마크다운(삭제 제외)."""
    out = subprocess.run(["git", "diff", "--cached", "--no-renames", "--diff-filter=ACMRT", "-U0", "--",
                          SCOPE + "/*.md"], cwd=root, check=True, capture_output=True).stdout
    added, current = {}, None
    for line in out.decode("utf-8").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current is not None:
                added.setdefault(current, set())
            continue
        hunk = HUNK.match(line)
        if hunk and current is not None:
            start, count = int(hunk.group(1)), int(hunk.group(2) or "1")
            added[current].update(range(start, start + count))
    return added


def staged_problems(root):
    """staged 모드의 깨진 링크 [(경로, 줄, 대상)]."""
    problems = []
    for path, lines in sorted(staged_added_lines(root).items()):
        text = subprocess.run(["git", "show", ":" + path], cwd=root, check=True,
                              capture_output=True).stdout.decode("utf-8-sig")
        problems += [(path, number, target) for number, target in broken_links(root, path, text, lines)]
    return problems


def staged_citations(root):
    """staged 모드의 커밋 없는 줄 번호 인용 [(경로, 줄, 인용)]. pre-commit 은 막지 않고 알리기만 한다."""
    found = []
    for path, lines in sorted(staged_added_lines(root).items()):
        text = subprocess.run(["git", "show", ":" + path], cwd=root, check=True,
                              capture_output=True).stdout.decode("utf-8-sig")
        found += [(path, number, cited) for number, cited in citations_in(text, lines)]
    return found


def all_problems(root):
    problems = []
    for file in sorted((Path(root) / SCOPE).rglob("*.md")):
        path = file.relative_to(root).as_posix()
        text = file.read_text(encoding="utf-8-sig")
        problems += [(path, number, target) for number, target in broken_links(root, path, text)]
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        problems = all_problems(ROOT) if "--all" in argv else staged_problems(ROOT)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print("도구 오류: {}".format(error), file=sys.stderr)
        return 2
    for path, number, target in problems:
        print("{}:{}: 열리지 않는 링크 ({}) — 링크는 이 파일의 디렉터리 기준이다".format(path, number, target))
    if problems:
        print("깨진 링크 {}개".format(len(problems)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
