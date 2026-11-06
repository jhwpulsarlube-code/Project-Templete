#!/usr/bin/env python3
"""변경 diff 에서 변이 측정의 영향 범위(CONTEXT.md)를 도출한다. 규칙은 docs/agents/mutation-tool-reference.md "범위 선택".

    python -B test/tools/affected_scope.py application/lubx.c --base HEAD [--json 경로]

작업 트리를 `--base` 커밋과 비교해 시작점 함수(변경 줄이 본문에 걸리는 함수)에서 호출자·공용 상태·ISR 공유
상태로 넓힌 집합과, 사람이 판단해야 하는 항목(간접 참조·파일 밖 읽기)을 낸다. 함수 경계와 이름은 원본 토큰,
호출과 변수 참조는 호스트 러너 구성으로 전처리한 결과에서 뽑는다. 정적 분석이라 못 잡는 경로는
docs/agents/mutation-tool-reference.md 에 한계로 적혀 있다.
"""
from dataclasses import dataclass, field
from pathlib import Path
import argparse
import bisect
import hashlib
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import exhaustive_mutation as em  # noqa: E402
import verification_snapshot as vs  # noqa: E402

# 시작점의 호출자를 몇 단계까지 넣는가. 보정 측정(.scratch/lubx-host-test-harness/issues/13)으로 정했고,
# 바꾸면 근거를 docs/agents/mutation-tool-reference.md 에 남긴다. 피호출자는 넣지 않는다(시작점이 바뀌어도 호출자의 동작이 아니다).
CALLER_DEPTH = 1

# 호스트 시임(test/) 헤더가 선언한 변수(SFR 대역, DI/EI 호출 횟수 계수기)는 제품의 소프트웨어 상태가
# 아니다. 읽기·쓰기 분석에서 통째로 뺀다 — 계수기는 DI/EI 를 쓰는 모든 함수를 이어 붙이는 잡음이다.
# 대가로 레지스터를 매개한 공유(쓴 포트를 다른 함수가 읽음)는 도출하지 못한다(docs/agents/mutation-tool-reference.md 한계).
HOST_SEAM_PREFIX = "test/"

# 제품 전체를 훑을 때 러너 플래그에 더하는 인클루드. 정본은 check_pp_identity.py 다.
EXTRA_INCLUDES = em.PROJECT_CONFIG.get("extra_includes", [])

TYPE_WORDS = {"int", "char", "short", "long", "float", "double", "void", "signed", "unsigned",
              "_Bool", "const", "volatile", "static", "extern", "struct", "union", "enum",
              "typedef", "register", "inline", "__inline", "__inline__", "auto", "restrict"}
NON_NAME = em.KEYWORDS | TYPE_WORDS | {"sizeof", "__attribute__", "__asm__", "asm", "typeof",
                                        "__typeof__", "_Static_assert", "_Alignof"}
ATTRIBUTES = {"__attribute__", "__asm__", "asm", "__declspec"}
ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
OPENERS = {"(", "[", "{"}
CLOSERS = {")", "]", "}"}
FILE_SCOPE = "<file scope>"

MARKER = re.compile(r'^# (\d+) "((?:[^"\\]|\\.)*)"', re.M)
PRAGMA_INTERRUPT = re.compile(r"^#pragma\s+interrupt\s+(\w+)", re.M)


# ---------------------------------------------------------------------------------------------
# 토큰 보조
# ---------------------------------------------------------------------------------------------

def matching_back(tokens, position):
    """tokens[position] 의 ) 에 짝이 되는 여는 ( 위치."""
    depth = 0
    for index in range(position, -1, -1):
        if tokens[index].text == ")":
            depth += 1
        elif tokens[index].text == "(":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("열리지 않은 괄호: {}".format(tokens[position]))


def split_commas(tokens, low, high):
    """[low, high) 를 깊이 0 의 쉼표로 가른 (시작, 끝) 목록."""
    parts = []
    start = low
    index = low
    while index < high:
        text = tokens[index].text
        if text in OPENERS:
            index = em.matching(tokens, index) + 1
            continue
        if text == ",":
            parts.append((start, index))
            start = index + 1
        index += 1
    parts.append((start, high))
    return [part for part in parts if part[0] < part[1]]


@dataclass(frozen=True)
class Declarator:
    name: str
    is_function: bool
    static: bool
    extern: bool
    array: bool


