"""`<root>/<feature>/` 루트와 `issues/` 안의 표 밖 파일을 표준 폴더로 옮기고 참조를 고친다.

  scratch_layout.py measure [--root .scratch] [--keep GLOB ...]
  scratch_layout.py migrate [--root .scratch] [--keep GLOB ...] [--freeze GLOB ...] [--apply]
                            [--no-freeze-hashed] [--allow-frozen-links]

measure 는 읽기만 한다. migrate 는 --apply 없이는 계획만 출력한다.
분류할 수 없는 파일이 하나라도 남으면(--keep 으로 티켓이라 알려 주지 않은 것) 아무것도 옮기지 않고 exit 2.
동결한 파일은 이동만 하고 내용은 고치지 않는다 — 다른 파일이 그 바이트의 해시를 들고 있을 때 쓴다.
--freeze GLOB 으로 직접 지정하고, 저장소 안 다른 파일에 SHA-256 이 적힌 파일은 자동으로 동결한다(--no-freeze-hashed 로 끈다).
동결 파일이 이동 대상을 링크하거나 경로로 적고 있으면(고칠 수 없어 옮기면 깨진다) exit 2 —
그 대상을 --keep 으로 빼거나, 깨져도 좋으면 --allow-frozen-links.
이동 전후로 해시 소실 수와 깨진 링크 수를 재서 늘면 exit 3 (이미 옮긴 뒤이므로 git 으로 되돌린다).
동결 파일 자신의 링크가 끊어진 것은 알리기만 한다(고치지 않기로 한 파일이다).
이동하는 .py 가 __file__·parents[] 를 쓰면 WARN 을 낸다 — 폴더가 한 단계 깊어져 기준 경로가 어긋난다.
GLOB 은 저장소 루트 기준 `/` 구분 경로에 맞춘다 (예: '.scratch/*/evidence/*').
"""
import argparse
import fnmatch
import hashlib
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
HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")
MD_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
DEPTH_USE = re.compile(r"__file__|parents\[|os\.getcwd")


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


