#!/usr/bin/env python3
"""두 CS+ 빌드의 생성 어셈블리(DefaultBuild/*.asm·*.prn)를 함수 단위로 맞대 제품 변경의 오버헤드를 잰다.

    python -B test/tools/asm_compare.py --parent-build <부모 빌드 디렉터리> --build <빌드 디렉터리> --module <이름> [--module …]
    python -B test/tools/asm_compare.py --parent-build <…> --build <…> --all
    python -B test/tools/asm_compare.py --parent-build <…> --build <…> --all --moved _옛이름=_새이름

빌드는 하지 않는다. 부모 빌드는 사람이(또는 PowerShell 에서) 만든다
(.scratch/asm-overhead-compare/issues/01-parent-asm-compare-tool.md "지금 하는 일" 1·2):
부모 커밋을 저장소 밖에 꺼내고(`git worktree add --detach <경로> <부모>`, 또는 `git archive <부모>` 를 풀기),
그 사본의 Pulsarlube_R.mtpj 를 루트 AGENTS.md 의 `cubesuite /bb` 로 빌드한 뒤 출력의
`Build ended(Error:0, …)` 줄로 성공을 판정한다(CS+ 는 오류에도 종료 코드 0). 그 사본의 DefaultBuild 가 --parent-build 다.
꺼낸 사본은 지우지 않고 사용자에게 맡긴다.

함수별로 명령 수(`.bc $!…` 같은 의사 분기 포함, `.LINE`·`.STACK` 등 대문자 지시어 제외), `call` 수,
꼬리 호출(함수 심볼로 가는 `br`) 수, 크기(`.prn` 의 함수 라벨과 끝 라벨 `.LFEn` 의 주소 차), `.STACK` 값을 비교해
바뀐 함수만 한 줄씩 내고 끝에 `요약:` 한 줄을 낸다. 정적 심볼의 `@N` 번호와 지역 라벨 `.BB@LABEL@N_M` 번호만 다른
것은 바뀜으로 세지 않는다(지역 라벨은 함수 안에서 처음 나온 순서로 다시 번호를 매겨, 분기 대상이 바뀐 것은 잡는다).

`--moved 옛=새` 는 인라인 분할로 본문이 새 이름 함수로 옮겨 간 경우다. 부모의 옛 함수와 현재의 새 함수를 같은 함수로 맞대
(그 쌍은 `옛 → 새` 로 출력되고, 원래 이름은 사라졌어야 한다) 옮긴 본문이 새 함수의 `call` 증가로 잡히지 않게 한다.

종료 코드: 0 통과, 1 어느 함수든 `call` + 꼬리 호출 수가 늘었다(`call _f; ret` → `br _f` 는 늘지 않음),
2 입력 오류(.asm·.prn 없음, 비교할 함수 0 개, 두 빌드의 CC-RL 버전 머리말 불일치, 없는 모듈 이름, `--all` 에서
한쪽 빌드에만 있는 모듈). 명령 수·바이트 증가는 보고만 한다 — 사유는 사람이 티켓에 적는다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stack_depth  # noqa: E402

VERSION = re.compile(r"^#@\s+CC-RL Version\s*:\s*(\S+)")
# 소문자 니모닉이 명령이다. CC-RL 은 먼 분기를 `.bnz $!…` 처럼 점으로 시작하는 의사 분기로 쓰므로 점도 받는다.
INSTRUCTION = re.compile(r"^\s+(\.?[a-z][a-z0-9]*)\b([^;]*)")
# 지역 라벨(`$.BB@…`)이 아니라 함수 심볼로 가는 br 은 꼬리 호출이다.
TAIL_CALL = re.compile(r"^\s+br\s+\$?!{0,2}[A-Za-z_]")
LOCAL_LABEL = re.compile(r"\.BB@LABEL@\d+_\d+")
LOCAL_LABEL_LINE = re.compile(r"^(\.BB@LABEL@\d+_\d+):")
FUNCTION_END = re.compile(r"^(\.LFE\d+):")
SECTION = re.compile(r"^\s+\.SECTION\b")
PRN_LABEL = re.compile(r"^([0-9A-Fa-f]{8})\s+(?:[0-9A-Fa-f]+\s+)?\d+\s+([A-Za-z_.][\w@.]*):")
CALLS = ("call", "callt")
# 이 필드가 모두 같으면 "같은 함수"다.
FIELDS = ("instructions", "calls", "tail_calls", "stack", "size", "text")


class CompareError(Exception):
    """비교할 수 없는 입력 — 통과로 읽히지 않게 main 이 종료 코드 2 로 끝낸다."""


def strip_static_number(text):
    """`_v_count@13` → `_v_count@`: 다른 함수가 늘거나 줄면 바뀌는 정적 심볼 번호를 지운다."""
    return re.sub(r"@\d+", "@", text)


def renumber_labels(lines):
    """함수 안 지역 라벨을 처음 나온 순서의 번호(L0, L1, …)로 바꾼다. 번호만 다르면 같고, 분기 대상이 바뀌면 다르다."""
    order = {}

    def label(m):
        return "L{}".format(order.setdefault(m.group(0), len(order)))

    return tuple(LOCAL_LABEL.sub(label, line) for line in lines)


def read_module(build, module):
    """(CC-RL 버전, {정규화 이름: 함수}). 함수: name·instructions·calls·tail_calls·stack·size·text."""
    asm = Path(build, module + ".asm")
    prn = Path(build, module + ".prn")
    for path in (asm, prn):
        if not path.is_file():
            raise CompareError("{} 가 없다".format(path))
    version = None
    found = {}
    current = None
    for line in stack_depth.read_lines(asm):
        m = VERSION.match(line)
        if m and version is None:
            version = m.group(1)
            continue
        m = stack_depth.STACK.match(line)
        if m:
            current = {"name": m.group(1), "stack": int(m.group(2)), "instructions": 0, "calls": 0,
                       "tail_calls": 0, "text": [], "end": None}
            key = strip_static_number(m.group(1))
            if key in found:
                raise CompareError("{} 에 같은 함수 {} 가 두 번 있다".format(asm, m.group(1)))
            found[key] = current
            continue
        if current is None:
            continue
        m = FUNCTION_END.match(line)
        if m:
            current["end"] = m.group(1)
            current = None
            continue
        if SECTION.match(line):
            current = None
            continue
        m = LOCAL_LABEL_LINE.match(line)
        if m:
            current["text"].append(m.group(1) + ":")
            continue
        m = INSTRUCTION.match(line)
        if m:
            current["instructions"] += 1
            current["calls"] += m.group(1) in CALLS
            current["tail_calls"] += bool(TAIL_CALL.match(line))
            current["text"].append(" ".join((m.group(1) + " " + m.group(2)).split()))
    if version is None:
        raise CompareError("{} 에 CC-RL 버전 머리말이 없다".format(asm))
    addresses = {}
    for line in stack_depth.read_lines(prn):
        m = PRN_LABEL.match(line)
        if m:
            addresses[m.group(2)] = int(m.group(1), 16)
    for func in found.values():
        # 라벨 재번호가 먼저다 — 정적 번호를 먼저 지우면 `.BB@LABEL@7_36` 이 `.BB@LABEL@_` 로 뭉개진다
        func["text"] = tuple(strip_static_number(line) for line in renumber_labels(func["text"]))
        if func["end"] is None:
            raise CompareError("{} 의 {} 에 끝 라벨(.LFEn)이 없다".format(asm, func["name"]))
        if func["name"] not in addresses or func["end"] not in addresses:
            raise CompareError("{} 에 {} 또는 {} 의 주소가 없다 (.asm 과 다른 빌드인가)".format(
                prn, func["name"], func["end"]))
        func["size"] = addresses[func["end"]] - addresses[func["name"]]
    return version, found


def all_modules(parent_build, build):
    """두 빌드의 *.asm 이름. 한쪽에만 있는 모듈은 조용히 빠지지 않게 오류다."""
    names = []
    for path in (parent_build, build):
        if not Path(path).is_dir():
            raise CompareError("빌드 디렉터리가 없다: {}".format(path))
        names.append({asm.stem for asm in Path(path).glob("*.asm")})
    if names[0] != names[1]:
        raise CompareError("한쪽 빌드에만 있는 모듈: {} (--module 로 고른다)".format(
            ", ".join(sorted(names[0] ^ names[1]))))
    if not names[0]:
        raise CompareError("{} 와 {} 에 *.asm 이 없다".format(parent_build, build))
    return sorted(names[0])


def apply_moves(module, parent, current, moves, applied):
    """옮긴 본문(인라인 분할로 이름이 바뀐 함수)을 새 이름으로 맞대도록 부모 함수의 키를 바꾼다.

    부모에는 옛 이름만, 현재에는 새 이름만 있어야 한다 — 옛 이름이 남았다면 옮긴 것이 아니라 복제다.
    새 이름 쪽 이름표를 `옛 → 새` 로 바꿔 출력에 이동이 보이게 한다.
    """
    for old, new in moves.items():
        if old not in parent:
            continue
        if new not in current or old in current or new in parent:
            raise CompareError("{} 의 --moved {}={} 가 성립하지 않는다 (부모에 옛 이름만, 현재에 새 이름만 있어야 한다)".format(
                module, old, new))
        parent[new] = dict(parent.pop(old), name=new)
        current[new] = dict(current[new], name="{} → {}".format(old, new))
        applied.add(old)


def compare(parent_build, build, modules, moves=None):
    """([(모듈, 부모 함수 또는 None, 현재 함수 또는 None)] 바뀐 함수만, 같은 함수 수)."""
    changed = []
    same = 0
    moves = moves or {}
    applied = set()
    for module in modules:
        parent_version, parent = read_module(parent_build, module)
        version, current = read_module(build, module)
        if parent_version != version:
            raise CompareError("{} 의 CC-RL 버전이 다르다: 부모 {}, 현재 {}".format(module, parent_version, version))
        apply_moves(module, parent, current, moves, applied)
        for key in sorted(set(parent) | set(current)):
            before, after = parent.get(key), current.get(key)
            if before and after and all(before[f] == after[f] for f in FIELDS):
                same += 1
            else:
                changed.append((module, before, after))
    if not changed and not same:
        raise CompareError("비교할 함수가 없다 (모듈 {})".format(", ".join(modules)))
    missing = sorted(set(moves) - applied)
    if missing:
        raise CompareError("--moved 의 옛 이름이 비교한 모듈의 부모 빌드에 없다: {}".format(", ".join(missing)))
    return changed, same


def parse_moves(items):
    """`옛이름=새이름` 목록 → {옛: 새}. 형식이 틀리거나 같은 옛 이름을 두 번 주면 오류다."""
    moves = {}
    for item in items or []:
        old, sep, new = item.partition("=")
        if not sep or not old or not new or old == new:
            raise CompareError("--moved 는 옛이름=새이름 이다: {}".format(item))
        if old in moves:
            raise CompareError("--moved 옛 이름을 두 번 줬다: {}".format(old))
        moves[old] = new
    return moves


def field(func, name):
    """없는 쪽(추가·삭제된 함수)은 None."""
    return None if func is None else func[name]


def delta(before, after, name, form="{}"):
    """'4 → 5 (+1)'. 없는 쪽은 '-'."""
    a, b = field(before, name), field(after, name)
    text = "{} → {}".format("-" if a is None else form.format(a), "-" if b is None else form.format(b))
    if a is not None and b is not None and b != a:
        text += " ({:+d})".format(b - a)
    return text


def growth(before, after, *names):
    return sum(field(after, n) or 0 for n in names) - sum(field(before, n) or 0 for n in names)


def main(argv=None):
    parser = argparse.ArgumentParser(description="부모 대비 생성 어셈블리 오버헤드 비교")
    parser.add_argument("--parent-build", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--module", action="append", help="비교할 모듈(.asm 파일 이름에서 확장자를 뺀 것). 반복 가능")
    which.add_argument("--all", action="store_true", help="두 빌드의 *.asm 전부")
    parser.add_argument("--moved", action="append", metavar="옛=새",
                        help="인라인 분할로 이름이 바뀐 본문 함수(asm 심볼, 예: _cli__process=_cli__process_main). 반복 가능")
    args = parser.parse_args(argv)
    try:
        # 같은 이름을 두 번 주어도 한 번만 센다
        modules = all_modules(args.parent_build, args.build) if args.all else list(dict.fromkeys(args.module))
        changed, same = compare(args.parent_build, args.build, modules, parse_moves(args.moved))
    except CompareError as error:
        print("asm_compare: {}".format(error), file=sys.stderr)
        return 2

    grown = []
    print("parent", args.parent_build)
    print("build", args.build)
    for module, before, after in changed:
        name = (after or before)["name"]
        print("{} {}: 명령 {}, call {}, 꼬리 br {}, 크기 {}, .STACK {}".format(
            module, name, delta(before, after, "instructions"), delta(before, after, "calls"),
            delta(before, after, "tail_calls"), delta(before, after, "size", "0x{:X}"),
            delta(before, after, "stack")))
        if growth(before, after, "calls", "tail_calls") > 0:
            grown.append("{}:{} call {}, 꼬리 br {}".format(
                module, name, delta(before, after, "calls"), delta(before, after, "tail_calls")))
    totals = "바뀐 함수 {}, 같은 함수 {}, 명령 {:+d}, 바이트 {:+d}".format(
        len(changed), same,
        sum(growth(b, a, "instructions") for _, b, a in changed),
        sum(growth(b, a, "size") for _, b, a in changed))
    if grown:
        print("요약: 실패 — call 증가 {} 함수({}), {}".format(len(grown), "; ".join(grown), totals))
        return 1
    print("요약: 통과 — call 증가 0, {}".format(totals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