def declarators(tokens, low, high):
    """선언문 [low, high)(세미콜론 제외)이 선언하는 이름들. typedef 와 태그만 있는 선언은 비운다."""
    if tokens[low].text == "typedef":
        return []
    words = set()
    index = low
    while index < high:
        if tokens[index].text in OPENERS:
            index = em.matching(tokens, index) + 1
            continue
        words.add(tokens[index].text)
        index += 1
    static, extern = "static" in words, "extern" in words
    found = []
    for start, end in split_commas(tokens, low, high):
        cut = end
        index = start
        while index < end:
            if tokens[index].text in OPENERS:
                index = em.matching(tokens, index) + 1
                continue
            if tokens[index].text == "=":
                cut = index
                break
            index += 1
        items = []   # ("tok", 위치) 또는 ("group", 여는 위치, 닫는 위치)
        index = start
        while index < cut:
            if tokens[index].text in OPENERS:
                closing = em.matching(tokens, index)
                items.append(("group", index, closing))
                index = closing + 1
            else:
                items.append(("tok", index))
                index += 1
        name, is_function, array = None, False, False
        for position, item in enumerate(items):
            if item[0] != "group" or tokens[item[1]].text == "{":
                continue
            opening, closing = item[1], item[2]
            if tokens[opening].text == "(" and tokens[opening + 1].text == "*":
                inner = [k for k in range(opening + 2, closing) if tokens[k].kind == "id"]
                if inner:
                    name = tokens[inner[-1]].text
                    array = any(tokens[k].text == "[" for k in range(opening + 2, closing))
            elif position and items[position - 1][0] == "tok" and \
                    tokens[items[position - 1][1]].kind == "id":
                name = tokens[items[position - 1][1]].text
                is_function = tokens[opening].text == "("
                array = tokens[opening].text == "["
            break
        else:
            names = [item[1] for item in items if item[0] == "tok" and tokens[item[1]].kind == "id"]
            if names:
                last = names[-1]
                before = tokens[last - 1].text if last > start else ""
                if before not in ("struct", "union", "enum"):
                    name = tokens[last].text
        if name and name not in NON_NAME:
            found.append(Declarator(name, is_function, static, extern, array))
    return found


DECLARATION_WORDS = TYPE_WORDS - {"typedef"}


def starts_declaration(tokens, index):
    token = tokens[index]
    if token.kind != "id":
        return False
    if token.text in DECLARATION_WORDS:
        return True
    if token.text in NON_NAME:
        return False
    following = tokens[index + 1]
    if following.kind == "id" and following.text not in NON_NAME:
        return True
    return following.text == "*" and tokens[index + 2].kind == "id" and \
        tokens[index + 3].text in ("=", ";", ",", "[")


def statement_end(tokens, index, limit):
    """index 에서 시작하는 문장의 세미콜론 위치(깊이 0)."""
    while index < limit and tokens[index].text != ";":
        index = em.matching(tokens, index) + 1 if tokens[index].text in OPENERS else index + 1
    return index


def local_names(tokens, opening, closing, parameters):
    """함수 본문 안 지역 변수(c99 for 선언 포함)와 매개변수 이름."""
    names = set(parameters)
    index = opening + 1
    start = True
    while index < closing:
        text = tokens[index].text
        if text in ("{", "}", ";", "else", "do"):
            start = True
            index += 1
            continue
        if text == "for" and tokens[index + 1].text == "(":
            index += 2
            start = True
            continue
        if start and starts_declaration(tokens, index):
            end = statement_end(tokens, index, closing)
            names.update(d.name for d in declarators(tokens, index, end) if not d.is_function)
            index = end + 1
            start = True
            continue
        start = False
        index += 1
    return names


# ---------------------------------------------------------------------------------------------
# 전처리 결과 분석 (TU 하나)
# ---------------------------------------------------------------------------------------------

@dataclass
class Func:
    name: str
    file: str                                # 정의가 있는 파일, 저장소 기준(전처리 줄 표지로 잰다)
    calls: set = field(default_factory=set)
    refs: set = field(default_factory=set)   # 호출이 아닌 자리에 나온 함수 이름(대입·초기화자·인자)
    reads: set = field(default_factory=set)
    writes: set = field(default_factory=set)
    statics: frozenset = frozenset()         # 이 함수가 속한 TU 에서 static 인 변수 이름


@dataclass
class Var:
    file: str
    static: bool
    array: bool


@dataclass
class Tu:
    functions: list
    variables: dict          # 이름 -> Var
    function_names: set
    scope_refs: dict         # 파일 -> 함수 밖 초기화자에 나온 이름
    interrupts: set


def unescape(path):
    return re.sub(r"\\(.)", r"\1", path)


def relative_path(path, root):
    """저장소 안이면 저장소 기준 `/` 경로, 밖이면 실제 경로 그대로. 상대 경로는 전처리를 돌린 root 기준이다."""
    path = unescape(path)
    real = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    base = os.path.realpath(root)
    inside = os.path.normcase(real) == os.path.normcase(base) or \
        os.path.normcase(real).startswith(os.path.normcase(base) + os.sep)
    return os.path.relpath(real, base).replace("\\", "/") if inside else real.replace("\\", "/")