def read_text(repo, f):
    try:
        with open(os.path.join(repo, f), encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def read_bytes(repo, f):
    try:
        with open(os.path.join(repo, f), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def hash_bound(repo, files, root):
    """다른 파일에 SHA-256 이 적힌 파일 집합. 줄 끝을 LF 로 바꾼 해시도 같은 파일로 본다(autocrlf)."""
    prefix = root.strip("/") + "/"
    scoped = [f for f in files if f.startswith(prefix)]
    digests = {}
    for f in scoped:
        data = read_bytes(repo, f)
        if data is None:
            continue
        for variant in {data, data.replace(b"\r\n", b"\n")}:
            digests.setdefault(hashlib.sha256(variant).hexdigest(), set()).add(f)
    bound = set()
    for f in scoped:
        text = read_text(repo, f) if f.endswith(TEXT_EXTS) else None
        for h in set(x.lower() for x in HEX64.findall(text or "")):
            bound |= digests.get(h, set()) - {f}
    return bound


def verify_state(repo, files, root):
    """(문서가 적은 해시 중 실제 파일과 맞는 것의 집합, 깨진 상대 링크 집합). 이동 전후 비교용."""
    prefix = root.strip("/") + "/"
    digests = set()
    for f in files:
        if f.startswith(prefix):
            data = read_bytes(repo, f)
            if data is None:
                continue
            digests |={hashlib.sha256(data).hexdigest(), hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()}
    mentioned, broken = set(), set()
    for f in files:
        text = read_text(repo, f) if f.endswith(TEXT_EXTS) else None
        if text is None:
            continue
        if f.startswith(prefix):
            mentioned |= {x.lower() for x in HEX64.findall(text)} & digests
        if f.endswith(".md"):
            for target in MD_LINK.findall(text):
                if re.match(r"[a-zA-Z]+:", target) or target.startswith(("/", "#")):
                    continue
                resolved = os.path.normpath(os.path.join(repo, str(P(f).parent), target.replace("%20", " ")))
                if not os.path.exists(resolved):
                    broken.add(f + " -> " + target)
    return mentioned, broken


def frozen_link_breaks(repo, files, moves, frozen):
    """[(동결 파일, 사유)] — 동결이라 고치지 못하는데 이동 때문에 끊어질 링크."""
    out = []
    for f in sorted(frozen):
        text = read_text(repo, f) if f.endswith(TEXT_EXTS) else None
        if text is None:
            continue
        for old in moves:
            if old in text:
                out.append((f, "이동 대상 경로 `%s` 가 적혀 있다" % old))
        if f.endswith(".md"):
            for target in MD_LINK.findall(text):
                if re.match(r"[a-zA-Z]+:", target) or target.startswith(("/", "#")):
                    continue
                resolved = os.path.normpath(str(P(f).parent / target.replace("%20", " "))).replace("\\", "/")
                if resolved in moves:
                    out.append((f, "이동 대상 `%s` 를 링크한다" % resolved))
    return out


def depth_warnings(repo, moves):
    """이동하는 .py 중 __file__ 등으로 기준 경로를 잡는 파일 — 폴더가 한 단계 깊어지면 어긋난다."""
    out = []
    for old in sorted(moves):
        if old.endswith(".py"):
            text = read_text(repo, old)
            if text and DEPTH_USE.search(text):
                out.append(old)
    return out


def rewrite(repo, files, moves, frozen):
    LINK = re.compile(r"(\]\()([^)#\s]+)(#[^)]*)?(\))")
    new_of = lambda f: moves.get(f, f)
    edits = {}
    for f in files:
        if not f.endswith(TEXT_EXTS) or f in frozen:
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
    frozen = {f for f in files if matches(f, a.freeze)}
    if not a.no_freeze_hashed:
        frozen |= hash_bound(a.repo, files, a.root)
    breaks = frozen_link_breaks(a.repo, files, moves, frozen)
    if breaks and not a.allow_frozen_links:
        for f, reason in breaks:
            print(f"FROZEN  {f}: {reason}", file=sys.stderr)
        print("동결 파일은 고치지 못해 이동하면 링크가 깨진다. 대상을 --keep 으로 빼거나 --allow-frozen-links 를 준다.", file=sys.stderr)
        return 2
    edits = rewrite(a.repo, files, moves, frozen)
    for old in depth_warnings(a.repo, moves):
        print(f"WARN    {old}: __file__·parents[] 기준 경로를 쓴다 — 폴더가 한 단계 깊어지면 어긋난다. 이동 뒤 확인한다")
    print(f"이동 {len(moves)}개(동결 {len(frozen)}개), 내용 수정 {len(edits)}개"
          + ("" if a.apply else " (계획만 — --apply 로 실행)"))
    if not a.apply:
        return 0
    before_hashes, before_broken = verify_state(a.repo, files, a.root)
    for old, new in moves.items():
        os.makedirs(os.path.join(a.repo, os.path.dirname(new)), exist_ok=True)
        if in_git:
            subprocess.run(["git", "mv", old, new], cwd=a.repo, check=True, capture_output=True)
        else:
            os.replace(os.path.join(a.repo, old), os.path.join(a.repo, new))
    for f, t in edits.items():
        with open(os.path.join(a.repo, moves.get(f, f)), "w", encoding="utf-8", newline="") as fh:
            fh.write(t)
    after_files, _ = tracked(a.repo)
    after_hashes, after_broken = verify_state(a.repo, after_files, a.root)
    lost, new_broken = before_hashes - after_hashes, after_broken - before_broken
    frozen_after = tuple(moves.get(f, f) + " -> " for f in frozen)
    in_frozen = {item for item in new_broken if item.startswith(frozen_after)}   # 동결한 파일의 링크는 고치지 않기로 했다
    new_broken -= in_frozen
    print(f"검증: 해시 소실 {len(lost)}개, 깨진 링크 {len(before_broken)} -> {len(after_broken)}개"
          f"(신규 {len(new_broken) + len(in_frozen)}, 그중 동결 파일 {len(in_frozen)})")
    for item in sorted(in_frozen):
        print(f"FROZEN-BROKEN {item}")
    if lost or new_broken:
        for item in sorted(new_broken):
            print(f"BROKEN  {item}", file=sys.stderr)
        print("이동 전보다 나빠졌다. git 으로 되돌리고 원인 파일을 --keep 으로 빼라.", file=sys.stderr)
        return 3
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
            p.add_argument("--no-freeze-hashed", action="store_true")
            p.add_argument("--allow-frozen-links", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")   # cp949 콘솔에서 `—` 출력이 도구를 죽인다
    sys.exit(main())
