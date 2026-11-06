#!/usr/bin/env python3
"""변이 스키마타(이슈 14): 한 구성의 모든 변이를 바이너리 하나에 넣고 실행할 때 번호로 켠다.

변이마다 TU 를 다시 컴파일하는 현재 방식(per-mutant)은 시간의 약 85% 가 컴파일이다. 스키마타는 변이 위치를
런타임 분기로 감싼 사본을 구성마다 한 번만 컴파일하고, 변이 번호를 환경변수 EM_MUTANT 로 받아 그 변이만 켠다.

- 식 변이(비교·`&&`/`||`·`+`/`-`·상수 ±1·하위 조건 참/거짓·인자 치환): `(em_mutant == k ? (변이) : (원본))`.
  피연산자는 선택된 분기에서만 평가되므로 부작용 순서가 원본과 같다. 연산자 치환은 피연산자 경계를 우선순위로
  찾아 `L op R` 전체를 감싼다.
- 문장 삭제: `{ if (em_mutant != k) { 문장 } }`. 바깥 중괄호가 `if`/`else` 본문의 dangling else 를 막는다.
- 감쌀 수 없는 변이는 혼합 모드로 변이별 컴파일한다(`Site.mixed`): 상수식 문맥(`case` 라벨·`static` 선언·
  `sizeof` 안), 선언처럼 보이는 문장 삭제, 지시문을 품은 식 구간, 서로 엇갈리는 구간, 그리고 스키마타 사본이
  컴파일되지 않을 때 그 오류가 가리키는 변이.

생성은 exhaustive_mutation.generate 가 한다. 이 모듈은 이미 만든 변이를 어떻게 적용할지만 바꾸므로 변이 식별 키는
같다. 제품 소스와 test/*.c 는 건드리지 않고 격리 사본의 대상 파일만 바꾼다.
"""
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import bisect
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import exhaustive_mutation as em  # noqa: E402
import verification_snapshot as vs  # noqa: E402

ENV = "EM_MUTANT"
VAR = "em_mutant"
KILL_PROBE_ID = 0                 # 공개 void 함수를 비우는 kill probe 의 변이 번호
SURVIVE_PROBE_ID = 999999999      # 스키마타에 없는 번호: 어느 분기도 켜지 않는다(비활성 분기 안의 변이와 같다)
MAX_ROUNDS = 12                   # 컴파일 오류 변이를 걷어내는 재시도 상한

# 호스트 하네스. -include 로 먼저 넣어 대상 파일이 em_mutant 를 쓸 수 있게 한다.
HEADER = """/* 변이 스키마타 하네스 — 측정 도구가 만든 사본 전용. 변이 번호는 환경변수 {env} 에서 한 번 읽는다. */
#include <stdlib.h>
static int {var} = -1;
static void {var}_init(void) __attribute__((constructor));
static void {var}_init(void)
{{
    const char *text = getenv("{env}");
    {var} = text ? atoi(text) : -1;
}}
""".format(env=ENV, var=VAR)

# 이항 연산자 우선순위(클수록 강하다). 복합 대입·`?:`·`,` 는 모두 이보다 낮아 피연산자를 끊는다.
PRECEDENCE = {"*": 10, "/": 10, "%": 10, "+": 9, "-": 9, "<<": 8, ">>": 8,
              "<": 7, "<=": 7, ">": 7, ">=": 7, "==": 6, "!=": 6,
              "&": 5, "^": 4, "|": 3, "&&": 2, "||": 1}
AMBIGUOUS = ("+", "-", "*", "&")   # 단항일 수 있어 앞 토큰으로 가른다
LOWEST = frozenset([",", ";", "?", ":", "=", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=",
                    "<<=", ">>=", "{", "}"])
HALTING = frozenset(em.KEYWORDS - {"sizeof"})   # return case else ... — 식이 여기서 끝난다
OPERATOR_SWAPS = ("comparison", "logical", "arithmetic")
SELF_CONTAINED = ("enum", "argument", "condition", "constant")


@dataclass
class Site:
    """변이 하나가 들어갈 자리. mixed 가 비어 있지 않으면 스키마타에 넣지 않는다(이유 문자열)."""
    ident: int
    kind: str                # expr | stmt
    start: int = 0           # 구간(원본 문자 오프셋)
    end: int = 0
    arm: str = ""            # expr: 변이된 구간 전체 텍스트
    mixed: str = ""