def same_file(a, b):
    return os.path.normcase(a) == os.path.normcase(b)


def marker_table(text, root):
    offsets, files = [], []
    for match in MARKER.finditer(text):
        offsets.append(match.start())
        raw = match.group(2)
        files.append(raw if raw.startswith("<") else relative_path(raw, root))
    return offsets, files


def body_header(tokens, opening):
    """본문 여는 위치에서 (이름, 매개변수 목록의 여는 괄호, 닫는 괄호) 위치. 함수 정의가 아니면 None.
    `__attribute__((...))` 가 매개변수 뒤에 붙은 모양도 건너뛴다."""
    right = opening - 1
    while right > 0 and tokens[right].text == ")":
        left = matching_back(tokens, right)
        name = left - 1
        if name < 0:
            return None
        if tokens[name].text in ATTRIBUTES:
            right = name - 1
            continue
        if tokens[name].kind != "id" or tokens[name].text in NON_NAME:
            return None
        return tokens[name].text, left, right
    return None


def classify_access(tokens, index, variable):
    """tokens[index] 의 변수 이름이 (읽힘, 쓰임) 중 무엇인지. 쓰임이면 복합 대입처럼 읽힘도 함께 센다.

    쓰임: 접미 사슬(`.f` `->f` `[i]`) 뒤 대입 연산자·`++`·`--`, 앞 `++`·`--`, 단항 `&`. 배열 이름이
    그대로 인자로 넘어가면 호출된 쪽이 쓸 수 있어 쓰임으로 본다. `p->f = x` 의 p 는 값을 읽는 것이기도 하다."""
    previous = tokens[index - 1].text
    follow = index + 1
    through_pointer = False
    while True:
        if tokens[follow].text in (".", "->") and tokens[follow + 1].kind == "id":
            through_pointer = through_pointer or tokens[follow].text == "->"
            follow += 2
        elif tokens[follow].text == "[":
            follow = em.matching(tokens, follow) + 1
        else:
            break
    # 괄호로 감싼 좌변 `(v) = x`: 변수 바로 앞의 묶음 괄호 수만큼 닫는 괄호를 건넌다.
    # 제어문 머리·호출·캐스트의 괄호는 묶음이 아니다.
    back = index - 1
    while tokens[back].text == "*":
        back -= 1
    opens = 0
    while tokens[back].text == "(" and not (tokens[back - 1].kind == "id" or
                                            tokens[back - 1].text in (")", "]")):
        opens += 1
        back -= 1
    while opens and tokens[follow].text == ")":
        follow += 1
        opens -= 1
    operator = tokens[follow].text
    if operator == "=":
        return through_pointer, True   # `p->f = x` 는 포인터 값 p 를 읽는다
    if operator in ASSIGN or operator in ("++", "--") or previous in ("++", "--"):
        return True, True
    if previous == "&" and not em.ends_operand(tokens[index - 2]):
        return True, True
    if variable.array and tokens[index + 1].text in (",", ")") and previous in ("(", ",") and \
            tokens[index - 2].text != "sizeof":
        return True, True
    return True, False


def analyze_tu(text, root):
    """전처리 결과 한 덩어리에서 함수별 호출·변수 참조와 파일 스코프 선언을 뽑는다."""
    tokens = em.tokenize(text)
    offsets, files = marker_table(text, root)

    def file_of(position):
        found = bisect.bisect_right(offsets, position) - 1
        return files[found] if found >= 0 else ""

    bodies = em.function_bodies(tokens)
    closes = {closing for _, closing in bodies}
    variables, function_names, scope_refs = {}, set(), {}
    start, depth = 0, 0
    for index, token in enumerate(tokens):
        if token.text in OPENERS:
            depth += 1
        elif token.text in CLOSERS:
            depth -= 1
            if depth == 0 and index in closes:
                start = index + 1
        elif token.text == ";" and depth == 0:
            if start < index:
                where = file_of(tokens[start].start)
                for declared in declarators(tokens, start, index):
                    if declared.is_function:
                        function_names.add(declared.name)
                        continue
                    known = variables.get(declared.name)
                    variables[declared.name] = Var(
                        known.file if known else where,
                        declared.static or bool(known and known.static),
                        declared.array or bool(known and known.array))
                refs = scope_refs.setdefault(where, set())
                for low, high in split_commas(tokens, start, index):
                    for position in range(low, high):
                        if tokens[position].text == "=":
                            refs.update(t.text for t in tokens[position + 1:high]
                                        if t.kind == "id")
                            break
            start = index + 1
    headers = [(opening, closing, body_header(tokens, opening)) for opening, closing in bodies]
    function_names.update(header[0] for _, _, header in headers if header)
    statics = frozenset(name for name, var in variables.items() if var.static)

    functions = []
    for opening, closing, header in headers:
        if not header:
            continue
        name, left, right = header
        parameters = {d.name for low, high in split_commas(tokens, left + 1, right)
                      for d in declarators(tokens, low, high)}
        scope = local_names(tokens, opening, closing, parameters)
        func = Func(name, file_of(tokens[opening].start), statics=statics)
        for index in range(opening + 1, closing):
            token = tokens[index]
            if token.kind != "id" or token.text in NON_NAME:
                continue
            if tokens[index - 1].text in (".", "->", "struct", "union", "enum"):
                continue
            if token.text in scope:
                continue
            variable = variables.get(token.text)
            if variable and variable.file.startswith(HOST_SEAM_PREFIX):
                continue
            if tokens[index + 1].text == "(":
                func.calls.add(token.text)
                if variable:    # 함수 포인터 변수 호출은 그 변수를 읽는 것이다
                    func.reads.add(token.text)
                continue
            if variable:
                read, write = classify_access(tokens, index, variable)
                if read:
                    func.reads.add(token.text)
                if write:
                    func.writes.add(token.text)
            elif token.text in function_names:
                func.refs.add(token.text)
        functions.append(func)
    return Tu(functions, variables, function_names, scope_refs,
              set(PRAGMA_INTERRUPT.findall(text)))


