#!/usr/bin/env python3
"""staged 텍스트 파일에서 이 커밋이 들인 인코딩 손상을 찾는다.

손상은 빌드를 깨지 않고 며칠 뒤에 드러난다. Python 문자열의 `\\b`(`tools\\build.cmd`)가 백스페이스가 되고,
BOM 없는 `.ps1` 은 PowerShell 5.1 이 ANSI(cp949)로 읽어 한글 주석 한 줄에 파싱이 깨진다.

    python -B test/tools/check_encoding.py          # staged 변경 (pre-commit)
    python -B test/tools/check_encoding.py --all    # 추적 파일 전체 (점검용)

줄 검사(제어 문자, UTF-8 이 아닌 줄, U+FFFD)는 추가·수정한 줄만 본다. 파일 검사는 `.ps1` BOM, `.cmd`·`.bat` ASCII,
`.md`·`.c`·`.h`·`.py`·`.js` 에 새로 붙은 BOM, 부모와 달라진 줄 끝이다. 옛 손상이 그 파일을 건드린 커밋을 막지 않게
하려는 것이고, 옛 손상은 `--all` 로 점검한다. git 이 바이너리로 보는 파일은 건너뛰되, 텍스트 확장자인데
바이너리가 됐다면(NUL) 그것을 알린다. 본문은 작업 트리가 아니라 index 에서 읽는다.
종료 코드: 0 손상 없음, 1 있음, 2 도구 오류(Git 실행 실패).
"""
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
BOM = b"\xef\xbb\xbf"
CONTROL = re.compile(rb"[\x00\x07\x08\x0b\x0c\x1b]")
CONTROL_NAMES = {0x00: "NUL", 0x07: "BEL", 0x08: "BS", 0x0b: "VT", 0x0c: "FF", 0x1b: "ESC"}
BOM_REQUIRED = (".ps1",)
ASCII_ONLY = (".cmd", ".bat")
NO_NEW_BOM = (".md", ".c", ".h", ".py", ".js")
TEXT_SUFFIXES = BOM_REQUIRED + ASCII_ONLY + NO_NEW_BOM + (".json", ".sh", ".txt", ".yml", ".yaml", ".asm", ".tsv")
HUNK = re.compile(rb"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
CHANGED = "--diff-filter=ACMRT"
REPLACEMENT = chr(0xFFFD)   # 소스에 문자 그대로 두면 이 검사가 자기 파일을 막는다


def line_problems(data, only_lines=None):
    """[(줄 번호, 사유)]. only_lines 가 있으면 그 줄 번호만 본다."""
    problems = []
    for number, line in enumerate(data.split(b"\n"), 1):
        if only_lines is not None and number not in only_lines:
            continue
        for byte in sorted(set(CONTROL.findall(line))):
            problems.append((number, "제어 문자 " + CONTROL_NAMES[byte[0]]))
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            problems.append((number, "UTF-8 이 아니다"))
            continue
        if REPLACEMENT in text:
            problems.append((number, "U+FFFD(대체 문자)"))
    return problems


def eol_style(data):
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return "mixed" if crlf and lf else "crlf" if crlf else "lf" if lf else "none"


def file_problems(path, staged, parent):
    """파일 하나의 사유 목록. parent 는 HEAD 의 본문(새 파일이면 None)."""
    suffix = Path(path).suffix.lower()
    problems = []
    if suffix in BOM_REQUIRED and not staged.startswith(BOM):
        problems.append("BOM 이 없다 — PowerShell 5.1 은 BOM 없는 파일을 ANSI 로 읽는다. UTF-8 BOM 으로 저장한다")
    if suffix in ASCII_ONLY and any(byte > 0x7f for byte in staged):
        problems.append("ASCII 가 아닌 문자 — 콘솔 코드 페이지를 따르므로 ASCII 만 쓴다")
    if suffix in NO_NEW_BOM and staged.startswith(BOM) and not (parent or b"").startswith(BOM):
        problems.append("BOM 을 새로 붙였다 — 이 형식은 BOM 없는 UTF-8 이다")
    old, new = (eol_style(parent) if parent is not None else None), eol_style(staged)
    if old in ("lf", "crlf") and new not in (old, "none"):
        problems.append("줄 끝이 {} 에서 {} 로 바뀌었다 — 파일의 기존 줄 끝을 따른다".format(old, new))
    elif old is None and new == "mixed":
        problems.append("줄 끝이 LF·CRLF 로 섞였다")
    return problems


def git(root, *args, check=True):
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], cwd=root, check=check,
                          capture_output=True)


def staged_files(root):
    """{경로: 바이너리 여부} — staged 추가·수정 파일(삭제 제외)."""
    out = git(root, "diff", "--cached", "--no-renames", CHANGED, "--numstat", "-z").stdout
    files = {}
    for record in out.split(b"\0"):
        if record:
            added, _, path = record.split(b"\t", 2)
            files[path.decode("utf-8")] = added == b"-"
    return files


def staged_added_lines(root):
    """{경로: {추가된 줄 번호}}."""
    out = git(root, "diff", "--cached", "--no-renames", CHANGED, "-U0").stdout
    added, current, header = {}, None, False
    for line in out.split(b"\n"):
        if line.startswith(b"diff --git "):
            current, header = None, True
        elif header and line.startswith(b"+++ "):
            current = line[6:].decode("utf-8") if line.startswith(b"+++ b/") else None
        elif line.startswith(b"@@"):
            header = False
            hunk = HUNK.match(line)
            if hunk and current is not None:
                start, count = int(hunk.group(1)), int(hunk.group(2) or b"1")
                added.setdefault(current, set()).update(range(start, start + count))
    return added


def staged_problems(root):
    """[(경로, 줄 번호 또는 None, 사유)]."""
    lines = staged_added_lines(root)
    problems = []
    for path, binary in sorted(staged_files(root).items()):
        if binary:
            if Path(path).suffix.lower() in TEXT_SUFFIXES:
                problems.append((path, None, "git 이 바이너리로 본다(NUL 이 들어갔다)"))
            continue
        staged = git(root, "show", ":" + path).stdout
        parent = git(root, "show", "HEAD:" + path, check=False)
        parent = parent.stdout if parent.returncode == 0 else None
        problems += [(path, number, reason) for number, reason in line_problems(staged, lines.get(path, set()))]
        problems += [(path, None, reason) for reason in file_problems(path, staged, parent)]
    return problems


def all_problems(root):
    problems = []
    for record in git(root, "ls-files", "--eol", "-z").stdout.decode("utf-8").split("\0"):
        if not record:
            continue
        info, path = record.split("\t", 1)
        data = git(root, "show", ":" + path).stdout
        if info.startswith("i/-text") and Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        problems += [(path, number, reason) for number, reason in line_problems(data)]
        problems += [(path, None, reason) for reason in file_problems(path, data, data)]
    return problems


def describe(path, number, reason):
    return "{}:{}: {}".format(path, number, reason) if number else "{}: {}".format(path, reason)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        problems = all_problems(ROOT) if "--all" in argv else staged_problems(ROOT)
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        print("도구 오류: {}".format(error), file=sys.stderr)
        return 2
    for problem in problems:
        print(describe(*problem))
    if problems:
        print("인코딩 손상 {}개".format(len(problems)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