def previous_operand_end(tokens, index):
    """tokens[index] 가 피연산자의 끝인가. 후위 ++/-- 는 그 앞 토큰으로 판단한다."""
    token = tokens[index]
    if token.text in ("++", "--"):
        return index > 0 and previous_operand_end(tokens, index - 1)
    if token.kind == "id":
        return token.text not in em.KEYWORDS
    return token.kind in ("num", "str") or token.text in (")", "]")


def binary_precedence(tokens, index):
    """tokens[index] 가 이항 연산자이면 우선순위, 아니면 None. `(type)-x` 같은 캐스트 뒤 단항은 가르지 못한다."""
    text = tokens[index].text
    if text not in PRECEDENCE:
        return None
    if text in AMBIGUOUS and not (index > 0 and previous_operand_end(tokens, index - 1)):
        return None
    return PRECEDENCE[text]


def opening_of(tokens, index):
    """tokens[index] 의 ) ] 에 짝이 되는 여는 토큰 위치."""
    closers = {")": "(", "]": "["}
    opener, closer = closers[tokens[index].text], tokens[index].text
    depth = 0
    for position in range(index, -1, -1):
        if tokens[position].text == closer:
            depth += 1
        elif tokens[position].text == opener:
            depth -= 1
            if depth == 0:
                return position
    raise ValueError("열리지 않은 괄호: {}".format(tokens[index]))


def operand_span(tokens, operator, floor, ceiling):
    """tokens[operator] 이항 연산자의 (왼쪽 피연산자 시작, 오른쪽 피연산자 끝) 토큰 위치. 못 찾으면 None.

    왼쪽은 더 약한 이항 연산자, 오른쪽은 같거나 더 약한 이항 연산자(좌결합)·`?:`·`,`·대입·`;` 에서 끊긴다.
    floor·ceiling 은 함수 본문의 여는·닫는 중괄호 위치다."""
    precedence = PRECEDENCE[tokens[operator].text]
    low = None
    index = operator - 1
    while index > floor:
        token = tokens[index]
        if token.text in (")", "]"):
            index = opening_of(tokens, index)
            low = index
            index -= 1
            continue
        if token.text in ("(", "[") or token.text in LOWEST or \
                (token.kind == "id" and token.text in HALTING):
            break
        found = binary_precedence(tokens, index)
        if found is not None and found < precedence:
            break
        low = index
        index -= 1
    high = None
    index = operator + 1
    while index < ceiling:
        token = tokens[index]
        if token.text in ("(", "["):
            index = em.matching(tokens, index)
            high = index
            index += 1
            continue
        if token.text in (")", "]") or token.text in LOWEST or \
                (token.kind == "id" and token.text in HALTING):
            break
        found = binary_precedence(tokens, index)
        if found is not None and found <= precedence:
            break
        high = index
        index += 1
    if low is None or high is None:
        return None
    return low, high


def keeps_grouping(tokens, low, high, original, replacement):
    """`L original R` 구간 [low, high] 의 연산자를 replacement 로 바꿔도, 구간 밖 이웃 연산자와의 묶임이 변이별
    컴파일(텍스트 치환 뒤 다시 파싱)과 같은가.

    우선순위가 같으면 같다. 낮아지면(`&&` → `||`) 구간 밖에서 멈춘 이웃 연산자가 새 연산자보다 강하거나 같은 쪽으로
    피연산자를 가져가므로 `a && b && c` 의 첫 `&&` 가 `(a || b) && c` 가 아니라 `a || (b && c)` 로 읽힌다.
    높아지는 쪽은 이웃이 이미 더 약해 그대로 끊긴다."""
    old, new = PRECEDENCE[original], PRECEDENCE[replacement]
    if new >= old:
        return True
    left = binary_precedence(tokens, low - 1)
    right = binary_precedence(tokens, high + 1)
    return not ((left is not None and left >= new) or (right is not None and right > new))