def merge_functions(tus, file):
    """TU 여럿(빌드 구성)에서 file 에 정의된 함수를 이름으로 합친다."""
    merged = {}
    for tu in tus:
        for func in tu.functions:
            if not same_file(func.file, file):
                continue
            into = merged.setdefault(func.name, Func(func.name, func.file, statics=func.statics))
            into.calls |= func.calls
            into.refs |= func.refs
            into.reads |= func.reads
            into.writes |= func.writes
            into.statics = into.statics | func.statics
    return merged


# ---------------------------------------------------------------------------------------------
# 변경 분류: 시작점, 지워진 함수, 본문 밖 변경
# ---------------------------------------------------------------------------------------------

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)


def parse_hunks(diff):
    """`git diff --unified=0` 한 파일의 (옛 시작, 옛 줄수, 새 시작, 새 줄수) 목록."""
    return [(int(a), int(b) if b else 1, int(c), int(d) if d else 1)
            for a, b, c, d in HUNK.findall(diff)]


@dataclass(frozen=True)
class Span:
    name: str
    first: int   # 머리 첫 줄
    last: int    # 닫는 중괄호 줄
    start: int   # 머리 첫 글자 위치
    end: int     # 닫는 중괄호 끝 위치


def spans(source):
    tokens = em.tokenize(source)
    found = []
    for name, _, _, _, opening, closing in em.functions(tokens):
        begin = opening
        while begin > 0 and tokens[begin - 1].text not in (";", "}"):
            begin -= 1
        start, end = tokens[begin].start, tokens[closing].end
        found.append(Span(name, source.count("\n", 0, start) + 1,
                          source.count("\n", 0, end - 1) + 1, start, end))
    return found


def strip_comments(text):
    out = []
    index, length = 0, len(text)
    while index < length:
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            out.append(" ")
        elif text[index] in "\"'":
            quote = text[index]
            end = index + 1
            while end < length and text[end] != quote and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            out.append(text[index:end + 1])
            index = end + 1
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def outside_signature(source):
    """함수(머리부터 닫는 중괄호)를 빼고 주석·공백을 정규화한 파일 스코프 텍스트.
    함수가 늘거나 줄어도 같고, 선언·매크로·`#if` 구조가 바뀌면 달라진다."""
    pieces, cursor = [], 0
    for span in sorted(spans(source), key=lambda s: s.start):
        pieces.append(source[cursor:span.start])
        cursor = max(cursor, span.end)
    pieces.append(source[cursor:])
    return " ".join(strip_comments(" ".join(pieces)).split())


def touched(found, begin, count):
    """줄 구간 [begin, begin+count) 에 걸리는 함수 이름. 줄이 없으면(count 0) 아무것도 아니다."""
    if count == 0:
        return set()
    return {s.name for s in found if s.first < begin + count and begin <= s.last}


def inside_gap(found, after):
    """줄 after 와 그 다음 줄 사이(삭제된 자리)가 한 함수 안인가."""
    return {s.name for s in found if s.first <= after and after + 1 <= s.last}


@dataclass
class Changes:
    starts: set          # 새 파일에 있는 시작점 함수
    deleted: set         # 옛 파일에만 있는 함수
    outside: bool        # 함수 밖(선언·매크로·#if 구조)이 바뀜
    defined: list        # 새 파일의 함수 이름(정의 순)


