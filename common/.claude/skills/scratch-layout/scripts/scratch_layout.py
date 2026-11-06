"""`<root>/<feature>/` 루트와 `issues/` 안의 표 밖 파일을 표준 폴더로 옮기고 참조를 고친다.

  scratch_layout.py measure [--root .scratch] [--keep GLOB ...]
  scratch_layout.py migrate [--root .scratch] [--keep GLOB ...] [--freeze GLOB ...] [--apply]

measure 는 읽기만 한다. migrate 는 --apply 없이는 계획만 출력한다.
분류할 수 없는 파일이 하나라도 남으면(--keep 으로 티켓이라 알려 주지 않은 것) 아무것도 옮기지 않고 exit 2.
--freeze 에 맞는 파일은 이동만 하고 내용은 고치지 않는다 — 다른 파일이 그 바이트의 해시를 들고 있을 때 쓴다.
GLOB 은 저장소 루트 기준 `/` 구분 경로에 맞춘다 (예: '.scratch/*/evidence/*').
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import PurePosixPath as P

STATUS = re.compile(r"^\**Status\**:", re.I)
EVIDENCE_NAME = re.compile(r"observations|evidence|survivor|verification", re.I)
FEATURE_DOCS = ("spec.md", "map.md", "ledger.md")  # 기능 루트에 둘 수 있는 문서
TEXT_EXTS = (".md", ".py", ".json", ".txt", ".yaml", ".yml", ".html", ".toml", ".c", ".h", ".ps1", ".sh")
UNKNOWN = "?"


def classify(rel, head, at_root=False):
    """rel 은 issues/ 아래(또는 기능 루트 기준) 상대 경로. 반환: 목적 폴더 / None(그대로) / UNKNOWN."""
    name = P(rel).name
    if at_root and (name in FEATURE_DOCS or name.startswith(".")):
        return None
    if name.endswith(".json"):
        return "measure" if "baseline" in name else "review"
    if name.endswith(".py"):
        return "scripts"
    if name.endswith(".md"):
        if not at_root and any(STATUS.match(line) for line in head):
            return None  # Status 줄이 있으면 이름과 무관하게 티켓이다 (예: `05-motor-survivors.md` 티켓)
        if EVIDENCE_NAME.search(name):
            return "evidence"
        return UNKNOWN  # 분류할 이름 규칙이 없는 .md: 티켓일 수도, 근거 문서일 수도 있다 — 판단은 사람이
    return UNKNOWN


def tracked(root_dir):
    r = subprocess.run(["git", "ls-files", "-z"], cwd=root_dir, capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0:
        return [f for f in r.stdout.split("\0") if f], True
    out = []
    for d, dirs, files in os.walk(root_dir):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__")]
        out += [os.path.relpath(os.path.join(d, f), root_dir).replace("\\", "/") for f in files]
    return out, False


def matches(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def plan(repo, root, keep):
    files, in_git = tracked(repo)
    moves, unknown = {}, []
    prefix = root.strip("/") + "/"
    for f in files:
        if not f.startswith(prefix) or matches(f, keep):
            continue
        parts = P(f).parts
        at_root = len(parts) == 3
        if not at_root and (len(parts) < 4 or parts[2] != "issues"):
            continue
        rel = "/".join(parts[2:] if at_root else parts[3:])
        try:
            head = open(os.path.join(repo, f), encoding="utf-8").read().splitlines()[:15] if f.endswith(".md") else []
        except (OSError, UnicodeDecodeError):
            head = []
        dest = classify(rel, head, at_root)
        if dest is None:
            continue
        if dest == UNKNOWN:
            unknown.append(f)
            continue
        moves[f] = str(P(*parts[:2]) / dest / P(f).name)
    return moves, unknown, files, in_git


def rewrite(repo, files, moves, freeze):
    LINK = re.compile(r"(\]\()([^)#\s]+)(#[^)]*)?(\))")
    new_of = lambda f: moves.get(f, f)
    edits = {}
    for f in files:
        if not f.endswith(TEXT_EXTS) or matches(f, freeze):
            continue
        try:
            t = open(os.path.join(repo, f), encoding="utf-8", newline="").read()
        except (OSError, UnicodeDecodeError):
            continue
        o = t
        for old, new in moves.items():
            t = t.replace(old, new)
        if f.endswith(".md"):
            def fix(m):
                tgt = m.group(2)
                if re.match(r"[a-z]+:", tgt) or tgt.startswith("/"):
                    return m.group(0)
                resolved = os.path.normpath(str(P(f).parent / tgt)).replace("\\", "/")
                if resolved not in moves and f not in moves:
                    return m.group(0)
                rel = os.path.relpath(new_of(resolved), str(P(new_of(f)).parent)).replace("\\", "/")
                return m.group(1) + rel + (m.group(3) or "") + m.group(4)
            t = LINK.sub(fix, t)
        if t != o:
            edits[f] = t
    return edits


def cmd_measure(a):
    moves, unknown, _, _ = plan(a.repo, a.root, a.keep)
    for old, new in sorted(moves.items()):
        print(f"MOVE    {old} -> {new}")
    for f in sorted(unknown):
        hint = "기능 루트에는 spec·map·ledger 만 둔다 — 목적 폴더를 정하거나 --keep" if len(P(f).parts) == 3 \
            else "티켓이면 --keep, 아니면 분류 규칙을 정해야 한다"
        print(f"UNKNOWN {f}  ({hint})")
    print(f"이동 대상 {len(moves)}개, 분류 불가 {len(unknown)}개")
    return 0


def cmd_migrate(a):
    moves, unknown, files, in_git = plan(a.repo, a.root, a.keep)
    if unknown:
        for f in sorted(unknown):
            print(f"UNKNOWN {f}", file=sys.stderr)
        print("분류 불가 파일이 남았다. --keep 으로 티켓을 지정하거나 먼저 분류를 정하라.", file=sys.stderr)
        return 2
    dests = list(moves.values())
    clash = [d for d in dests if dests.count(d) > 1 or os.path.exists(os.path.join(a.repo, d))]
    if clash:
        print("목적지 충돌: " + ", ".join(sorted(set(clash))), file=sys.stderr)
        return 2
    edits = rewrite(a.repo, files, moves, a.freeze)
    print(f"이동 {len(moves)}개, 내용 수정 {len(edits)}개" + ("" if a.apply else " (계획만 — --apply 로 실행)"))
    if not a.apply:
        return 0
    for old, new in moves.items():
        os.makedirs(os.path.join(a.repo, os.path.dirname(new)), exist_ok=True)
        if in_git:
            subprocess.run(["git", "mv", old, new], cwd=a.repo, check=True, capture_output=True)
        else:
            os.replace(os.path.join(a.repo, old), os.path.join(a.repo, new))
    for f, t in edits.items():
        with open(os.path.join(a.repo, moves.get(f, f)), "w", encoding="utf-8", newline="") as fh:
            fh.write(t)
    print("완료")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("measure", cmd_measure), ("migrate", cmd_migrate)):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
        p.add_argument("--root", default=".scratch")
        p.add_argument("--keep", action="append", default=[])
        if name == "migrate":
            p.add_argument("--freeze", action="append", default=[])
            p.add_argument("--apply", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
