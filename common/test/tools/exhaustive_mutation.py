#!/usr/bin/env python3
"""제품 .c 파일 하나의 전수 변이를 호스트 러너의 모든 구성에서 돌려 생존자를 보고한다.

측정 도구이지 게이트가 아니다. 호스트 게이트 설정(CC, CFLAGS, VARIANTS)의 권위는
test/run_tests.py 에 있고, 이 도구는 그것을 격리된 사본에서 읽어 쓴다.

    python -B test/tools/exhaustive_mutation.py \
        library/dfm.c [--kill-probe 함수] [--json 경로] [--baseline 경로]

생존자는 대상별 기준선(test/mutation_baselines/<대상>.json, ADR 0024)과 대조해 새로 생김·사라짐·
안전 경로(docs/agents/host-test-project.md 표)의 미검토만 따로 보고한다. 기준선은 읽기만 한다.

측정 방식은 둘이다(이슈 14·17, ADR 0029). 기본은 한 구성의 모든 변이를 바이너리 하나에 넣고 실행할 때
번호(환경변수 EM_MUTANT)로 켜는 변이 스키마타 syntax(schemata.py)이고, `--per-mutant` 는 변이마다 TU 를
다시 컴파일하는 per-mutant 다.
어느 쪽이든 변이 생성·식별 키·판정 분류는 같고, 보고서의 `method` 와 review_context 에 방식이 남는다.

연산자: 비교 상호 치환, && <-> ||, 이항 + <-> -, 정수 상수 +-1, 문장 삭제(`;`),
if/while 조건과 그 && || 하위 조건 항상 참/거짓, 인자 enum 형제 치환, 단일 식별자·0 아닌
리터럴 인자 -> 0. 함수 본문 안만 변이한다(주석·문자열·전처리 지시문·#define 제외).
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
import argparse
import bisect
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import verification_snapshot as vs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# docs/agents/host-test-project.md 무접촉 원칙의 제품 경로. `*.c`·`*.h` 는 깊이를 가리지 않으므로
# 테스트와 `.scratch/` 의 합성 소스는 제외 항목으로 뺀다.
_PROJECT_CONFIG = ROOT / "test" / "host-test.project.json"
PROJECT_CONFIG = json.loads(_PROJECT_CONFIG.read_text(encoding="utf-8")) if _PROJECT_CONFIG.exists() else {}
PRODUCT_PATHSPEC = PROJECT_CONFIG.get("product_pathspec") or ["*.c", "*.h", "*.mtpj", ":(exclude)test/", ":(exclude).scratch/"]


COMPARISONS = ("<", "<=", ">", ">=", "==", "!=")
LOGICAL = {"&&": "||", "||": "&&"}
ARITHMETIC = {"+": "-", "-": "+"}
KEYWORDS = {"return", "case", "sizeof", "if", "else", "while", "for", "do", "switch",
            "break", "continue", "goto", "default"}
PUNCTUATORS = sorted([
    "<<=", ">>=", "...", "->", "++", "--", "<<", ">>", "<=", ">=", "==", "!=",
    "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "^=", "|=", "##",
], key=len, reverse=True)


@dataclass(frozen=True)
class Token:
    kind: str   # id, num, str, op
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Mutant:
    operator: str
    start: int
    end: int
    replacement: str
    line: int
    original_line: str
    mutated_line: str
    function: str = ""
    original: str = ""   # 바뀐 구간의 원래 토큰열, 공백 하나로 이음(줄바꿈·주석 무관)
    ordinal: int = 0     # 파일 안 같은 (함수 이름, 연산자, 원래 토큰열, 치환 결과) 중 몇 번째인지, 1부터

    @property
    def key(self):
        """생존자 기준선의 식별 키(ADR 0024). 줄 번호·뮤턴트 번호는 넣지 않는다 — 무관한 줄이
        끼어도 같아야 한다. 순번이 어긋나면 조용히 오매칭되지 않고 새로 생김 + 사라짐으로 드러난다."""
        return (self.function, self.operator, self.original, self.replacement, self.ordinal)


@dataclass(frozen=True)
class ConfigResult:
    config: str    # "<test 파일 이름>[<variant>]"
    outcome: str   # pass, build_fail, run_fail, timeout


@dataclass(frozen=True)
class Verdict:
    status: str        # killed, invalid, survived
    killed_by: tuple
    invalid_in: tuple


def classify(results):
    """실행 실패·timeout(killed) > 빌드 실패(invalid) > 전부 통과(survived).
    한 구성이라도 테스트가 실행에서 잡았으면 다른 구성의 빌드 실패와 상관없이 killed 다.
    도구·환경 오류(컴파일러 부재, baseline 실패)는 여기 오기 전에 실행 전체를 무효로 만든다."""
    killed_by = tuple(r.config for r in results if r.outcome in ("run_fail", "timeout"))
    invalid_in = tuple(r.config for r in results if r.outcome == "build_fail")
    if killed_by:
        return Verdict("killed", killed_by, invalid_in)
    if invalid_in:
        return Verdict("invalid", (), invalid_in)
    return Verdict("survived", (), ())


def tokenize(source):
    """주석·전처리 지시문을 건너뛰고 C 토큰을 낸다. 문자열·문자 상수는 str 토큰 하나다."""
    tokens = []
    index = 0
    at_line_start = True
    length = len(source)
    while index < length:
        char = source[index]
        if char == "\n":
            at_line_start = True
            index += 1
            continue
        if char in " \t\r\f\v":
            index += 1
            continue
        if source.startswith("//", index):
            index = source.find("\n", index)
            index = length if index < 0 else index
            continue
        if source.startswith("/*", index):
            index = source.index("*/", index) + 2
            continue
        if char == "#" and at_line_start:
            while True:
                end = source.find("\n", index)
                if end < 0:
                    index = length
                    break
                if source[index:end].rstrip("\r").endswith("\\"):
                    index = end + 1
                    continue
                index = end
                break
            continue
        at_line_start = False
        if char in "\"'":
            end = index + 1
            while source[end] != char:
                end += 2 if source[end] == "\\" else 1
            tokens.append(Token("str", source[index:end + 1], index, end + 1))
            index = end + 1
            continue
        match = re.compile(r"[A-Za-z_]\w*").match(source, index)
        if match:
            tokens.append(Token("id", match.group(), index, match.end()))
            index = match.end()
            continue
        match = re.compile(r"(0[xX][0-9A-Fa-f]+|\d+)[uUlL]*").match(source, index)
        if match:
            tokens.append(Token("num", match.group(), index, match.end()))
            index = match.end()
            continue
        for punctuator in PUNCTUATORS:
            if source.startswith(punctuator, index):
                break
        else:
            punctuator = char
        tokens.append(Token("op", punctuator, index, index + len(punctuator)))
        index += len(punctuator)
    return tokens


def function_bodies(tokens):
    """최상위 `) {` 로 시작하는 함수 본문의 토큰 구간 [open, close] 목록."""
    bodies = []
    depth = 0
    opening = None
    for position, token in enumerate(tokens):
        if token.text == "{":
            if depth == 0 and position > 0 and tokens[position - 1].text == ")":
                opening = position
            depth += 1
        elif token.text == "}":
            depth -= 1
            if depth == 0 and opening is not None:
                bodies.append((opening, position))
                opening = None
    return bodies


def ends_operand(token):
    """이 토큰 뒤의 + - 는 이항이다. `(int)-a` 같은 캐스트 뒤 단항은 구분하지 못한다."""
    if token.kind == "id":
        return token.text not in KEYWORDS
    return token.kind == "num" or token.text in (")", "]")


def literal_value(text):
    digits = text.rstrip("uUlL")
    if digits[:2] in ("0x", "0X"):
        return int(digits, 16)
    return int(digits, 8 if len(digits) > 1 and digits[0] == "0" else 10)


def shifted_constants(text):
    """정수 리터럴의 +1, -1 표기. 진법·16진 자릿수·접미사를 보존하고 음수는 만들지 않는다."""
    digits = text.rstrip("uUlL")
    suffix = text[len(digits):]
    is_hex = digits[:2] in ("0x", "0X")
    if not is_hex and len(digits) > 1 and digits[0] == "0":
        return []   # 8진 표기는 자릿수 보존이 모호하고 제품 코드에서 쓰이지 않는다
    value = literal_value(text)
    results = []
    for shifted in (value + 1, value - 1):
        if shifted < 0:
            continue
        if is_hex:
            body = format(shifted, "X" if digits[2:].isupper() else "x")
            results.append(digits[:2] + body.zfill(len(digits) - 2) + suffix)
        else:
            results.append(str(shifted) + suffix)
    return results


def make_mutant(source, operator, start, end, replacement):
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", end)
    line_end = len(source) if line_end < 0 else line_end
    original = source[line_start:line_end]
    mutated = source[line_start:start] + replacement + source[end:line_end]
    return Mutant(operator, start, end, replacement,
                  source.count("\n", 0, start) + 1,
                  original.replace("\r", ""), mutated.replace("\r", ""))


def matching(tokens, position):
    """tokens[position] 의 ( [ { 에 짝이 되는 닫는 토큰 위치."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = tokens[position].text
    depth = 0
    for index in range(position, len(tokens)):
        if tokens[index].text == opener:
            depth += 1
        elif tokens[index].text == pairs[opener]:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("닫히지 않은 괄호: {}".format(tokens[position]))


def control_headers(tokens, opening, closing):
    """본문 안 if/while/for/switch 머리의 (키워드, 여는 괄호, 닫는 괄호) 위치."""
    headers = []
    for index in range(opening + 1, closing):
        if tokens[index].text in ("if", "while", "for", "switch") and \
                tokens[index + 1].text == "(":
            headers.append((index, index + 1, matching(tokens, index + 1)))
    return headers


def statements(tokens, opening, closing):
    """본문 안 식 문장·return·break·continue 의 (첫 토큰, 세미콜론) 위치. 선언은 뺀다."""
    header_ends = {close for _, _, close in control_headers(tokens, opening, closing)}
    found = []
    index = opening + 1
    starts_statement = True
    while index < closing:
        token = tokens[index]
        if token.text == "(" and not starts_statement:
            index = matching(tokens, index)
            starts_statement = index in header_ends
            index += 1
            continue
        if token.text in ("case", "default"):
            while tokens[index].text != ":":
                index += 1
            starts_statement = True
            index += 1
            continue
        if token.text in ("{", "}", ";", "else", "do"):
            starts_statement = True
            index += 1
            continue
        if starts_statement and token.text not in ("if", "for", "while", "switch"):
            end = index
            while tokens[end].text != ";":
                end = matching(tokens, end) + 1 if tokens[end].text in "([{" else end + 1
            is_declaration = (tokens[index].kind == "id" and
                              tokens[index + 1].kind == "id" and
                              tokens[index].text not in KEYWORDS)
            if not is_declaration:
                found.append((index, end))
            index = end + 1
            starts_statement = True
            continue
        starts_statement = False
        index += 1
    return found


def subconditions(tokens, low, high):
    """[low, high) 조건 안에서 && || 로 나뉘는 하위 조건 구간을 재귀로 모은다(자신은 빼고)."""
    while tokens[low].text == "(" and matching(tokens, low) == high - 1:
        low, high = low + 1, high - 1
    operands = []
    start = low
    index = low
    while index < high:
        if tokens[index].text in "([{":
            index = matching(tokens, index) + 1
            continue
        if tokens[index].text in LOGICAL:
            operands.append((start, index))
            start = index + 1
        index += 1
    operands.append((start, high))
    if len(operands) == 1:
        return []
    found = []
    for operand in operands:
        found.append(operand)
        found.extend(subconditions(tokens, *operand))
    return found


def conditions(tokens, opening, closing):
    """if·while 조건 전체와 그 하위 조건의 토큰 구간 [low, high). for 조건은 뺀다."""
    found = []
    for keyword, left, right in control_headers(tokens, opening, closing):
        if tokens[keyword].text in ("if", "while"):
            found.append((left + 1, right))
            found.extend(subconditions(tokens, left + 1, right))
    return found


def call_arguments(tokens, opening, closing):
    """본문 안 호출(매크로 호출 포함)의 인자 하나하나. 단일 캐스트 `(t)` 를 벗긴 핵심 토큰
    위치를 내고, 핵심이 토큰 하나가 아니면 뺀다."""
    found = []
    for index in range(opening + 1, closing):
        callee = tokens[index]
        if callee.kind != "id" or callee.text in KEYWORDS or tokens[index + 1].text != "(":
            continue
        right = matching(tokens, index + 1)
        start = index + 2
        position = start
        while position <= right:
            if tokens[position].text in "([{" and position != right:
                position = matching(tokens, position) + 1
                continue
            if position == right or tokens[position].text == ",":
                low = start
                if position - low == 4 and tokens[low].text == "(" and \
                        tokens[low + 1].kind == "id" and tokens[low + 2].text == ")":
                    low += 3
                if position - low == 1:
                    found.append(low)
                start = position + 1
            position += 1
    return found


def enum_families(preprocessed):
    """전처리된 TU 텍스트에서 enum 마다 열거자 이름 튜플을 낸다."""
    tokens = tokenize(preprocessed)
    families = []
    for index, token in enumerate(tokens):
        if token.text != "enum":
            continue
        opening = index + 1
        if tokens[opening].kind == "id":
            opening += 1
        if tokens[opening].text != "{":
            continue
        closing = matching(tokens, opening)
        names = []
        expect_name = True
        position = opening + 1
        while position < closing:
            if tokens[position].text in "([{":
                position = matching(tokens, position) + 1
                continue
            if expect_name and tokens[position].kind == "id":
                names.append(tokens[position].text)
            expect_name = tokens[position].text == ","
            position += 1
        families.append(tuple(names))
    return families


def zero_macros(defines):
    """`gcc -dM -E` 출력에서 값이 정수 0 인 object-like 매크로 이름들. 인자→0 변이의
    동치 뮤턴트(`_false` → `0`)를 만들지 않으려고 쓴다."""
    names = set()
    for line in defines.splitlines():
        match = re.match(r"#define\s+(\w+)\s+(.*)$", line)
        if not match:
            continue
        value = match.group(2).replace(" ", "")
        while value.startswith("(") and value.endswith(")"):
            value = value[1:-1]
        if re.fullmatch(r"(0[xX]0+|0+)[uUlL]*", value):
            names.add(match.group(1))
    return frozenset(names)


def identified(mutants, tokens):
    """뮤턴트마다 함수 이름을 받아 원래 토큰열과 순번을 채운다. 순번은 함수 본문 단위가 아니라
    이름 단위로 센다 — `#if`/`#else` 로 같은 이름의 정의가 둘 있는 파일(`library/dfm.c`)에서도
    키가 겹치지 않는다."""
    starts = [token.start for token in tokens]
    counts = {}
    found = []
    for function, mutant in mutants:
        low = bisect.bisect_left(starts, mutant.start)
        high = bisect.bisect_left(starts, mutant.end)
        original = " ".join(token.text for token in tokens[low:high])
        head = (function, mutant.operator, original, mutant.replacement)
        counts[head] = counts.get(head, 0) + 1
        found.append(replace(mutant, function=function, original=original,
                             ordinal=counts[head]))
    return found


def generate(source, enums, zeros=frozenset()):
    tokens = tokenize(source)
    siblings = {name: family for family in enums for name in family}
    named = []
    for function, _, _, _, opening, closing in functions(tokens):
        mutants = []
        for position in call_arguments(tokens, opening, closing):
            token = tokens[position]
            if token.text in siblings:
                for other in siblings[token.text]:
                    if other != token.text:
                        mutants.append(make_mutant(source, "enum", token.start,
                                                   token.end, other))
            elif (token.kind == "id" and token.text not in zeros) or (
                    token.kind == "num" and
                                        literal_value(token.text) != 0):
                mutants.append(make_mutant(source, "argument", token.start, token.end,
                                           "0"))
        for low, high in conditions(tokens, opening, closing):
            for constant in ("1", "0"):
                mutants.append(make_mutant(source, "condition", tokens[low].start,
                                           tokens[high - 1].end, constant))
        for first, semicolon in statements(tokens, opening, closing):
            mutants.append(make_mutant(source, "statement", tokens[first].start,
                                       tokens[semicolon].end, ";"))
        for position in range(opening + 1, closing):
            token = tokens[position]
            if token.text in ARITHMETIC and ends_operand(tokens[position - 1]):
                mutants.append(make_mutant(source, "arithmetic", token.start, token.end,
                                           ARITHMETIC[token.text]))
            elif token.kind == "num":
                for text in shifted_constants(token.text):
                    mutants.append(make_mutant(source, "constant", token.start, token.end,
                                               text))
            elif token.text in COMPARISONS:
                for other in COMPARISONS:
                    if other != token.text:
                        mutants.append(make_mutant(source, "comparison",
                                                   token.start, token.end, other))
            elif token.text in LOGICAL:
                mutants.append(make_mutant(source, "logical", token.start, token.end,
                                           LOGICAL[token.text]))
        named.extend((function, mutant) for mutant in mutants)
    return identified(named, tokens)


def functions(tokens):
    """(이름, static 여부, void 반환 여부, 인자 이름들, 본문 여는 위치, 닫는 위치)."""
    found = []
    for opening, closing in function_bodies(tokens):
        right = opening - 1
        depth = 0
        left = right
        while True:
            depth += {")": 1, "(": -1}.get(tokens[left].text, 0)
            if depth == 0:
                break
            left -= 1
        name = left - 1
        start = name
        while start > 0 and tokens[start - 1].text not in (";", "}"):
            start -= 1
        prefix = [token.text for token in tokens[start:name]]
        parameters = []
        for chunk in " ".join(t.text for t in tokens[left + 1:right]).split(","):
            words = chunk.split()
            if words and words != ["void"]:
                parameters.append(words[-1])
        found.append((tokens[name].text, "static" in prefix,
                      prefix[-1:] == ["void"], tuple(parameters), opening, closing))
    return found


def kill_probe(source, name=None):
    """반드시-죽는 probe(양성 대조): 공개 void 함수 하나(기본은 첫 번째)의 본문을 비운다. 인자는
    -Wunused-parameter 로 invalid 가 되지 않게 (void) 로 소비한다."""
    tokens = tokenize(source)
    for function, is_static, is_void, parameters, opening, closing in functions(tokens):
        if (name is None and is_void and not is_static) or function == name:
            body = "".join(" (void){};".format(p) for p in parameters)
            return function, (source[:tokens[opening].start] + "{" + body + " }" +
                              source[tokens[closing].end:])
    raise ValueError("inert 로 만들 공개 void 함수가 없다: {}".format(name))


def live_kill_probes(source, regions, active):
    """kill probe 후보: 정의가 어떤 구성에서 실제로 컴파일되는 공개 void 함수를 파일 순서로. `#if !defined
    (__inline__x)` 안의 정의처럼 모든 구성에서 비활성이면 비워도 아무것도 바뀌지 않아 probe 가 죽지 못한다
    (option.c 의 `option_timer__xms`: 1 ms ISR 이 매크로 판본을 쓴다). 활성이어도 테스트가 부르지 않는 함수는
    죽지 못하므로(option.c 의 JIG 함수) 호출자는 후보를 순서대로 시도해 죽는 첫 함수를 쓴다."""
    tokens = tokenize(source)
    found = []
    for function, is_static, is_void, _, opening, _ in functions(tokens):
        if is_void and not is_static:
            line = source.count("\n", 0, tokens[opening].start) + 1
            if regions.get(line) in (None, *active):
                found.append(function)
    return found


def skipped_note(skipped):
    """앞선 kill probe 후보가 건너뛰어졌다면 보고서에 남길 문구(어떤 함수를 테스트가 부르지 않는지 드러낸다)."""
    if not skipped:
        return ""
    return " — 앞선 후보 {} 는 테스트가 죽이지 못해 건너뜀".format(", ".join(skipped))


def newline_of(source):
    return "\r\n" if "\r\n" in source else "\n"


def inserted_survive_probe(source, mutant):
    """반드시-사는 probe(음성 대조, 삽입형): 파일 끝에 `#if 0` 분기를 붙이고 그 안에 변이된 줄을 둔다.
    대상 파일에 모든 구성에서 비활성인 분기가 없을 때만 쓴다."""
    newline = newline_of(source)
    return (source + newline + "#if 0" + newline + mutant.mutated_line + newline +
            "#endif" + newline)


def mark_regions(source):
    """조건부 분기(#if/#ifdef/#ifndef/#elif/#else)마다 첫 줄에 EM_REGION_<n> 표지를 넣는다.
    반환: (표지를 넣은 텍스트, {원본 줄 번호: 가장 안쪽 분기 번호 또는 None}). 지시문 줄은 빠진다."""
    lines = source.splitlines(keepends=True)
    marked = []
    regions = {}
    stack = []
    counter = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        directive = re.match(r"\s*#\s*(\w+)", line)
        if not directive:
            regions[index + 1] = stack[-1] if stack else None
            marked.append(line)
            index += 1
            continue
        while line.rstrip("\r\n").endswith("\\") and index + 1 < len(lines):
            marked.append(line)
            index += 1
            line = lines[index]
        marked.append(line)
        index += 1
        keyword = directive.group(1)
        if keyword == "endif":
            stack.pop()
        elif keyword in ("if", "ifdef", "ifndef", "elif", "else"):
            if keyword in ("elif", "else"):
                stack.pop()
            counter += 1
            stack.append(counter)
            ending = line[len(line.rstrip("\r\n")):] or "\n"
            marked.append("EM_REGION_{}{}".format(counter, ending))
    return "".join(marked), regions


def active_regions(preprocessed):
    return {int(n) for n in re.findall(r"\bEM_REGION_(\d+)\b", preprocessed)}


def apply(source, mutant):
    return source[:mutant.start] + mutant.replacement + source[mutant.end:]


# ---------------------------------------------------------------------------------------------
# 실행: 격리 사본, 구성 선택, baseline, probe, 전수 실행
# ---------------------------------------------------------------------------------------------

ToolError = vs.ToolError   # 사본 준비 module 과 같은 클래스여야 기존 예외 처리가 같은 오류로 인식한다


@dataclass
class Result:
    mutant_id: str
    mutant: Mutant
    verdict: Verdict
    configs: tuple
    measured_by: str = "per-mutant"   # 스키마타 모드에서는 "schemata" 또는 "per-mutant: <혼합 모드 이유>"


@dataclass
class Probe:
    expected: str
    description: str
    verdict: Verdict
    configs: tuple

    @property
    def holds(self):
        return self.verdict.status == self.expected


@dataclass
class Report:
    target: str
    command: str
    head: str
    overlay: list
    target_sha256: str
    compiler: str
    configs: list
    run_timeout: float
    probes: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    inactive: list = field(default_factory=list)
    comparison: "Comparison" = None   # 실행이 유효할 때만
    functions: list = field(default_factory=list)  # 비어 있으면 파일 전체
    skipped: int = 0
    review_context: dict = field(default_factory=dict)
    scope: dict = None   # --scope-from 의 영향 범위 도출(affected_scope.to_json), 없으면 None
    method: str = "per-mutant"   # per-mutant | schemata
    timing: dict = None   # 단계별 누적 초와 벽시계(run 이 채운다)
    schemata: dict = None   # 스키마타 모드의 감싼·혼합 변이 수와 컴파일 라운드

    @property
    def valid(self):
        return bool(self.probes) and all(p.holds for p in self.probes.values())

    def count(self, status):
        return sum(1 for r in self.results if r.verdict.status == status)


# ---------------------------------------------------------------------------------------------
# 생존자 기준선 (ADR 0024): 형식 검사, 안전 경로, 대조
# ---------------------------------------------------------------------------------------------

# 생존자 상태(CONTEXT.md)와 상태별로 반드시 채울 필드. reason·ledger 는 모든 상태에 필요하다.
STATES = {"kill": (), "등가 뮤턴트": (), "조건부 등가": ("premise", "breaks_when"),
          "제외": ("review_when",), "미검토": ()}
KEY_FIELDS = ("function", "operator", "original", "replacement", "ordinal")


class BaselineError(ToolError):
    """기준선 형식 오류. 뮤턴트를 돌리기 전에 실행을 거부한다."""


def key_json(key):
    return dict(zip(KEY_FIELDS, key))


def key_tuple(raw):
    return tuple(raw[f] for f in KEY_FIELDS)


def baseline_path(target):
    """대상 제품 경로를 그대로 따른다: sensor/x.c → test/mutation_baselines/sensor/x.json."""
    return ROOT / "test" / "mutation_baselines" / Path(target).with_suffix(".json")


def filled(value):
    return isinstance(value, str) and value.strip() != ""


def parse_baseline(data, target):
    """{키 튜플: 항목} 을 파일 순서대로 낸다. 형식이 틀리면 BaselineError."""
    if not isinstance(data, dict) or data.get("target") != target:
        raise BaselineError("기준선 target 이 대상 {!r} 와 다르다".format(target))
    items = data.get("survivors", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise BaselineError("기준선 survivors 는 객체의 목록이다")
    entries = {}
    for index, item in enumerate(items, 1):
        where = "기준선 {}번째 항목".format(index)
        raw = item.get("key")
        if not isinstance(raw, dict) or set(raw) != set(KEY_FIELDS) or \
                not all(isinstance(raw[f], str) for f in KEY_FIELDS[:4]) or \
                type(raw["ordinal"]) is not int:
            raise BaselineError("{}: key 는 {} 다섯 필드이고 ordinal 만 정수다".format(
                where, ", ".join(KEY_FIELDS)))
        key = key_tuple(raw)
        if key in entries:
            raise BaselineError("{}: 키 중복 {}".format(where, key))
        state = item.get("state")
        if state not in STATES:
            raise BaselineError("{}: state {!r} 는 {} 중 하나여야 한다".format(
                where, state, ", ".join(STATES)))
        for name in ("reason", "ledger") + STATES[state]:
            if not filled(item.get(name)):
                raise BaselineError("{}: {} 상태에 {} 가 비어 있다".format(where, state, name))
        entries[key] = item
    return entries


def load_baseline(path, target):
    """기준선이 없으면 빈 기준선이다 — 모든 생존자가 새로 생김으로 보고된다."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise BaselineError("기준선 JSON 을 읽지 못했다: {}: {}".format(path, error))
    return parse_baseline(data, target)


def safety_functions(text):
    """docs/agents/host-test-project.md "안전 경로" 절의 표에서 {파일: 함수 이름 집합}."""
    table = {}
    found = re.search(r"^### 안전 경로.*?(?=^#)", text + "\n#", re.M | re.S)
    if found is None:
        raise ToolError('docs/agents/host-test-project.md 에 "### 안전 경로" 절이 없다 — 도구가 이 절의 표를 읽는다'
                        "(표가 비어 있어도 된다)")
    section = found.group()
    for match in re.finditer(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$", section, re.M):
        table[match.group(1)] = set(re.findall(r"`([^`]+)`", match.group(2)))
    return table


def unmeasurable_safety_functions(text):
    """docs/agents/host-test-project.md "측정 불가 안전 경로" 절의 표에서 {파일: 함수 이름 집합}. 절이 없으면 빈 표다."""
    found = re.search(r"^#### 측정 불가 안전 경로.*?(?=^#)", text + "\n#", re.M | re.S)
    table = {}
    if found is not None:
        for match in re.finditer(r"^\|\s*`([^`]+)`\s*\|([^|]*)\|", found.group(), re.M):
            table[match.group(1)] = set(re.findall(r"`([^`]+)`", match.group(2)))
    return table


def check_measurable(target, unmeasurable):
    """측정 불가 안전 경로 파일은 폐포 오류 대신 그 사실을 밝히고 거부한다."""
    normalized = posixpath.normpath(target)   # ./a/b.c 와 a/../a/b.c 도 같은 파일이다
    if normalized in unmeasurable:
        raise ToolError("{} 는 측정 불가 안전 경로다(docs/agents/host-test-project.md): 어떤 테스트 TU 의 폐포에도 들지 않아 "
                        "전수 변이로 잴 수 없고, 안전 경로 함수({})의 생존자 처리가 성립하지 않는다"
                        .format(normalized, ", ".join(sorted(unmeasurable[normalized]))))


def check_safety_functions(target, source, names):
    """표에 적힌 이름이 파일에 없으면 그 함수의 생존자가 조용히 안전 경로 밖으로 빠진다."""
    defined = {function for function, *_ in functions(tokenize(source))}
    missing = sorted(set(names) - defined)
    if missing:
        raise ToolError("안전 경로 표의 함수가 {} 에 없다: {}".format(target, ", ".join(missing)))


@dataclass
class Comparison:
    path: str                 # None 이면 기준선 파일이 없다
    new: list                 # (Result, 기준선 항목 또는 None) — kill 항목이 되살아난 것도 여기다
    disappeared: list         # (기준선 항목, 현재 상태: killed·invalid·비활성 분기·미생성)
    safety_unreviewed: list   # 안전 경로 함수의 생존자 중 기준선에 없거나 미검토인 것
    safety_unmeasured: list = field(default_factory=list)


def compare(results, inactive, baseline, safety, path=None, scope=None):
    """기준선 대조. kill 항목은 지금도 killed 이면 조용하고, 그 밖의 항목은 지금 생존자가 아니면
    사라짐이다. 어떤 항목도 지우지 않는다 — 고치는 것은 사람이 기준선 파일에서 한다."""
    status = {r.mutant.key: r.verdict.status for r in results}
    status.update((m.key, "비활성 분기") for m in inactive)
    survivors = [r for r in results if r.verdict.status == "survived"]

    def state(result):
        item = baseline.get(result.mutant.key)
        return item and item["state"]   # 기준선에 없으면 None

    new = [(r, baseline.get(r.mutant.key)) for r in survivors if state(r) in (None, "kill")]
    disappeared = []
    for key, item in baseline.items():
        if scope is not None and key[0] not in scope:
            continue
        current = status.get(key, "미생성")
        if item["state"] == "kill":
            gone = current not in ("killed", "survived")   # survived 는 new 에 이미 있다
        else:
            gone = current != "survived"
        if gone:
            disappeared.append((item, current))
    unreviewed = [r for r in survivors if r.mutant.function in safety and
                  state(r) in (None, "미검토")]
    return Comparison(path, new, disappeared, unreviewed,
                      sorted(safety - scope) if scope is not None else [])


def git(*args, cwd=None):
    cwd = ROOT if cwd is None else cwd
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def snapshot(root=None):
    """원본 작업 트리 상태: git status 와 제품 경로 파일 전부의 sha256."""
    root = ROOT if root is None else root
    files = [f for f in git("ls-files", "-z", "--", *PRODUCT_PATHSPEC, cwd=root).split("\0")
             if f]
    hashes = {f: hashlib.sha256((root / f).read_bytes()).hexdigest()
              for f in files if (root / f).is_file()}
    return git("status", "--porcelain=v1", "-uall", cwd=root), hashes


@contextmanager
def untouched(root=None):
    """블록 전후로 원본 작업 트리 상태(snapshot)가 같지 않으면 도구 오류다."""
    before = snapshot(root)
    try:
        yield
    finally:
        if snapshot(root) != before:
            raise ToolError("실행 전후 원본 작업 트리 상태(제품 파일 해시 또는 git status)가 다르다")


def current_snapshot(revision, target):
    """revision 사본에 현재 변경분을 반영하고 러너·target 구성까지 준비하는 요청(verification_snapshot)."""
    return vs.prepare(ROOT, revision, vs.CURRENT_CHANGES, target,
                      lambda seconds: TIMING.add("teardown", seconds))


METHODS = ("per-mutant", "schemata")


class Timing:
    """단계별 누적 초(이슈 14). 작업자 스레드가 쓴 시간의 합이라 벽시계보다 클 수 있다(작업자 N 이면 최대 N배).

    prepare 워크트리·러너·구성 준비, generate 변이 생성(전처리 포함), baseline baseline·probe 실행,
    schemata_build 스키마타 컴파일(구성 병렬, 벽시계), compile 변이별 컴파일(성공한 것),
    run 변이 실행(timeout 이 아닌 것), timeout 무한 루프 변이를 timeout 까지 기다린 시간, invalid 빌드 실패한 컴파일과 스키마타의 invalid 검증 컴파일, teardown 격리 사본 삭제.
    작업자 사본의 준비·삭제는 baseline·스키마타 컴파일과 겹쳐 돌므로 벽시계에는 그만큼 덜 든다."""
    PHASES = ("prepare", "generate", "baseline", "schemata_build", "compile", "run", "timeout",
              "invalid", "teardown")

    def __init__(self):
        self._lock = threading.Lock()
        self._local = threading.local()
        self.reset()

    def reset(self):
        with self._lock:
            self.seconds = dict.fromkeys(self.PHASES, 0.0)
            self.counts = dict.fromkeys(self.PHASES, 0)

    def add(self, phase, seconds):
        phase = getattr(self._local, "redirect", None) or phase
        with self._lock:
            self.seconds[phase] += seconds
            self.counts[phase] += 1

    @contextmanager
    def redirect(self, phase):
        """이 스레드의 compile·run·invalid 시간을 phase 로 돌린다(baseline·probe 를 따로 보려고)."""
        self._local.redirect = phase
        try:
            yield
        finally:
            self._local.redirect = None

    @contextmanager
    def span(self, phase):
        began = time.monotonic()
        try:
            yield
        finally:
            self.add(phase, time.monotonic() - began)

    def snapshot(self):
        with self._lock:
            return {"seconds": {k: round(v, 2) for k, v in self.seconds.items()},
                    "counts": dict(self.counts)}


TIMING = Timing()


def content_sha256(path):
    """줄바꿈(CRLF/LF)만 다른 같은 내용은 같은 hash 로 본다 — autocrlf 사본과 새로 쓴 LF 파일이 어긋나지 않게 한다."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def capture_review_context(worktree, configs, compiler, method="per-mutant"):
    """전처리기 의존 파일과 실제 빌드 인자·측정 방식을 묶어 이전 검토의 재사용 여부를 판별한다."""
    root = vs.normalized(worktree)
    files = {}
    for config in configs:
        for raw in config["dependencies"]:
            path = Path(vs.normalized(raw if os.path.isabs(raw) else worktree / raw))
            relative = os.path.relpath(path, root).replace("\\", "/")
            files[relative] = content_sha256(path)
    for relative in ("test/run_tests.py", "test/tools/exhaustive_mutation.py",
                     "test/tools/schemata.py", "test/tools/verification_snapshot.py",
                     "test/tools/affected_scope.py", "test/host-test.project.json",
                     "docs/agents/host-test-project.md"):
        path = worktree / relative
        if path.is_file():
            files[relative] = content_sha256(path)

    def argument(raw):
        prefix = "-I" if raw.startswith("-I") else ""
        value = raw[len(prefix):]
        if os.path.isabs(value):
            value = vs.normalized(value)
            if value == root or value.startswith(root + os.sep):
                value = "<repo>" + value[len(root):].replace("\\", "/")
        return prefix + value

    return {"files": files, "compiler": compiler, "measurement": method,
            "configs": [{"name": c["name"], "arguments": [argument(a) for a in c["base"]]}
                        for c in configs]}


def review_context_hash(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def preprocess(worktree, config, *flags):
    result = subprocess.run(config["base"] + list(flags), cwd=worktree, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ToolError("전처리 실패: {}\n{}".format(config["name"], result.stderr[-2000:]))
    return result.stdout


def compile_config(worktree, out, config, syntax_only=False):
    """구성 하나를 게이트 플래그 그대로 컴파일한다. (성공 여부, 컴파일 초). 도구·환경 오류는 ToolError.
    syntax_only 는 -fsyntax-only 로 전단(front-end) 오류·경고만 본다 — 실패하면 전체 컴파일도 실패한다."""
    began = time.monotonic()
    exe = os.path.join(out, config["exe"])
    try:
        build = subprocess.run(config["base"] + (["-fsyntax-only"] if syntax_only else ["-o", exe]),
                               cwd=worktree,
                               capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise ToolError("컴파일러가 없다: {}".format(config["base"][0]))
    except subprocess.TimeoutExpired:
        raise ToolError("컴파일 timeout(120s): {}".format(config["name"]))
    return build.returncode == 0, time.monotonic() - began


LAUNCH_ATTEMPTS = 5
LAUNCH_WAIT = 0.5


def launch(exe, **kwargs):
    """실행 파일을 띄운다. Windows 가 방금 만든 실행 파일을 검사 중이면 CreateProcess 가 잠깐 WinError 5
    (PermissionError)로 실패한다. 한 번 실패로 몇십 분 측정을 버리지 않도록 다시 시도하고, 끝내 안 되면
    오류를 그대로 낸다. timeout 은 재시도하지 않는다."""
    for attempt in range(LAUNCH_ATTEMPTS):
        try:
            return subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        except PermissionError:
            if attempt == LAUNCH_ATTEMPTS - 1:
                raise
            time.sleep(LAUNCH_WAIT * (attempt + 1))


def run_config(worktree, out, config, run_timeout):
    """(결과, 실행 시간, 컴파일+실행 전체 시간)."""
    began = time.monotonic()
    exe = os.path.join(out, config["exe"])
    built, compile_seconds = compile_config(worktree, out, config)
    TIMING.add("compile" if built else "invalid", compile_seconds)
    if not built:
        return ConfigResult(config["name"], "build_fail"), 0.0, time.monotonic() - began
    started = time.monotonic()
    try:
        run = launch(exe, cwd=worktree, timeout=run_timeout)
    except subprocess.TimeoutExpired:
        TIMING.add("timeout", run_timeout)
        return ConfigResult(config["name"], "timeout"), run_timeout, time.monotonic() - began
    elapsed = time.monotonic() - started
    TIMING.add("run", elapsed)
    return (ConfigResult(config["name"], "pass" if run.returncode == 0 else "run_fail"),
            elapsed, time.monotonic() - began)


class Journal:
    """판정이 끝난 변이를 한 줄씩 바로 디스크에 남겨 강제 종료·예외 뒤에 이어 가게 한다(이슈 19).

    첫 줄은 입력 fingerprint 다. 같은 fingerprint 의 파일만 이어 쓰고, 다르면 기록을 버리고 새로 시작한다.
    줄은 쓰는 도중 끊길 수 있어 읽을 때 해석되지 않는 줄은 무시한다. 작업자 여럿이 쓰므로 잠근다."""

    def __init__(self, path, fingerprint):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.done = {}
        lines = []
        if self.path.is_file():
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        if lines and self._parse(lines[0]).get("fingerprint") == fingerprint:
            for line in lines[1:]:
                entry = self._parse(line)
                if isinstance(entry.get("i"), int) and "key" in entry:
                    self.done[entry["i"]] = entry
        header = json.dumps({"fingerprint": fingerprint})
        body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.done.values())
        # 잘린 줄을 걷어낸 깨끗한 파일로 바꾼다. 교체가 중간에 끊겨도 이전 파일이 남는다
        staging = self.path.with_name(self.path.name + ".tmp")
        staging.write_text(header + "\n" + body, encoding="utf-8", newline="\n")
        os.replace(staging, self.path)

    @staticmethod
    def _parse(line):
        try:
            value = json.loads(line)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def restore(self, index, mutant):
        """기록된 판정(같은 번호·같은 키일 때만). 없으면 None."""
        entry = self.done.get(index)
        if entry is None or entry["key"] != key_json(mutant.key):
            return None
        configs = tuple(ConfigResult(name, outcome) for name, outcome in entry["configs"])
        verdict = Verdict(entry["status"], tuple(entry["killed_by"]), tuple(entry["invalid_in"]))
        return Result("M{:03d}".format(index), mutant, verdict, configs, entry["measured_by"])

    def record(self, index, result):
        entry = {"i": index, "key": key_json(result.mutant.key), "status": result.verdict.status,
                 "killed_by": list(result.verdict.killed_by),
                 "invalid_in": list(result.verdict.invalid_in),
                 "configs": [[c.config, c.outcome] for c in result.configs],
                 "measured_by": result.measured_by}
        with self.lock, self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def discard(self):
        self.path.unlink(missing_ok=True)


class Bench:
    """격리 사본 위의 대상 파일에 텍스트를 쓰고 구성을 순서대로 돌린 뒤 원문으로 되돌린다.

    실행 실패·timeout 이 나오면 killed 가 확정되므로 남은 구성은 돌리지 않는다. 생존·invalid 는
    다른 구성이 아직 죽일 수 있어 전부 돌린다. 병렬성은 구성 안이 아니라 작업자(Bench) 사이에 둔다.

    screen 에 컴파일러 플래그(예: ["-O0"])를 주면 먼저 그 플래그를 뒤에 붙여 한 번 돌려 killed 만
    확정한다. 선별에서 killed 가 아닌 변이(빌드 실패 포함)는 원래 플래그로 다시 돌려 판정한다 —
    선별의 빌드 실패는 -O 수준에 따라 경고가 달라질 수 있어 믿지 않는다."""

    def __init__(self, worktree, out, target, configs, screen=None):
        self.worktree = worktree
        self.out = out
        self.path = worktree / target
        self.original = self.path.read_bytes()
        self.configs = list(configs)
        self.run_timeout = 120.0
        self.costs = {}
        self.screen = list(screen or [])

    def prioritize(self, costs):
        """구성 이름별 비용(초)이 작은 것부터 돌린다. 동률은 기존 순서를 지킨다."""
        self.configs.sort(key=lambda c: costs[c["name"]])

    def run_all(self, configs, precheck=False):
        outcomes = []
        for config in configs:
            if precheck:
                # 전단 오류·경고로 실패하면 전체 컴파일도 실패하므로 invalid 가 확정이다 — 0.16s 로 2s 를 아낀다
                passed, seconds = compile_config(self.worktree, self.out, config, syntax_only=True)
                TIMING.add("invalid", seconds)
                if not passed:
                    outcomes.append((ConfigResult(config["name"], "build_fail"), 0.0, seconds))
                    continue
            outcome = run_config(self.worktree, self.out, config, self.run_timeout)
            outcomes.append(outcome)
            if outcome[0].outcome in ("run_fail", "timeout"):
                break
        return outcomes

    @contextmanager
    def mutated(self, text):
        """블록 동안 대상 파일을 text 로 바꾸고 끝나면 원문으로 되돌린다(None 이면 그대로 둔다)."""
        if text is None:
            yield
            return
        self.path.write_bytes(text.encode("utf-8"))
        try:
            yield
        finally:
            self.path.write_bytes(self.original)

    def compile_check(self, config, syntax_only=False):
        """지금 대상 파일 내용이 config 에서 컴파일되는가(실행하지 않는다). 스키마타 모드가 invalid 를
        가릴 때 쓴다 — 시간은 invalid 단계에 센다."""
        built, seconds = compile_config(self.worktree, self.out, config, syntax_only)
        TIMING.add("invalid", seconds)
        return built

    def run(self, text, precheck=False):
        """text 를 구성마다 컴파일·실행해 (판정, 구성별 결과, 최장 실행 초). precheck 는 구성마다 먼저
        -fsyntax-only 로 거른다(스키마타 모드가 변이별로 컴파일하는 혼합 모드 변이에 쓴다)."""
        self.path.write_bytes(text.encode("utf-8"))
        try:
            outcomes, full = [], True
            if self.screen:
                outcomes = self.run_all([dict(c, base=c["base"] + self.screen)
                                         for c in self.configs])
                full = not any(r.outcome in ("run_fail", "timeout") for r, _, _ in outcomes)
            if full:
                outcomes = self.run_all(self.configs, precheck)
        finally:
            self.path.write_bytes(self.original)
        if full:
            self.costs = {r.config: total for r, _, total in outcomes}
        results = tuple(result for result, _, _ in outcomes)
        return classify(results), results, max(elapsed for _, elapsed, _ in outcomes)


def run(target, kill_probe_name=None, progress=None, baseline=None, workers=1, functions=None,
        screen=None, scope_from=None, schemata=None, journal=None):
    """baseline: 기준선 경로(기본 baseline_path(target)). 형식 오류는 뮤턴트를 돌리기 전에 거부한다.

    scope_from: 커밋. 작업 트리의 변경에서 영향 범위를 도출해(affected_scope) 도출 집합 ∪ 안전 경로 ∪
    functions 를 측정한다. 폴백이면 파일 전체다.

    schemata: None 이면 변이별 컴파일(per-mutant), 아니면 변이 스키마타이고 값은 invalid 판정 방식이다
    (schemata.judge 참조): "syntax", "survivors", "all"(대조용, 느리다). CLI 의 기본(스키마타 syntax)은
    parse_args 가 정하고, 이 API 의 기본은 변이별 컴파일 그대로다(직접 부르는 스크립트와 기존 테스트 유지).

    journal: 판정 기록 파일 경로(Journal). 판정이 끝난 변이를 바로 남기고, 같은 입력의 기록이 있으면 그
    변이는 다시 돌리지 않는다. 지우는 것은 부른 쪽의 몫이다(보고서를 쓴 뒤 — main 참조)."""
    target = target.replace("\\", "/")
    if type(workers) is not int or workers < 1:
        raise ToolError("workers 는 1 이상의 정수여야 한다")
    if schemata not in (None, "syntax", "survivors", "all"):
        raise ToolError("schemata 는 syntax, survivors, all 중 하나다: {!r}".format(schemata))
    if schemata and screen:
        raise ToolError("--schemata 는 1차 선별(--screen-opt)과 함께 쓰지 않는다")
    TIMING.reset()
    started = time.monotonic()
    progress = progress or (lambda message: None)
    path = Path(baseline) if baseline else baseline_path(target)
    entries = load_baseline(path, target)
    agents = (ROOT / "docs" / "agents" / "host-test-project.md").read_text(encoding="utf-8")
    check_measurable(target, unmeasurable_safety_functions(agents))
    safety = safety_functions(agents)
    try:
        label = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        label = str(path)
    context = (label if path.is_file() else None, entries, safety.get(target, set()))
    with untouched():
        scope = None
        if scope_from:
            import affected_scope   # 순환을 피한다: affected_scope 가 이 모듈을 가져온다
            others = [p for p in git("diff", "--name-only", scope_from, "--", *PRODUCT_PATHSPEC).split("\n")
                      if p and p != target]
            if others:
                # 이 diff 가 완료 게이트·pre-commit 의 제품 diff 해시가 된다. 커밋하지 않을 변경이 섞이면 해시가 어긋난다
                progress("주의: 대상 밖 제품 파일 변경 {}개가 diff 에 든다 ({}) — 커밋에 넣지 않을 변경이면 되돌린 뒤 "
                         "다시 잰다".format(len(others), ", ".join(others[:5])))
            derivation = affected_scope.derive(ROOT, target, scope_from, progress)
            scope = affected_scope.to_json(derivation)
            if derivation.fallback:
                functions = None
            else:
                functions = sorted(set(derivation.derived) | safety.get(target, set()) |
                                   set(functions or []))
                if not functions:
                    raise ToolError("영향 범위와 안전 경로가 모두 비었다: base {} 대비 {} 에 측정할 "
                                    "함수가 없다".format(derivation.base[:12], target))
        head = git("rev-parse", "HEAD").strip()
        with ExitStack() as held, tempfile.TemporaryDirectory(prefix="plr-exhaustive-mut-") as out:
            with TIMING.span("prepare"):
                prepared = held.enter_context(current_snapshot("HEAD", target))
            report = measure(prepared, out, target, head, kill_probe_name, progress, context,
                             workers, functions, screen, schemata, journal)
        report.scope = scope
    report.timing = {"wall": round(time.monotonic() - started, 2), **TIMING.snapshot()}
    return report


def measure(prepared_copy, out, target, head, kill_probe_name, progress, context,
            workers=1, selected_functions=None, screen=None, schemata=None, journal_path=None):
    label, entries, safety = context
    method = "schemata" if schemata else "per-mutant"
    prepared = time.monotonic()
    worktree, runner, configs, copied = (prepared_copy.path, prepared_copy.runner,
                                         prepared_copy.configs, prepared_copy.copied)
    path = worktree / target
    source = path.read_bytes().decode("utf-8")
    check_safety_functions(target, source, safety)
    scope = set(selected_functions) if selected_functions else None
    defined = {name for name, *_ in functions(tokenize(source))}
    missing = sorted(scope - defined) if scope is not None else []
    if missing:
        raise ToolError("지정한 함수가 {} 에 없다: {}".format(target, ", ".join(missing)))
    compiler = subprocess.run([runner.CC, "--version"], capture_output=True,
                              text=True).stdout.splitlines()[0]
    review_context = capture_review_context(worktree, configs, compiler, method)
    progress("구성 {}개: {}".format(len(configs), ", ".join(c["name"] for c in configs)))
    TIMING.add("prepare", time.monotonic() - prepared)

    generating = time.monotonic()
    families, zeros, defines = [], None, []
    for config in configs:
        for family in enum_families(preprocess(worktree, config, "-E")):
            if family not in families:
                families.append(family)
        defines.append(preprocess(worktree, config, "-dM", "-E"))
        found = zero_macros(defines[-1])
        zeros = found if zeros is None else zeros & found
    marked, regions = mark_regions(source)
    path.write_bytes(marked.encode("utf-8"))
    try:
        active = set().union(*(active_regions(preprocess(worktree, c, "-E")) for c in configs))
    finally:
        path.write_bytes(source.encode("utf-8"))

    # 지정이 없으면 활성 정의를 가진 공개 void 함수 전부가 후보다(없으면 [None] 으로 기존 오류를 낸다).
    probe_names = [kill_probe_name] if kill_probe_name else (
        live_kill_probes(source, regions, active) or [None])
    mutants = generate(source, families, zeros)
    inactive = [m for m in mutants if regions.get(m.line) not in (None, *active)]
    live = [m for m in mutants if m not in inactive]
    selected = [(index, m) for index, m in enumerate(live, 1)
                if scope is None or m.function in scope]
    TIMING.add("generate", time.monotonic() - generating)

    report = Report(target=target, command=" ".join(sys.argv), head=head, overlay=copied,
                    target_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    compiler=compiler, configs=[c["name"] for c in configs],
                    run_timeout=0.0,
                    inactive=[m for m in inactive if scope is None or m.function in scope],
                    functions=sorted(scope) if scope is not None else [],
                    skipped=sum(1 for m in mutants if scope is not None and m.function not in scope))
    report.review_context = review_context
    report.method = method
    bench = Bench(worktree, out, target, configs, screen)
    count = min(workers, len(selected)) - 1

    def prepare_worker(_):
        """작업자 격리 사본 하나(사본·구성은 작업자 몫이다). baseline 이 끝나기 전에 시작한다."""
        local = ExitStack()
        try:
            with TIMING.span("prepare"):
                worker = local.enter_context(current_snapshot(head, target))
                worker_out = local.enter_context(tempfile.TemporaryDirectory(
                    prefix="plr-exhaustive-mut-"))
                return local, Bench(worker.path, worker_out, target, worker.configs, screen)
        except BaseException:
            local.close()
            raise

    def close_workers(futures):
        """만든 작업자 사본을 동시에 지운다."""
        stacks = []
        for future in futures:
            try:
                stacks.append(future.result()[0])
            except BaseException:
                pass   # 준비 실패는 결과를 거두는 쪽이 던진다
        with ThreadPoolExecutor(max_workers=max(1, len(stacks))) as closer:
            list(closer.map(ExitStack.close, stacks))

    with ExitStack() as stack:
        # 등록 순서의 역순으로 닫힌다: 준비 풀이 끝나기를 기다린 뒤(shutdown) 만든 사본을 지운다
        preparing = []
        stack.callback(close_workers, preparing)
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=max(1, count)))
        preparing.extend(pool.submit(prepare_worker, number) for number in range(count))
        with TIMING.redirect("baseline"):
            verdict, results, elapsed = bench.run(source)
        if verdict.status != "survived":
            raise ToolError("baseline 실패: {}".format(
                ", ".join("{}={}".format(r.config, r.outcome) for r in results)))
        bench.run_timeout = report.run_timeout = max(10.0, round(10 * elapsed, 1))
        bench.prioritize(bench.costs)

        built = None
        if schemata:
            import schemata as schemata_tool   # 순환을 피한다: schemata 가 이 모듈을 가져온다
            with TIMING.redirect("baseline"):
                lvalues, macros = {}, set()
                for text in defines:
                    macros |= set(schemata_tool.macro_table(text))
                    for name, positions in schemata_tool.lvalue_parameters(text).items():
                        lvalues.setdefault(name, set()).update(positions)
                skipped_probes, build_seconds = [], 0.0
                for probe_name in probe_names:
                    built = schemata_tool.build(worktree, out, target, bench.configs, source,
                                                selected, probe_name, progress, lvalues, macros)
                    baseline_run = schemata_tool.judge(bench, built, None)
                    build_seconds += built.seconds
                    if baseline_run[0].status != "survived":
                        raise ToolError("스키마타 baseline 실패: {}".format(", ".join(
                            "{}={}".format(r.config, r.outcome) for r in baseline_run[1])))
                    kill_verdict, kill_results = schemata_tool.judge(
                        bench, built, schemata_tool.KILL_PROBE_ID)
                    if kill_verdict.status == "killed" or probe_name == probe_names[-1]:
                        break
                    skipped_probes.append(probe_name)
                    progress("kill probe {} 를 테스트가 죽이지 못했다 — 다음 후보".format(probe_name))
            TIMING.add("schemata_build", build_seconds)   # redirect 밖에서 더한다(redirect 안이면 phase 로 안 잡힌다)
            reasons = {}
            for _, reason in built.mixed():
                reasons[reason] = reasons.get(reason, 0) + 1
            report.schemata = {"validate": schemata, "wrapped": len(built.wrapped()),
                               "mixed": sum(reasons.values()), "mixed_reasons": reasons,
                               "compile_rounds": built.rounds,
                               "build_seconds": round(built.seconds, 2)}
            progress("스키마타: 감싼 변이 {}개, 혼합 모드 {}개 {}".format(
                report.schemata["wrapped"], report.schemata["mixed"], reasons))
            with TIMING.redirect("baseline"):
                report.probes["kill"] = Probe(
                    "killed", "공개 함수 {} inert (스키마타 번호 {}){}".format(
                        built.probe, schemata_tool.KILL_PROBE_ID, skipped_note(skipped_probes)),
                    kill_verdict, kill_results)
                verdict, results = schemata_tool.judge(bench, built, schemata_tool.SURVIVE_PROBE_ID)
                report.probes["survive"] = Probe(
                    "survived", "스키마타에 없는 변이 번호 {} — 켜지는 분기가 없다(비활성 분기 안의 "
                    "변이와 같다)".format(schemata_tool.SURVIVE_PROBE_ID), verdict, results)
        else:
            with TIMING.redirect("baseline"):
                skipped_probes = []
                for probe_name in probe_names:
                    name, text = kill_probe(source, probe_name)
                    verdict, results, _ = bench.run(text)
                    if verdict.status == "killed" or probe_name == probe_names[-1]:
                        break
                    skipped_probes.append(name)
                    progress("kill probe {} 를 테스트가 죽이지 못했다 — 다음 후보".format(name))
                report.probes["kill"] = Probe(
                    "killed", "공개 함수 {} inert{}".format(name, skipped_note(skipped_probes)),
                    verdict, results)
                if inactive:
                    probe, description = apply(source, inactive[0]), "비활성 분기 안의 변이"
                    shown = inactive[0]
                elif live:
                    probe = inserted_survive_probe(source, live[0])
                    description, shown = "사본 끝에 삽입한 #if 0 분기 안의 변이", live[0]
                else:
                    shown = make_mutant(source, "probe", len(source), len(source), "")
                    probe = source + "\n#if 0\nint em_survive_probe = 1;\n#endif\n"
                    description = "사본 끝에 삽입한 #if 0 분기 안의 변이"
                verdict, results, _ = bench.run(probe)
                report.probes["survive"] = Probe(
                    "survived", "{} ({}:{} {})".format(description, target, shown.line,
                                                       shown.mutated_line.strip()),
                    verdict, results)
        progress("probe: " + ", ".join("{}={}".format(k, p.verdict.status)
                                      for k, p in report.probes.items()))
        if not report.valid:
            return report

        benches = [bench]
        for future in preparing:
            _, worker_bench = future.result()
            worker_bench.run_timeout = bench.run_timeout
            worker_bench.prioritize({c["name"]: i for i, c in enumerate(bench.configs)})
            benches.append(worker_bench)

        journal = None
        if journal_path:
            # 판정에 영향 주는 입력만 묶는다. --workers 는 판정이 같으므로 넣지 않는다
            journal = Journal(journal_path, review_context_hash({
                "context": review_context, "schemata": schemata, "screen": screen,
                "target_sha256": report.target_sha256,
                "mutants": [[index, key_json(m.key)] for index, m in selected]}))
            if journal.done:
                progress("이어 가기: 기록에서 판정 {}개를 복원한다 ({})".format(
                    len(journal.done), journal.path))

        def run_batch(item):
            worker_bench, batch = item
            found = []
            for index, mutant in batch:
                if vs.stopping.is_set():
                    break
                restored = journal.restore(index, mutant) if journal else None
                if restored:
                    found.append((index, restored))
                    continue
                measured_by = "per-mutant"
                if built is not None and not built.sites[index].mixed:
                    measured_by = "schemata"
                    verdict, results = schemata_tool.judge(
                        worker_bench, built, index, lambda: apply(source, mutant), schemata)
                else:
                    if built is not None:
                        measured_by = "per-mutant: " + built.sites[index].mixed
                    verdict, results, _ = worker_bench.run(apply(source, mutant),
                                                           precheck=built is not None)
                result = Result("M{:03d}".format(index), mutant, verdict, results, measured_by)
                if journal:
                    journal.record(index, result)
                found.append((index, result))
                progress("[M{:03d}/{}] {}:{} {} -> {}".format(
                    index, len(live), target, mutant.line, mutant.operator, verdict.status))
            return found

        # 작업자마다 한 batch만 맡겨 같은 제품 파일·출력 파일의 동시 쓰기를 막는다.
        with ThreadPoolExecutor(max_workers=len(benches)) as runners:
            batches = runners.map(run_batch, [(b, selected[i::len(benches)])
                                          for i, b in enumerate(benches)])
            ordered = sorted((item for batch in batches for item in batch), key=lambda x: x[0])
            report.results = [result for _, result in ordered]
        report.comparison = compare(report.results, report.inactive, entries, safety, label, scope)
        return report


# ---------------------------------------------------------------------------------------------
# 보고
# ---------------------------------------------------------------------------------------------

def format_report(report):
    lines = ["# 전수 변이: {}".format(report.target), ""]
    lines.append("- 실행: `{}`".format(report.command))
    lines.append("- HEAD `{}`, 덮은 작업 트리 변경분: {}".format(
        report.head, ", ".join("`{}`".format(p) for p in report.overlay) or "없음"))
    lines.append("- 대상 sha256 `{}`".format(report.target_sha256))
    lines.append("- 컴파일러: {}".format(report.compiler))
    lines.append("- 구성: {}".format(", ".join("`{}`".format(c) for c in report.configs)))
    lines.append("- 함수 범위: {}; 범위 밖 건너뜀 {}개".format(
        ", ".join("`{}`".format(name) for name in report.functions) or "파일 전체", report.skipped))
    if report.scope:
        lines.append("- 영향 범위: base `{}`, 시작점 {}개, 도출 {}개, 폴백 {}, 간접·파일 밖 {}건".format(
            report.scope["base"][:12], len(report.scope["starts"]), len(report.scope["derived"]),
            report.scope["fallback"] or "없음", len(report.scope["indirect"])))
    lines.append("- 측정 방식: {}".format(format_method(report)))
    if report.timing:
        lines.append("- 시간: 벽시계 {}s; 단계별 누적 초(작업자 합) {}".format(
            report.timing["wall"], ", ".join("{} {}".format(k, v) for k, v in
                                             report.timing["seconds"].items())))
    lines.append("- 실행 timeout: {}s (baseline 최장 실행의 10배, 최소 10s)".format(
        report.run_timeout))
    for kind, probe in report.probes.items():
        lines.append("- probe {}: {} — 기대 {}, 결과 {} ({})".format(
            kind, probe.description, probe.expected, probe.verdict.status,
            "성립" if probe.holds else "불성립"))
    if not report.valid:
        lines += ["", "**실행 무효** — 자기 민감도 probe 가 기대와 다르다. 결과를 믿지 않는다."]
        return "\n".join(lines) + "\n"
    lines.append("- 뮤턴트 {}개: killed {}, survived {}, invalid {}; 비활성 분기(집계 제외) {}".format(
        len(report.results), report.count("killed"), report.count("survived"),
        report.count("invalid"), len(report.inactive)))
    lines += ["", "## 생존자", "", "| ID | 위치 | 연산자 | 원문 | 변이문 |",
              "|---|---|---|---|---|"]
    for r in report.results:
        if r.verdict.status == "survived":
            lines.append("| {} | `{}:{}` | {} | `{}` | `{}` |".format(
                r.mutant_id, report.target, r.mutant.line, r.mutant.operator,
                r.mutant.original_line.strip().replace("\n", " "),
                r.mutant.mutated_line.strip().replace("\n", " ")))
    return "\n".join(lines + format_comparison(report.comparison)) + "\n"


def format_method(report):
    if report.schemata is None:
        return report.method
    info = report.schemata
    return "{} (invalid 검증 {}; 감싼 변이 {}개, 혼합 모드 {}개{}; 스키마타 컴파일 {}s, 오류 변이 걷어내기 {}회)".format(
        report.method, info["validate"], info["wrapped"], info["mixed"],
        "".join(" — {} {}".format(reason, count)
                for reason, count in sorted(info["mixed_reasons"].items())),
        info["build_seconds"], info["compile_rounds"])


def key_text(key):
    function, operator, original, replacement, ordinal = key
    return "`{}` {} `{}` → `{}` #{}".format(function, operator, original, replacement, ordinal)


DISAPPEARED_LIST_MAX = 10   # 이보다 많으면 항목마다 쓰지 않고 (함수, 기준선 상태, 지금 상태)별 개수로 접는다


def format_disappeared(disappeared):
    """사라진 항목 줄. 많으면 접는다 — 키 목록은 --json 의 baseline.disappeared 다."""
    if len(disappeared) <= DISAPPEARED_LIST_MAX:
        return ["{} — 기준선 {}, 지금 {}".format(key_text(key_tuple(item["key"])), item["state"], status)
                for item, status in disappeared]
    counts = {}
    for item, status in disappeared:
        group = (item["key"]["function"], item["state"], status)
        counts[group] = counts.get(group, 0) + 1
    lines = ["`{}` 기준선 {}, 지금 {}: {}건".format(function, state, status, count)
             for (function, state, status), count in sorted(counts.items())]
    return lines + ["키 목록은 `--json` 결과의 `baseline.disappeared` 에 있다({}건)".format(len(disappeared))]


def format_comparison(comparison):
    lines = ["", "## 기준선 대조", ""]
    if comparison.path is None:
        lines.append("- 기준선 없음 — 모든 생존자가 새로 생김이다.")
    else:
        lines.append("- 기준선 `{}`".format(comparison.path))
    lines.append("- 새로 생김 {}, 사라짐 {}, 안전 경로 미검토 {}".format(
        len(comparison.new), len(comparison.disappeared), len(comparison.safety_unreviewed)))
    if comparison.safety_unmeasured:
        lines.append("- 이번 실행에서 보지 않은 안전 경로: " + ", ".join(
            "`{}`".format(name) for name in comparison.safety_unmeasured))
    sections = [
        ("새로 생긴 생존자 — 상태를 정해 기준선에 올린다",
         ["{} 줄 {}: {}{}".format(r.mutant_id, r.mutant.line, key_text(r.mutant.key),
                                  " — 기준선은 kill: 잡던 테스트가 더 이상 잡지 않는다"
                                  if item else "")
          for r, item in comparison.new]),
        ("사라진 항목 — 기준선에서 지우거나 고친다",
         format_disappeared(comparison.disappeared)),
        ("안전 경로의 미검토 생존자 — 바로 처리한다 (ADR 0024)",
         ["{} 줄 {}: {}".format(r.mutant_id, r.mutant.line, key_text(r.mutant.key))
          for r in comparison.safety_unreviewed]),
    ]
    for title, items in sections:
        if items:
            lines += ["", "### " + title, ""] + ["- " + item for item in items]
    return lines


def to_json(report):
    def verdict(v):
        return {"status": v.status, "killed_by": list(v.killed_by),
                "invalid_in": list(v.invalid_in)}
    return {
        "target": report.target, "command": report.command, "head": report.head,
        "overlay": report.overlay, "target_sha256": report.target_sha256,
        "compiler": report.compiler, "configs": report.configs,
        "run_timeout": report.run_timeout, "valid": report.valid,
        "functions": report.functions, "skipped": report.skipped, "scope": report.scope,
        "method": report.method, "timing": report.timing, "schemata": report.schemata,
        "review_context": report.review_context,
        "review_context_sha256": review_context_hash(report.review_context),
        "probes": {k: {"expected": p.expected, "description": p.description,
                       "verdict": verdict(p.verdict)} for k, p in report.probes.items()},
        "inactive": [{"line": m.line, "operator": m.operator, "mutated": m.mutated_line}
                     for m in report.inactive],
        "results": [{"id": r.mutant_id, "key": key_json(r.mutant.key),
                     "line": r.mutant.line, "operator": r.mutant.operator,
                     "original": r.mutant.original_line, "mutated": r.mutant.mutated_line,
                     "measured_by": r.measured_by,
                     **verdict(r.verdict)} for r in report.results],
        "baseline": comparison_json(report.comparison),
    }


def comparison_json(comparison):
    if comparison is None:
        return None

    def survivor(result):
        return {"id": result.mutant_id, "key": key_json(result.mutant.key),
                "line": result.mutant.line, "mutated": result.mutant.mutated_line}
    return {
        "path": comparison.path,
        "new": [{**survivor(r), "baseline_state": item and item["state"]}
                for r, item in comparison.new],
        "disappeared": [{"key": item["key"], "state": item["state"], "status": status}
                        for item, status in comparison.disappeared],
        "safety_unreviewed": [survivor(r) for r in comparison.safety_unreviewed],
        "safety_unmeasured": comparison.safety_unmeasured,
    }


PROGRESS_EVERY = 100


def parse_args(argv=None):
    """기본은 스키마타 syntax 이고, 변이별 컴파일은 --per-mutant 로만 켠다(ADR 0029).
    반환값의 `schemata` 는 run() 에 그대로 넘기는 값이다(None 이면 변이별 컴파일)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("target", help="저장소 루트 기준 제품 .c 경로")
    parser.add_argument("--kill-probe", help="inert 로 만들 공개 함수(기본: 첫 공개 void 함수)")
    parser.add_argument("--json", help="전체 결과를 쓸 JSON 경로")
    parser.add_argument("--baseline",
                        help="생존자 기준선 경로(기본: test/mutation_baselines/<대상>.json)")
    parser.add_argument("--workers", type=int, default=1, help="격리 작업자 수(기본: 1)")
    parser.add_argument("--function", action="append", dest="functions",
                        help="측정할 함수 이름(여러 번 지정 가능, 기본: 파일 전체)")
    parser.add_argument("--scope-from", metavar="COMMIT",
                        help="작업 트리를 이 커밋과 비교해 영향 범위(도출 집합 ∪ 안전 경로)를 측정한다."
                             " 커밋 전 HEAD, 커밋 후 HEAD~1. 폴백이면 파일 전체. --function 은 더하기만 한다")
    parser.add_argument("--per-mutant", action="store_true",
                        help="변이마다 TU 를 다시 컴파일해 측정한다(기본은 스키마타 syntax). 스키마타와"
                             " 같은 id 별 판정이 필요한 대조나 --screen-opt 에 쓴다")
    parser.add_argument("--screen-opt", dest="screen", metavar="FLAG",
                        help="변이별 컴파일 전용(--per-mutant 필요). 1차 선별 컴파일 플래그(예: -O0)로"
                             " killed 만 확정하고 나머지는 원래 플래그로 다시 판정한다. 스키마타는 구성마다"
                             " 한 번만 컴파일하므로 선별이 겹쳐 함께 못 쓴다")
    parser.add_argument("--schemata", nargs="?", const="syntax", metavar="MODE",
                        choices=("syntax", "survivors", "all"),
                        help="스키마타의 invalid 판정 방식(기본 측정 방식, 이슈 14·17): 구성마다 한 번만"
                             " 컴파일하고 변이 번호를 환경변수로 켠다. syntax(기본: 모든 변이를 변이별"
                             " -fsyntax-only 로 검증하고 살아남은 변이는 변이별 전체 컴파일), survivors(살아남은"
                             " 변이만 변이별 전체 컴파일), all(모든 변이를 변이별 전체 컴파일, 매일 대조용)."
                             " --per-mutant 와 함께 못 쓴다")
    parser.add_argument("--verbose", action="store_true",
                        help="변이별 진행 줄을 모두 낸다(기본: %d번째와 마지막만)" % PROGRESS_EVERY)
    args = parser.parse_args(argv)
    if args.per_mutant and args.schemata:
        parser.error("--per-mutant 와 --schemata 는 함께 쓰지 않는다")
    if args.screen and not args.per_mutant:
        parser.error("--screen-opt 은 변이별 컴파일 전용이다: --per-mutant 를 함께 준다")
    if args.per_mutant:
        args.schemata = None
    elif not args.schemata:
        args.schemata = "syntax"
    return args


def make_progress_printer(verbose, write=None):
    """변이별 한 줄(`[M012/3273] ...`)은 기본으로 PROGRESS_EVERY 번째와 마지막만 낸다. 나머지 진행 줄은 그대로 낸다."""
    write = write or (lambda message: print(message, file=sys.stderr, flush=True))
    pattern = re.compile(r"^\[M(\d+)/(\d+)\]")

    def progress(message):
        match = pattern.match(message)
        if verbose or not match:
            write(message)
            return
        index, total = int(match.group(1)), int(match.group(2))
        if index % PROGRESS_EVERY == 0 or index == total:
            write(message)

    return progress


def main():
    args = parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # --json 옆의 기록 파일: 같은 명령을 다시 실행하면 이어 가고, 보고서를 쓰고 나면 지운다(이슈 19)
    journal = args.json + ".progress" if args.json else None
    try:
        report = run(args.target, args.kill_probe,
                     make_progress_printer(args.verbose), args.baseline,
                     args.workers, args.functions, [args.screen] if args.screen else None,
                     args.scope_from, args.schemata, journal)
    except ToolError as error:
        print("도구 오류 — 실행 무효: {}".format(error), file=sys.stderr)
        return 1
    if args.json:
        Path(args.json).write_text(json.dumps(to_json(report), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        if journal:
            Path(journal).unlink(missing_ok=True)
    sys.stdout.write(format_report(report))
    return 0 if report.valid else 2


if __name__ == "__main__":
    # affected_scope 가 `import exhaustive_mutation` 으로 같은 모듈(같은 ToolError)을 얻게 한다
    sys.modules.setdefault("exhaustive_mutation", sys.modules["__main__"])
    sys.exit(main())