def classify_changes(old_source, new_source, hunks):
    old_spans, new_spans = spans(old_source), spans(new_source)
    named = set()
    for old_start, old_count, new_start, new_count in hunks:
        named |= touched(old_spans, old_start, old_count)
        named |= touched(new_spans, new_start, new_count)
        if new_count == 0:
            named |= inside_gap(new_spans, new_start)
    present = {s.name for s in new_spans}
    return Changes(named & present, named - present,
                   outside_signature(old_source) != outside_signature(new_source),
                   [s.name for s in new_spans])


# ---------------------------------------------------------------------------------------------
# 확장: 호출자·공용 상태·ISR 공유 상태, 간접 참조, 파일 밖 읽기
# ---------------------------------------------------------------------------------------------

@dataclass
class Program:
    functions: list   # 대상 파일 밖 제품 함수들(파일별 단독 전처리)
    interrupts: set


def reach(roots, edges):
    seen, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(edges.get(name, ()))
    return seen


def contexts(functions, interrupts):
    """이름 -> {"isr", "main"} 의 부분집합. ISR 은 `#pragma interrupt` 가 루트, main 은 `main` 이 루트다.
    호출이 아닌 함수 이름 참조(함수 포인터 대입)도 간선으로 센다. 어느 쪽에서도 닿지 않으면 main 이다."""
    edges = {}
    for func in functions:
        edges.setdefault(func.name, set()).update(func.calls | func.refs)
    isr, main = reach(interrupts, edges), reach({"main"}, edges)
    return {name: (({"isr"} if name in isr else set()) | ({"main"} if name in main else set()))
            or {"main"} for name in edges}


def isr_shared_variables(functions, context):
    access = {}
    for func in functions:
        for name in func.reads | func.writes:
            access.setdefault(name, set()).update(context.get(func.name, {"main"}))
    return {name for name, seen in access.items() if {"isr", "main"} <= seen}


def callers_of(names, functions, depth=CALLER_DEPTH):
    """functions(이름 -> Func) 안에서 names 를 호출하는 함수 이름, depth 단계까지(names 자신 제외)."""
    found, frontier = set(), set(names)
    for _ in range(depth):
        frontier = {f.name for f in functions.values() if f.calls & frontier} - found - set(names)
        found |= frontier
    return found


@dataclass
class Derivation:
    target: str
    base: str
    diff_sha256: str
    derived: list
    reasons: dict
    fallback: object          # None 또는 사유 문자열
    indirect: list            # 사람이 풀 항목(간접 참조·파일 밖 읽기)
    defined: list
    starts: list = field(default_factory=list)
    deleted: list = field(default_factory=list)


def expand(changes, new_funcs, old_funcs, variables, scope_refs, program, target):
    """시작점·지워진 함수에서 도출 집합과 사람이 풀 항목을 낸다.

    new_funcs/old_funcs: 대상 파일의 새·옛 함수(이름 -> Func), variables: 대상 TU 의 파일 스코프 변수(이름 -> Var),
    scope_refs: 대상 파일의 함수 밖 초기화자에 나온 함수 이름.
    program: 대상 파일 밖 제품 함수와 ISR 루트."""
    reasons = {}

    def add(name, why):
        if name in new_funcs:
            reasons.setdefault(name, []).append(why)

    for name in sorted(changes.starts):
        add(name, "시작점")
    for name in sorted(callers_of(changes.starts, new_funcs)):
        add(name, "시작점의 호출자")
    # 부모 기준 호출자: 호출을 걷어낸 호출자는 본문이 바뀌어 시작점이다. 호출이 그대로인 호출자(함수를 다른 파일로
    # 옮긴 경우)는 새 파일에서도 보이므로 두 쪽을 모두 본다.
    for name in sorted(callers_of(changes.deleted, old_funcs) | callers_of(changes.deleted, new_funcs)):
        add(name, "지워진 함수의 호출자")

    functions = list(new_funcs.values()) + program.functions
    context = contexts(functions, program.interrupts)
    shared = isr_shared_variables(functions, context)

    def of(name):
        return context.get(name, {"main"})

    # 쓰기를 걷어낸 변경도 읽는 쪽의 입력이 바뀐다 — 새 쓰기에 부모의 쓰기를 합친다.
    writes = {}
    for name in changes.starts | changes.deleted:
        writes[name] = ((new_funcs[name].writes if name in new_funcs else set()) |
                        (old_funcs[name].writes if name in old_funcs else set()))
    written = {}
    for name in sorted(writes):
        for variable in writes[name]:
            written.setdefault(variable, []).append(name)
    for variable, writers in sorted(written.items()):
        readers = {f.name for f in new_funcs.values() if variable in f.reads} - set(writers)
        for name in sorted(readers):
            add(name, "공용 상태: {} ({} 이 씀)".format(variable, ", ".join(sorted(writers))))
            if variable in shared and any(a != b for w in writers for a in of(w) for b in of(name)):
                add(name, "ISR 공유 상태: {} (쓰는 쪽과 반대 문맥에서 읽음)".format(variable))

    items = []
    for name in sorted(writes):
        sites = sorted({f.name for f in new_funcs.values() if name in f.refs and f.name != name})
        if name in scope_refs:
            sites.append(FILE_SCOPE)
        if sites and name in changes.starts:
            items.append({"function": name, "kind": "indirect", "referenced_in": sites,
                          "resolution": "", "reason": ""})
        for variable in sorted(writes[name]):
            var = variables.get(variable)
            if var is None or var.static:   # static 이면 다른 파일의 같은 이름은 다른 변수다
                continue
            readers = sorted({"{}:{}".format(f.file, f.name) for f in program.functions
                              if variable in f.reads and variable not in f.statics
                              and not same_file(f.file, target)})
            if readers:
                items.append({"function": name, "kind": "external_reader", "variable": variable,
                              "referenced_in": readers, "isr_shared": variable in shared,
                              "resolution": "", "reason": ""})
    return sorted(reasons), reasons, items


