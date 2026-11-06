#!/usr/bin/env python3
"""CS+ 빌드의 생성 어셈블리(DefaultBuild/*.asm)에서 최대 스택 깊이를 정적으로 잰다.

이 머신의 CS+ 설치본에는 Call Walker 가 없어(이슈 pump-current-pattern-detection/37 기록) 총 스택을 도구로
읽지 못한다. 대신 CC-RL 이 함수마다 내는 `.STACK <이름> = <바이트>` 지시어(함수 자체의 프레임)와 호출 간선
(`call`·`br`)으로 호출 폐포를 걸어 경로의 합을 구한다.

    python -B test/tools/stack_depth.py [DefaultBuild 경로]

빌드 디렉터리가 없거나 `*.asm` 이 없거나 `.STACK` 지시어가 하나도 없으면 "0 B" 를 내지 않고 stderr 에 사유를
적고 종료 코드 1 로 끝난다(측정할 것이 없음).

뿌리: `main` 과 `.vector` 로 등록된 ISR. 간접 호출(`call hl` 등)이 있는 함수는 주소를 취한(`LOWW(이름)`·`#이름`)
함수 전부를 호출한다고 보아 상한을 잡는다. `.STACK` 이 없는 피호출자(`__COM_uidiv` 등 런타임 라이브러리)의
스택은 합산에서 빠지므로 **결과는 하한**이다(빠진 목록을 함께 출력한다). main + 최대 ISR 합은 ISR 중첩이 없다는
가정이다(확인 필요). 정적 추정이며 실기 측정이 아니다.
"""
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]

STACK = re.compile(r"^\s*\.STACK\s+(\S+)\s*=\s*(\d+)")
VECTOR = re.compile(r"^(\S+)\s+\.vector\s+(0x[0-9a-fA-F]+)")
CALL = re.compile(r"^\s*(?:call|br)\s+\$?!{0,2}\.?([A-Za-z_][\w@]*)\s*$")
ADDR = re.compile(r"(?:LOWW\(|HIGHW\(|#!?!?(?!LOWW|HIGHW|LOW\(|HIGH\())([A-Za-z_][\w@]*)")
INDIRECT = re.compile(r"^\s*call\s+(?:ax|bc|de|hl)\b")


class StackDepthError(Exception):
    """측정할 입력이 없다 — 0 B 로 읽히지 않게 main 이 오류로 끝낸다."""


def read_lines(asm):
    """CC-RL·어셈블러 출력(.asm·.prn) 한 파일의 줄. 깨진 바이트는 대체 문자로 읽는다."""
    return Path(asm).read_text(encoding="utf-8", errors="replace").splitlines()


def load(build):
    """(own, edges, vectors, indirect, called). own: (파일, 이름) -> 프레임 바이트."""
    if not Path(build).is_dir():
        raise StackDepthError("빌드 디렉터리가 없다: {}".format(build))
    if not any(Path(build).glob("*.asm")):
        raise StackDepthError("{} 에 *.asm 이 없다 (.asm 출력이 꺼졌거나 빌드 산출물이 비었다)".format(build))
    own = {}
    edges = {}
    vectors = {}
    indirect = 0
    called = set()      # call·br 로 직접 부른 이름
    sites = []          # 간접 호출이 있는 함수
    addr = set()        # 주소를 취한 이름(함수 포인터 후보)
    for asm in sorted(Path(build).glob("*.asm")):
        func = None
        for line in read_lines(asm):
            m = VECTOR.match(line)
            if m:
                vectors[(asm.name, m.group(1))] = m.group(2)
                continue
            m = STACK.match(line)
            if m:
                own[(asm.name, m.group(1))] = int(m.group(2))
                func = (asm.name, m.group(1))     # 지시어는 함수 라벨 바로 다음 줄이다
                edges.setdefault(func, set())
                continue
            if func is None:
                continue
            for name in ADDR.findall(line):
                addr.add(name)
            if INDIRECT.match(line):
                indirect += 1
                sites.append(func)
                continue
            m = CALL.match(line)
            if m and not m.group(1).startswith("."):
                edges[func].add(m.group(1))
                called.add(m.group(1))
    # 간접 호출은 주소를 취한 함수 전부를 피호출자로 본다(상한). 함수 이름이 아닌 것은 resolve 가 버린다.
    for func in sites:
        edges[func] |= addr
    return own, edges, vectors, indirect, called


def analyze(build):
    """{main: (바이트, 경로), isrs: [(바이트, 경로, 이름, 벡터)], unresolved: [이름], indirect: 수, functions: 수}"""
    own, edges, vectors, indirect, called = load(build)
    if not own:
        raise StackDepthError("{} 의 *.asm 에 .STACK 지시어가 없다 (스택 정보를 내는 옵션이 꺼졌다)".format(build))
    by_name = {}
    for (f, n) in own:
        by_name.setdefault(n, []).append((f, n))

    def resolve(file, name):
        if (file, name) in own:
            return (file, name)
        cands = by_name.get(name, [])
        return cands[0] if cands else None

    memo = {}

    def depth(key, seen=()):
        if key in memo:
            return memo[key]
        if key in seen:
            return (0, [])  # 재귀 고리는 한 번만 센다(되돌아온 호출은 더하지 않는다)
        best = (0, [])
        for callee in sorted(edges.get(key, ())):
            target = resolve(key[0], callee)
            if target is None:
                continue
            found = depth(target, seen + (key,))
            if found[0] > best[0]:
                best = found
        result = (own[key] + best[0], [key[1]] + best[1])
        memo[key] = result
        return result

    roots = [k for k in own if k[1] in ("_main", "main")]
    isrs = sorted(((*depth(k), k[1], vectors[k]) for k in own if k in vectors), key=lambda r: -r[0])
    return {
        "main": max((depth(k) for k in roots), default=(0, [])),
        "isrs": isrs,
        "unresolved": sorted(c for c in called if c.startswith("_") and resolve("", c) is None),
        "indirect": indirect,
        "functions": len(own),
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    build = Path(argv[0]) if argv else ROOT / "DefaultBuild"
    try:
        result = analyze(build)
    except StackDepthError as error:
        print("stack_depth: {}".format(error), file=sys.stderr)
        return 1
    main_bytes, main_path = result["main"]
    isr_max = result["isrs"][0][0] if result["isrs"] else 0
    print("build", build)
    print("함수 수(.STACK)", result["functions"], "벡터 ISR", len(result["isrs"]),
          "간접 호출(call ax 등)", result["indirect"])
    print("main 최대 경로 {} B: {}".format(main_bytes, " > ".join(main_path)))
    for size, path, name, vector in result["isrs"]:
        print("ISR {} (vector {}) {} B: {}".format(name, vector, size, " > ".join(path)))
    print("합산에서 빠진 피호출자(.STACK 없음, 라이브러리 등) {}개: {}".format(
        len(result["unresolved"]), ", ".join(result["unresolved"])))
    print("main + 최대 ISR 합 {} B (ISR 중첩 없음 가정, 하한)".format(main_bytes + isr_max))
    return 0


if __name__ == "__main__":
    sys.exit(main())