def case_labels(tokens):
    """`case` 라벨의 상수식 토큰 구간 [case, 짝 콜론]."""
    ranges = []
    for index, token in enumerate(tokens):
        if token.kind == "id" and token.text == "case":
            depth = 0
            position = index + 1
            while position < len(tokens):
                text = tokens[position].text
                if text in "([{":
                    position = em.matching(tokens, position) + 1
                    continue
                if text == "?":
                    depth += 1
                elif text == ":":
                    if depth == 0:
                        break
                    depth -= 1
                position += 1
            ranges.append((index, position))
    return ranges


def statement_ranges(tokens, opening, closing, first_words):
    """본문 안에서 첫 토큰이 first_words 에 있는 문장의 토큰 구간 [첫 토큰, 끝 세미콜론]."""
    ranges = []
    index = opening + 1
    while index < closing:
        token = tokens[index]
        if token.kind == "id" and token.text in first_words:
            end = index
            while tokens[end].text != ";":
                end = em.matching(tokens, end) + 1 if tokens[end].text in "([{" else end + 1
            ranges.append((index, end))
            index = end
        index += 1
    return ranges


def sizeof_groups(tokens):
    ranges = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind == "id" and token.text == "sizeof" and tokens[index + 1].text == "(":
            ranges.append((index, em.matching(tokens, index + 1)))
    return ranges


def looks_like_declaration(tokens, first, semicolon):
    """`u08 *p = ...;` 처럼 식별자·`*` 로만 이뤄진 접두 뒤에 선언자가 오는 문장. 선언을 지우면 이름이 사라져
    뒤의 모든 사용이 컴파일 오류가 되는데, 그 오류는 변이 구간 밖이라 오류 위치로 변이를 찾을 수 없다."""
    if tokens[first].kind != "id":
        return False
    chain = 0
    index = first
    while index <= semicolon and (tokens[index].kind == "id" or tokens[index].text == "*"):
        if tokens[index].kind == "id" and tokens[index].text in em.KEYWORDS:
            return False
        chain += 1
        index += 1
    return chain >= 2 and index <= semicolon and tokens[index - 1].kind == "id" and \
        tokens[index].text in ("=", "[", ";", ",")


def inside(ranges, low, high):
    """[low, high] 토큰 구간과 겹치는 구간이 있는가."""
    return any(start <= high and low <= end for start, end in ranges)


MACRO = re.compile(r"#define\s+(\w+)\(([^)]*)\)\s*(.*)$")
ASSIGNED = r"\s*\)*\s*(?:(?:<<|>>|[-+*/%&^|])?=(?!=)|\+\+|--)"


def macro_table(defines):
    """`gcc -dM -E` 출력에서 함수형 매크로 {이름: (매개변수 이름 목록, 본문)}."""
    table = {}
    for line in defines.splitlines():
        match = MACRO.match(line)
        if match:
            table[match.group(1)] = ([p.strip() for p in match.group(2).split(",") if p.strip()],
                                     match.group(3))
    return table