# ---------------------------------------------------------------------------------------------
# 입출력: git, 러너 구성, 전처리
# ---------------------------------------------------------------------------------------------

def git_bytes(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


def product_files(root):
    return [f for f in em.git("ls-files", "-z", "--", *em.PRODUCT_PATHSPEC, cwd=root).split("\0")
            if f.endswith(".c")]


def relative_dependencies(root, configs):
    found = set()
    for config in configs:
        for raw in config["dependencies"]:
            path = raw if os.path.isabs(raw) else os.path.join(root, raw)
            found.add(relative_path(path, root).casefold())
    return found


def tu_texts(root, configs):
    return [em.preprocess(root, config, "-E") for config in configs]


def scan_program(root, runner, target, progress=None):
    """대상 파일 밖의 제품 .c 마다 러너와 같은 플래그로 단독 전처리해 Program 을 만든다."""
    functions, interrupts, seen = [], set(), set()
    for name in product_files(root):
        if same_file(name, target):
            continue
        if progress:
            progress("제품 전처리: " + name)
        flags = list(runner.CFLAGS) + ["-I" + str(Path(root) / d) for d in EXTRA_INCLUDES]
        result = subprocess.run([runner.CC] + flags + ["-E", name], cwd=root, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise em.ToolError("제품 전처리 실패: {}\n{}".format(name, result.stderr[-2000:]))
        tu = analyze_tu(result.stdout, root)
        interrupts |= tu.interrupts
        for func in tu.functions:
            if (func.file, func.name) not in seen and not same_file(func.file, target):
                seen.add((func.file, func.name))
                functions.append(func)
    return Program(functions, interrupts)


def product_diff_sha256(root, sha, cached=False):
    """제품 경로의 base 대비 diff hash. cached 면 작업 트리 대신 index(커밋될 내용)와 비교한다.
    검토 JSON 의 scope.diff_sha256 이 이 값이다."""
    args = ["-c", "core.quotepath=off", "diff"] + (["--cached"] if cached else []) + \
        ["--no-renames", "--unified=0", sha, "--", *em.PRODUCT_PATHSPEC]
    return hashlib.sha256(git_bytes(root, *args)).hexdigest()


def derive(root, target, base, progress=None):
    """작업 트리의 target 이 base 커밋 대비 바뀐 것에서 영향 범위를 도출한다."""
    root = Path(root)
    target = target.replace("\\", "/")
    progress = progress or (lambda message: None)
    try:
        sha = em.git("rev-parse", "--verify", base + "^{commit}", cwd=root).strip()
    except subprocess.CalledProcessError as error:
        raise em.ToolError("base 리비전을 찾지 못했다: {}\n{}".format(base, error.stderr))
    digest = product_diff_sha256(root, sha)
    source = (root / target).read_bytes().decode("utf-8")
    defined = [s.name for s in spans(source)]

    def fallback(reason):
        return Derivation(target, sha, digest, sorted(set(defined)), {}, reason, [], defined)

    try:
        old = git_bytes(root, "show", "{}:{}".format(sha, target)).decode("utf-8")
    except subprocess.CalledProcessError:
        return fallback("대상 파일이 base 에 없다")
    hunks = parse_hunks(git_bytes(root, "-c", "core.quotepath=off", "diff", "--no-renames",
                                  "--unified=0", sha, "--", target).decode("utf-8", "replace"))
    changes = classify_changes(old, source, hunks)
    if changes.outside:
        return fallback("함수 밖(선언·매크로·#if 구조) 변경")

    runner, configs = vs.current_configs(root, target)
    dependencies = relative_dependencies(root, configs)
    changed = [p for p in em.git("diff", "--name-only", "-z", "--no-renames", sha, "--",
                                 *em.PRODUCT_PATHSPEC, cwd=root).split("\0") if p]
    headers = [p for p in changed if p.endswith(".h") and p.casefold() in dependencies]
    if headers:
        return fallback("헤더 변경: " + ", ".join(sorted(headers)))
    if not (changes.starts or changes.deleted):
        return Derivation(target, sha, digest, [], {}, None, [], defined)

    tus = [analyze_tu(text, root) for text in tu_texts(root, configs)]
    new_funcs = merge_functions(tus, target)
    # 어느 구성에서도 컴파일되지 않는 함수(`#if` 로 빠지거나 매크로로 대체된 것)는 호출도 변수 접근도 하지
    # 않으므로 시작점일 때만 분석할 수 없어 폴백한다. 모든 함수에 요구하면 option.c·system.c·motor.c 등
    # 비활성 함수가 있는 파일은 늘 폴백한다(2026-10-05 실측, 이슈 13).
    unmatched = sorted(changes.starts - set(new_funcs))
    if unmatched:
        return fallback("전처리 결과에서 대응하지 못한 시작점: " + ", ".join(unmatched))
    old_funcs = {}
    if changes.deleted or changes.starts:   # 지워진 함수의 호출자와 걷어낸 쓰기를 부모에서 본다
        try:
            with vs.prepare(root, sha, vs.PRISTINE_PARENT, target,
                            on_teardown=lambda seconds: em.TIMING.add("teardown", seconds)) as parent:
                old_tus = [analyze_tu(text, parent.path) for text in tu_texts(parent.path, parent.configs)]
                old_funcs = merge_functions(old_tus, target)
        except (em.ToolError, subprocess.CalledProcessError, OSError) as error:
            return fallback("base 의 전처리를 얻지 못했다: {}".format(str(error).splitlines()[0]))
    scope_refs = set()
    for tu in tus:
        for file, names in tu.scope_refs.items():
            if same_file(file, target):
                scope_refs |= names & tu.function_names
    program = scan_program(root, runner, target, progress)
    program.interrupts |= {name for tu in tus for name in tu.interrupts}
    variables = {}
    for tu in tus:
        for variable, var in tu.variables.items():
            known = variables.get(variable)
            variables[variable] = Var(var.file, var.static or bool(known and known.static),
                                      var.array)
    derived, reasons, items = expand(changes, new_funcs, old_funcs, variables, scope_refs,
                                     program, target)
    return Derivation(target, sha, digest, derived, reasons, None, items, defined,
                      sorted(changes.starts), sorted(changes.deleted))


# ---------------------------------------------------------------------------------------------
# 기록·게이트: 검토 JSON 의 scope 절
# ---------------------------------------------------------------------------------------------

RESOLUTIONS = ("measured", "outside_target")


def measured_functions(report_functions, defined):
    """보고서의 측정 함수 목록. 비어 있으면 파일 전체이므로 정의된 모든 함수다."""
    return sorted(set(report_functions) if report_functions else set(defined))


def missing_functions(derivation, measured):
    return sorted(set(derivation.derived) - set(measured))


def scope_clause(derivation, measured):
    """검토 JSON 의 scope 절 초안. 해소 칸(resolution·reason)은 사람이 채운다."""
    return {"base": derivation.base, "diff_sha256": derivation.diff_sha256,
            "derived": list(derivation.derived), "measured": list(measured),
            "fallback": derivation.fallback,
            "missing": [{"function": name, "reason": ""}
                        for name in missing_functions(derivation, measured)],
            "indirect": [dict(item) for item in derivation.indirect]}


def item_key(item):
    return (item.get("function"), item.get("kind"), item.get("variable"),
            tuple(item.get("referenced_in", ())))


def scope_issues(derivation, scope, measured):
    """검토 JSON 의 scope 절을 현재 도출과 측정 범위에 대조한 실패 사유 목록."""
    if not isinstance(scope, dict):
        return ["scope 절이 없다: --write-template 으로 초안을 만든다"]
    for name in ("missing", "indirect"):
        entries = scope.get(name)
        if entries is not None and (not isinstance(entries, list) or
                                    not all(isinstance(entry, dict) for entry in entries)):
            return ["scope.{} 는 객체의 목록이어야 한다".format(name)]
    issues = []
    if scope.get("base") != derivation.base:
        issues.append("scope.base 가 현재 --base 와 다르다: 초안을 다시 만든다")
    if scope.get("diff_sha256") != derivation.diff_sha256:
        issues.append("scope.diff_sha256 이 현재 diff 와 다르다: 변경을 고친 뒤 예전 판단을 재사용할 수 없다")
    if scope.get("derived") != list(derivation.derived) or scope.get("fallback") != derivation.fallback:
        issues.append("scope.derived·fallback 이 현재 도출과 다르다: 초안을 다시 만든다")
    if scope.get("measured") != list(measured):
        issues.append("scope.measured 가 측정 보고서의 범위와 다르다: 초안을 다시 만든다")
    reasons = {}
    for entry in scope.get("missing") or []:
        reasons[entry.get("function")] = entry.get("reason")
    for name in missing_functions(derivation, measured):
        if not em.filled(reasons.get(name)):
            issues.append("도출됐지만 측정하지 않은 함수에 reason 이 없다: " + name)
    recorded = {}
    for item in scope.get("indirect") or []:
        recorded[item_key(item)] = item
    expected = {item_key(item) for item in derivation.indirect}
    for key in sorted(expected - set(recorded), key=str):
        issues.append("간접·파일 밖 항목이 검토에 없다: {} {}".format(key[0], key[1]))
    for key in sorted(set(recorded) - expected, key=str):
        issues.append("검토에 현재 도출에 없는 항목이 있다: {} {}".format(key[0], key[1]))
    for key, item in recorded.items():
        if key not in expected:
            continue
        label = "{} {}".format(item.get("function"), item.get("kind"))
        if item.get("variable"):
            label += " " + item["variable"]
        resolution = item.get("resolution")
        if resolution not in RESOLUTIONS:
            issues.append("resolution 이 비었거나 {} 이 아니다: {}".format("/".join(RESOLUTIONS), label))
        if not em.filled(item.get("reason")):
            issues.append("reason 이 비었다: " + label)
        if resolution == "measured":
            if item.get("kind") == "external_reader":
                issues.append("파일 밖 읽기는 measured 로 풀 수 없다(outside_target): " + label)
            absent = sorted(({item.get("function")} | set(item.get("referenced_in", ())))
                            - {FILE_SCOPE} - set(measured))
            if absent:
                issues.append("measured 인데 측정 범위에 없다: {} ({})".format(label, ", ".join(absent)))
    return issues


def summary(derivation, issues, measured):
    """동작 변경 커밋 메시지에 옮길 한 줄. issues 는 게이트 전체의 실패 사유다 — 범위 검사만 통과하고
    다른 검토가 실패한 채 '통과'로 옮겨 가지 않게 한다."""
    head = "요약: 영향 범위 게이트 {} — base {}, 도출 {}개, 측정 {}개, 폴백 {}, 간접·파일 밖 {}건".format(
        "통과" if not issues else "미완료", derivation.base[:7], len(derivation.derived),
        len(measured), derivation.fallback or "없음", len(derivation.indirect))
    if issues:
        head += ", 실패 사유 {}건".format(len(issues))
    return head


def to_json(derivation):
    return {"base": derivation.base, "diff_sha256": derivation.diff_sha256,
            "derived": derivation.derived, "fallback": derivation.fallback,
            "reasons": derivation.reasons, "starts": derivation.starts,
            "deleted": derivation.deleted, "indirect": derivation.indirect}


def format_derivation(derivation):
    lines = ["# 영향 범위: {} (base {})".format(derivation.target, derivation.base[:12]), ""]
    lines.append("- 시작점: " + (", ".join("`{}`".format(n) for n in derivation.starts) or "없음"))
    if derivation.deleted:
        lines.append("- 지워진 함수: " + ", ".join("`{}`".format(n) for n in derivation.deleted))
    lines.append("- 폴백: " + (derivation.fallback or "없음"))
    lines.append("- 도출 {}개 / 파일의 함수 {}개".format(len(derivation.derived),
                                                    len(set(derivation.defined))))
    for name in derivation.derived:
        lines.append("  - `{}` — {}".format(name, "; ".join(derivation.reasons.get(name, ["폴백"]))))
    if derivation.indirect:
        lines += ["", "## 사람이 풀 항목 ({}건)".format(len(derivation.indirect))]
        for item in derivation.indirect:
            lines.append("- `{}` {}{}: {}".format(
                item["function"], item["kind"],
                " `{}`".format(item["variable"]) if item.get("variable") else "",
                ", ".join(item["referenced_in"])))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="저장소 루트 기준 제품 .c 경로")
    parser.add_argument("--base", required=True, help="비교할 커밋(커밋 전 HEAD, 커밋 후 HEAD~1)")
    parser.add_argument("--json", help="도출 결과를 쓸 JSON 경로")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        derivation = derive(em.ROOT, args.target, args.base,
                            lambda message: print(message, file=sys.stderr, flush=True))
    except (em.ToolError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print("도구 오류 — 영향 범위를 도출하지 못했다: {}".format(error), file=sys.stderr)
        return 1
    if args.json:
        Path(args.json).write_text(json.dumps(to_json(derivation), ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    sys.stdout.write(format_derivation(derivation))
    return 0


if __name__ == "__main__":
    sys.exit(main())
