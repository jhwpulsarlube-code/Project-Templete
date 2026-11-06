#!/usr/bin/env python3
"""전수 변이 보고서(--json) 두 개를 변이 id 로 대조한다. 기본 syntax 와 --schemata=all 의 판정 일치 점검(이슈 18).

    python -B test/tools/compare_reports.py <기준 report.json> <후보 report.json>

공통 id 의 식별 키 일치, 판정 쌍(기준 → 후보) 개수, 생존자 집합 차이를 출력한다.
종료 코드: 0 일치, 1 식별 키·id 집합·생존자 집합이 다름, 2 입력 오류. 절차는 docs/agents/mutation-tool-reference.md.
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import sys

sys.dont_write_bytecode = True


class ReportError(Exception):
    pass


def load_report(path):
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        results = report["results"]
        # probe 가 실패한 측정은 results 가 비어도 보고서를 쓴다. 그런 두 보고서는 일치로 보이므로 거부한다.
        if report["valid"] is not True or not results:
            raise ValueError("valid 가 true 가 아니거나 results 가 비었다")
        return {r["id"]: (r["key"], r["status"]) for r in results}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReportError("{}: 보고서를 읽을 수 없다 ({}: {})".format(
            path, type(error).__name__, error)) from error


def compare(base, candidate):
    """base·candidate 는 {id: (key, status)}. 대조 결과 dict 를 돌려준다."""
    common = base.keys() & candidate.keys()
    return {
        "key_mismatch": sorted(i for i in common if base[i][0] != candidate[i][0]),
        "only_base": sorted(base.keys() - candidate.keys()),
        "only_candidate": sorted(candidate.keys() - base.keys()),
        "pairs": Counter((base[i][1], candidate[i][1]) for i in common),
        "survivors_only_base": sorted(i for i in base
                                      if base[i][1] == "survived" and candidate.get(i, (None, None))[1] != "survived"),
        "survivors_only_candidate": sorted(i for i in candidate
                                           if candidate[i][1] == "survived" and base.get(i, (None, None))[1] != "survived"),
        "common": len(common),
    }


def is_match(found):
    return not (found["key_mismatch"] or found["only_base"] or found["only_candidate"]
                or found["survivors_only_base"] or found["survivors_only_candidate"])


def format_report(found):
    lines = ["공통 id {}개".format(found["common"]), "", "판정 쌍 (기준 → 후보):"]
    for (before, after), count in sorted(found["pairs"].items()):
        lines.append("  {} → {}: {}".format(before, after, count))
    sections = [("식별 키가 다른 id", found["key_mismatch"]),
                ("기준에만 있는 id", found["only_base"]),
                ("후보에만 있는 id", found["only_candidate"]),
                ("기준에서만 생존자", found["survivors_only_base"]),
                ("후보에서만 생존자", found["survivors_only_candidate"])]
    for title, ids in sections:
        lines.append("{}: {}".format(title, len(ids)) + ("  " + ", ".join(map(str, ids[:20])) if ids else ""))
    changed = sum(c for (b, a), c in found["pairs"].items() if b != a)
    lines.append("")
    lines.append("요약: {} (판정이 바뀐 변이 {}, 생존자 집합 {})".format(
        "일치" if is_match(found) else "불일치", changed,
        "같음" if not (found["survivors_only_base"] or found["survivors_only_candidate"]) else "다름"))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("base", help="기준 보고서(보통 기본 syntax 측정)")
    parser.add_argument("candidate", help="후보 보고서(보통 --schemata=all 측정)")
    args = parser.parse_args(argv)
    try:
        found = compare(load_report(args.base), load_report(args.candidate))
    except ReportError as error:
        print(error, file=sys.stderr)
        return 2
    print("\n".join(format_report(found)))
    return 0 if is_match(found) else 1


if __name__ == "__main__":
    # Windows 콘솔 코드 페이지(cp949)로는 '→' 를 못 쓴다.
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")
    sys.exit(main())