def call_arguments(body, name):
    """body 안의 `name(...)` 호출마다 인자 문자열 목록(최상위 쉼표로 가른다)."""
    calls = []
    for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"\s*\(", body):
        depth, start, arguments = 0, match.end(), []
        for index in range(match.end(), len(body)):
            char = body[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    arguments.append(body[start:index])
                    break
                depth -= 1
            elif char == "," and depth == 0:
                arguments.append(body[start:index])
                start = index + 1
        calls.append([a.strip() for a in arguments])
    return calls


def unparenthesized(text):
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text


def lvalue_parameters(defines):
    """대입·증감·주소 연산의 대상이 되는 매크로 매개변수 {이름: {인자 위치}} (다른 매크로로 넘기는 것까지 따라간다).

    이런 자리의 인자를 상수로 바꾸면 변이별 컴파일도 `lvalue required` 로 실패하고, 스키마타 사본에서는
    `(em_mutant == k ? 0 : x) = …` 로 사본 전체가 컴파일되지 않는다. 사본 오류에서 변이를 찾는 것보다 미리 혼합
    모드로 돌리는 편이 훨씬 싸다. 놓쳐도 사본 컴파일 오류 경로가 잡으므로 최적화일 뿐 정확성은 걸리지 않는다."""
    table = macro_table(defines)
    found = {name: set() for name in table}
    changed = True
    while changed:
        changed = False
        for name, (parameters, body) in table.items():
            for index, parameter in enumerate(parameters):
                if index in found[name]:
                    continue
                word = r"(?<!\w)" + re.escape(parameter) + r"(?!\w)"
                direct = (re.search(word + ASSIGNED, body) or
                          re.search(r"(?:\+\+|--|&)\s*\(*\s*" + word, body))
                passed = any(unparenthesized(arguments[position]) == parameter
                             for callee in table if callee != name and callee in body
                             for arguments in call_arguments(body, callee)
                             for position in found[callee] if position < len(arguments))
                if direct or passed:
                    found[name].add(index)
                    changed = True
    return {name: positions for name, positions in found.items() if positions}


def enclosing_call(tokens, index, opening):
    """tokens[index] 가 인자인 호출의 (피호출자 이름, 인자 위치). 호출 안이 아니면 None."""
    depth, position = 0, 0
    for scan in range(index - 1, opening, -1):
        text = tokens[scan].text
        if text in (")", "]", "}"):
            depth += 1
        elif text in ("(", "[", "{"):
            if depth == 0:
                callee = tokens[scan - 1]
                return (callee.text, position) if text == "(" and callee.kind == "id" else None
            depth -= 1
        elif text == "," and depth == 0:
            position += 1
    return None


# 함수형 매크로 인자를 상수로 바꾼 변이 팔 앞에 붙이는 컴파일 시점 검사. 원본 인자가 int 로 승격되지 않는 형(unsigned,
# long, 포인터, 실수, 구조체)이면 배열 크기가 음수라 컴파일 오류 → 혼합 모드가 된다. 이유: `?:` 는 두 팔을 공통 형으로
# 바꾸므로 `F(em_mutant == k ? 0 : x)` 의 0 이 x 의 형(unsigned long)이 되어, 변이별 컴파일의 `F(0)`(int)과 매크로 안의
# 산술(`(a) - 1`)이 다르게 계산된다. 일반 함수 호출은 매개변수 형으로 바뀌므로 검사하지 않는다.
INT_GUARD = "(void)sizeof(char[__builtin_types_compatible_p(__typeof__(+({})), int) ? 1 : -1]), "

# 상수·산술·enum 변이 팔 앞에 붙이는 검사: 팔과 원문의 형이 같아야 한다. 같지 않으면 `?:` 가 두 팔을 공통 형으로 바꿔
# (`-0x80000000 / 2` 의 0x80000000 → 0x7fffffff 에서 unsigned 가 int 로, 또는 그 반대로) 변이별 컴파일과 값이 달라지므로
# 컴파일 오류 → 혼합 모드로 보낸다. 조건식 변이는 참거짓만 쓰므로 형이 달라도 결과가 같아 검사하지 않는다.
TYPE_GUARD = "(void)sizeof(char[__builtin_types_compatible_p(__typeof__({arm}), __typeof__({orig})) ? 1 : -1]), "
TYPE_GUARDED = ("constant", "arithmetic", "enum")


def plan(source, tokens, items, lvalues=None, macros=None):
    """items: [(번호, Mutant)]. 번호마다 Site 를 낸다(스키마타에 못 넣는 것은 mixed 에 이유가 있다).
    lvalues 는 lvalue_parameters 의 결과, macros 는 함수형 매크로 이름 집합이다."""
    lvalues = lvalues or {}
    macros = macros or frozenset()
    starts = [token.start for token in tokens]
    bodies = [(opening, closing) for *_, opening, closing in em.functions(tokens)]
    labels = case_labels(tokens)
    sizeofs = sizeof_groups(tokens)
    statics = []
    for opening, closing in bodies:
        statics += statement_ranges(tokens, opening, closing, {"static"})
    sites = []
    for ident, mutant in items:
        low = bisect.bisect_left(starts, mutant.start)
        high = bisect.bisect_left(starts, mutant.end) - 1
        body = next(((o, c) for o, c in bodies if o < low and high < c), None)
        if body is None:
            sites.append(Site(ident, "expr", mixed="함수 본문 밖"))
            continue
        if mutant.operator == "statement":
            site = Site(ident, "stmt", mutant.start, mutant.end)
            if looks_like_declaration(tokens, low, high):
                site.mixed = "선언처럼 보이는 문장"
            elif inside(statics, low, high):
                site.mixed = "static 선언"
            sites.append(site)
            continue
        site = Site(ident, "expr", mutant.start, mutant.end, mutant.replacement)
        if mutant.operator in ("argument", "enum"):
            call = enclosing_call(tokens, low, body[0])
            if call and call[1] in lvalues.get(call[0], ()):
                site.mixed = "매크로 대상 인자"
                sites.append(site)
                continue
            if call and call[0] in macros and mutant.operator == "argument":
                site.arm = "(" + INT_GUARD.format(tokens[low].text) + mutant.replacement + ")"
        tlow, thigh = low, high
        if mutant.operator in OPERATOR_SWAPS:
            span = operand_span(tokens, low, body[0], body[1])
            if span is None:
                site.mixed = "피연산자 경계를 못 찾음"
                sites.append(site)
                continue
            tlow, thigh = span
            if not keeps_grouping(tokens, tlow, thigh, tokens[low].text, mutant.replacement):
                site.mixed = "우선순위가 바뀌는 치환"
                sites.append(site)
                continue
            site.start, site.end = tokens[tlow].start, tokens[thigh].end
            site.arm = source[site.start:mutant.start] + mutant.replacement + \
                source[mutant.end:site.end]
        if inside(labels, tlow, thigh):
            site.mixed = "case 라벨"
        elif inside(statics, tlow, thigh):
            site.mixed = "static 선언"
        elif inside(sizeofs, tlow, thigh):
            site.mixed = "sizeof 안"
        elif re.search(r"^[ \t]*#", source[site.start:site.end], re.M):
            site.mixed = "지시문을 품은 구간"
        elif mutant.operator in TYPE_GUARDED:
            site.arm = "(" + TYPE_GUARD.format(arm=site.arm, orig=source[site.start:site.end]) + site.arm + ")"
        sites.append(site)
    return sites


@dataclass
class Node:
    start: int
    end: int
    kind: str
    arms: list = field(default_factory=list)     # [(번호, 변이 텍스트)] expr, [번호] stmt
    children: list = field(default_factory=list)


def nest(sites):
    """구간이 같으면 한 노드로 묶고 포함 관계로 트리를 만든다. 서로 엇갈리는 구간의 변이는 mixed 로 돌리고
    (이유를 적는다) 그 번호들을 돌려준다."""
    crossing = []
    keyed = {}
    for site in sites:
        if site.mixed:
            continue
        keyed.setdefault((site.start, site.end, site.kind), []).append(site)
    root = Node(0, 1 << 60, "root")
    stack = [root]
    for (start, end, kind), group in sorted(keyed.items(), key=lambda i: (i[0][0], -i[0][1])):
        while stack[-1].end <= start:
            stack.pop()
        if end > stack[-1].end:   # 부모 구간 안에서 시작해 밖에서 끝난다
            for site in group:
                site.mixed = "엇갈리는 구간"
                crossing.append(site.ident)
            continue
        node = Node(start, end, kind)
        for site in group:
            node.arms.append(site.ident if kind == "stmt" else (site.ident, site.arm))
        stack[-1].children.append(node)
        stack.append(node)
    return root, crossing


class Builder:
    def __init__(self):
        self.parts = []
        self.length = 0
        self.spans = []   # (시작, 끝, 번호, 종류 expr|stmt)

    def write(self, text):
        self.parts.append(text)
        self.length += len(text)

    def text(self):
        return "".join(self.parts)


def render(builder, source, node, cursor, end):
    """node 의 자식 사이 원문을 쓰며 자식을 재귀로 감싼다."""
    for child in node.children:
        builder.write(source[cursor:child.start])
        wrap(builder, source, child)
        cursor = child.end
    builder.write(source[cursor:end])


def wrap(builder, source, node):
    if node.kind == "stmt":
        (ident,) = node.arms
        began = builder.length
        builder.write("{{ if ({} != {}) {{ ".format(VAR, ident))
        render(builder, source, node, node.start, node.end)
        builder.write(" } }")
        builder.spans.append((began, builder.length, ident, "stmt"))
        return
    builder.write("(")
    for ident, arm in node.arms:
        builder.write("{} == {} ? (".format(VAR, ident))
        began = builder.length
        builder.write(arm)
        builder.spans.append((began, builder.length, ident, "expr"))
        builder.write(") : ")
    builder.write("(")
    render(builder, source, node, node.start, node.end)
    builder.write("))")


def kill_guard(source, name):
    """kill probe 를 스키마타에 넣는 자리: (삽입 오프셋, 삽입 텍스트). 함수 본문 첫 줄에서 인자를 소비하고 돌아온다.
    공개 void 함수만 대상이다(em.kill_probe 와 같은 선택)."""
    tokens = em.tokenize(source)
    for function, is_static, is_void, parameters, opening, closing in em.functions(tokens):
        if (name is None and is_void and not is_static) or function == name:
            body = "".join(" (void){};".format(p) for p in parameters)
            return function, tokens[opening].end, \
                " if ({} == {}) {{{} return; }}".format(VAR, KILL_PROBE_ID, body)
    raise ValueError("inert 로 만들 공개 void 함수가 없다: {}".format(name))


def emit(source, sites, guard=None):
    """(스키마타 텍스트, [(시작, 끝, 번호, 종류)]). guard 는 kill_guard 의 (이름, 오프셋, 텍스트)."""
    root, _ = nest(sites)
    builder = Builder()
    if guard is None:
        render(builder, source, root, 0, len(source))
        return builder.text(), builder.spans
    _, offset, text = guard
    # 가드는 본문의 여는 중괄호 바로 뒤라 어떤 변이 구간과도 겹치지 않는다
    head = Node(0, offset, "root")
    tail = Node(offset, len(source), "root")
    for child in root.children:
        (head if child.end <= offset else tail).children.append(child)
    render(builder, source, head, 0, offset)
    builder.write(text)
    render(builder, source, tail, offset, len(source))
    return builder.text(), builder.spans


# ---------------------------------------------------------------------------------------------
# 컴파일: 오류가 가리키는 변이를 걷어낸다
# ---------------------------------------------------------------------------------------------

DIAGNOSTIC = re.compile(r"^((?:[A-Za-z]:)?[^:\n]+):(\d+):(\d+): (?:fatal )?error: (.*)$", re.M)
LOCATION = re.compile(r"^((?:[A-Za-z]:)?[^:\n]+):(\d+):(\d+):", re.M)


def flags_for(config, header):
    """스키마타 사본용 컴파일 인자. 경고는 판정에 쓰지 않으므로 끈다(-w, -Werror 제거) —
    invalid 는 살아남은 변이를 변이별로 다시 컴파일해 판정한다."""
    base = [flag for flag in config["base"] if flag != "-Werror"]
    return base + ["-w", "-include", str(header), "-fdiagnostics-column-unit=byte",
                   "-fdiagnostics-color=never"]


def char_offset(text, line_starts, line, column):
    start = line_starts[line - 1]
    end = text.find("\n", start)
    end = len(text) if end < 0 else end
    prefix = text[start:end].encode("utf-8")[:column - 1].decode("utf-8", errors="ignore")
    return start + len(prefix)


CALL = re.compile(r"[A-Za-z_]\w*\s*\(")


def call_extent(text, offset):
    """text[offset] 에서 시작하는 `이름(…)` 호출의 [시작, 닫는 괄호 뒤) 또는 None. 문자열 안의 괄호는 모른다."""
    match = CALL.match(text, offset)
    if not match:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return offset, index + 1
    return None


def blamed(text, spans, target_path, stderr):
    """컴파일러 출력의 위치(오류·`in expansion of macro` 노트 포함) 중 대상 파일 안의 것이 가리키는 변이 번호 집합.

    위치가 식 변이 구간 안이면 그 가장 안쪽 구간이다. 매크로 전개 오류는 위치가 인자가 아니라 매크로 호출의 첫
    글자라(변이 구간 밖이거나 문장 삭제 구간 안) 그 호출 안의 식 변이 전부를 의심한다 — 어느 인자 때문인지는
    메시지로 알 수 없어 넓게 잡고, 걷어낸 변이는 혼합 모드에서 제 판정을 받는다."""
    line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    found = set()
    wanted = vs.normalized(target_path)
    for match in LOCATION.finditer(stderr):
        if vs.normalized(match.group(1)) != wanted:
            continue
        line, column = int(match.group(2)), int(match.group(3))
        if line > len(line_starts):
            continue
        offset = char_offset(text, line_starts, line, column)
        containing = [s for s in spans if s[0] <= offset < s[1]]
        inner = min(containing, key=lambda s: s[1] - s[0]) if containing else None
        if inner is None or inner[3] == "stmt":
            extent = call_extent(text, offset)
            suspects = {s[2] for s in spans if s[3] == "expr" and extent and
                        extent[0] <= s[0] and s[1] <= extent[1]}
            if suspects:
                found |= suspects
                continue
        if inner is not None:
            found.add(inner[2])
    return found


class SchemataError(em.ToolError):
    pass


def syntax_check(worktree, config, header):
    """-fsyntax-only 컴파일. (성공 여부, stderr)."""
    result = subprocess.run(flags_for(config, header) + ["-fsyntax-only"], cwd=worktree,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120)
    return result.returncode == 0, result.stderr


@dataclass
class Build:
    """구성별 스키마타 실행 파일과 변이 배치."""
    exes: dict                 # 구성 이름 -> 실행 파일 경로
    sites: dict                # 번호 -> Site
    probe: str                 # kill probe 가 비운 함수 이름
    rounds: int = 0
    seconds: float = 0.0       # 스키마타 컴파일에 쓴 벽시계 초(구성 병렬)

    def wrapped(self):
        return sorted(i for i, s in self.sites.items() if not s.mixed)

    def mixed(self):
        return sorted(((i, s.mixed) for i, s in self.sites.items() if s.mixed))


def build(worktree, out, target, configs, source, items, kill_probe_name=None, progress=None,
          lvalues=None, macros=None):
    """items: [(번호, Mutant)]. 구성마다 스키마타 실행 파일을 만든다. 컴파일이 안 되는 사본이면 오류가 가리키는
    변이(위치로 못 찾으면 이분 탐색)를 혼합 모드로 돌리고 다시 컴파일한다. 원본 대상 파일은 끝에 되돌린다."""
    progress = progress or (lambda message: None)
    tokens = em.tokenize(source)
    sites = {site.ident: site for site in plan(source, tokens, items, lvalues, macros)}
    _, crossing = nest(list(sites.values()))
    for ident in crossing:
        sites[ident].mixed = "엇갈리는 구간"
    guard = kill_guard(source, kill_probe_name)
    header = Path(out) / "em_schemata.h"
    header.write_text(HEADER, encoding="utf-8")
    path = worktree / target
    original = path.read_bytes()
    began = time.monotonic()

    def text_for(allowed):
        return emit(source, [sites[i] for i in sorted(allowed)], guard)

    def check_all(text):
        path.write_bytes(text.encode("utf-8"))
        with ThreadPoolExecutor(max_workers=len(configs)) as pool:
            return list(pool.map(lambda c: syntax_check(worktree, c, header), configs))

    def failing(allowed):
        return [stderr for ok, stderr in check_all(text_for(allowed)[0]) if not ok]

    def bisect_culprits(allowed):
        """allowed 중 혼자서도 컴파일이 안 되는 변이들. 오류 위치로 못 찾은 경우의 마지막 수단이다."""
        if not allowed or not failing(allowed):
            return []
        if len(allowed) == 1:
            return list(allowed)
        middle = len(allowed) // 2
        return bisect_culprits(allowed[:middle]) + bisect_culprits(allowed[middle:])

    rounds = 0
    try:
        allowed = [i for i, s in sites.items() if not s.mixed]
        while True:
            text, spans = text_for(allowed)
            failures = [stderr for ok, stderr in check_all(text) if not ok]
            if not failures:
                break
            rounds += 1
            if rounds > MAX_ROUNDS:
                raise SchemataError("스키마타 사본이 {}번 걷어내도 컴파일되지 않는다".format(MAX_ROUNDS))
            culprits = set()
            for stderr in failures:
                culprits |= blamed(text, spans, path, stderr)
            culprits.discard(KILL_PROBE_ID)
            culprits &= set(allowed)
            how = "컴파일 오류 위치"
            if not culprits:
                if all(not DIAGNOSTIC.search(stderr) for stderr in failures):
                    raise SchemataError("스키마타 컴파일 실패(오류 줄 없음): " + failures[0][-800:])
                culprits = set(bisect_culprits(sorted(allowed)))
                how = "이분 탐색"
                if not culprits:
                    raise SchemataError("스키마타 컴파일 실패를 변이로 돌릴 수 없다: " + failures[0][-800:])
            for ident in culprits:
                sites[ident].mixed = "컴파일 오류({})".format(how)
            allowed = [i for i in allowed if i not in culprits]
            progress("스키마타 컴파일 오류 변이 {}개를 혼합 모드로 돌린다 ({}, 라운드 {})".format(
                len(culprits), how, rounds))
        path.write_bytes(text.encode("utf-8"))

        def compile_one(config):
            exe = os.path.join(out, "schemata-" + config["exe"])
            result = subprocess.run(flags_for(config, header) + ["-o", exe], cwd=worktree,
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=600)
            if result.returncode:
                raise SchemataError("스키마타 컴파일 실패({}): {}".format(
                    config["name"], result.stderr[-800:]))
            return config["name"], exe
        with ThreadPoolExecutor(max_workers=len(configs)) as pool:
            exes = dict(pool.map(compile_one, configs))
    finally:
        path.write_bytes(original)
    return Build(exes, sites, guard[0], rounds, time.monotonic() - began)


def run_exe(exe, cwd, ident, timeout):
    """스키마타 실행 파일을 변이 번호 ident 로 돌린다(ident None 이면 변이 없음). (결과, 초).
    기동의 일시적 PermissionError 재시도는 em.launch 가 맡는다."""
    env = dict(os.environ)
    env.pop(ENV, None)
    if ident is not None:
        env[ENV] = str(ident)
    began = time.monotonic()
    try:
        run = em.launch(exe, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", time.monotonic() - began
    return ("pass" if run.returncode == 0 else "run_fail"), time.monotonic() - began


def judge(bench, built, ident, mutated=None, validate="syntax"):
    """스키마타 번호 ident(None 이면 변이 없음)의 판정 (Verdict, 구성별 결과).

    구성을 비용 순서로 돌리다 실행 실패·timeout 이 나오면 killed 로 확정한다(per-mutant 와 같다).
    스키마타 사본은 경고를 끄고 원본 분기가 함께 있어, 변이만의 경고(-Wunused-but-set-variable,
    -Wmaybe-uninitialized, 항상 거짓인 비교 …)가 사라진다. 그래서 invalid 는 변이별로 따로 가린다.
    mutated 는 변이별 컴파일용 텍스트를 만드는 함수다(None 이면 가리지 않는다 — baseline·probe). validate:
    - "syntax": 구성마다 실행 전에 변이 텍스트를 -fsyntax-only(게이트 플래그, -Werror)로 검증한다. 실패하면
      전체 컴파일도 실패하므로 invalid 가 확정이고 그 구성은 실행하지 않는다. 통과해도 전체 컴파일에서만 나오는
      경고(-O2 흐름 분석)가 있을 수 있어, 전부 통과한 변이는 전체 컴파일로 다시 가린다. 실행에서 죽은 변이의
      전체 컴파일 전용 경고는 가리지 못한다(per-mutant 의 invalid 가 killed 로 남는다).
    - "survivors": 전부 통과한 변이만 전체 컴파일로 가린다(전단 검증 없음).
    - "all": 구성마다 전체 컴파일로 먼저 가리고 통과하면 실행한다(per-mutant 와 같은 invalid 판정.
      대조용이며 컴파일을 변이별로 하므로 느리다)."""
    outcomes = []
    with bench.mutated(mutated() if mutated is not None else None):
        for config in bench.configs:
            if mutated is not None and validate in ("syntax", "all") and                     not bench.compile_check(config, syntax_only=(validate == "syntax")):
                outcomes.append(em.ConfigResult(config["name"], "build_fail"))
                continue
            result, seconds = run_exe(built.exes[config["name"]], bench.worktree, ident,
                                      bench.run_timeout)
            em.TIMING.add("timeout" if result == "timeout" else "run", seconds)
            outcomes.append(em.ConfigResult(config["name"], result))
            if result in ("run_fail", "timeout"):
                break
        if mutated is not None and validate != "all" and                 all(o.outcome == "pass" for o in outcomes):
            outcomes = [em.ConfigResult(config["name"],
                                        "pass" if bench.compile_check(config) else "build_fail")
                        for config in bench.configs]
    results = tuple(outcomes)
    return em.classify(results), results
