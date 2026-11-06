#!/usr/bin/env python3
"""전수 측정과 현재 기준선·검토 증거를 대조하는 완료 게이트. 절차는 docs/agents/host-test-workflow.md."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import affected_scope as scope_tool
import exhaustive_mutation as em
import verification_snapshot as vs


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accepted_hashes(path):
    """증거 파일의 hash 로 인정하는 값: 원본 바이트, LF 정규화, CRLF 정규화.
    작업 트리는 CRLF(core.autocrlf), 인덱스·새로 만든 파일은 LF 라 같은 내용이 다른 바이트가 된다.
    예전 검토 JSON 의 hash(원본 바이트)도 그대로 통과한다."""
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    return {hashlib.sha256(data).hexdigest() for data in (raw, lf, crlf)}


def canonical_hash(path):
    """새로 쓰는 hash: LF 정규화(인덱스에 들어가는 형태). review_context 의 content_sha256 과 같다."""
    return em.content_sha256(path)


def entry_hash(item):
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def artifact_matches(root, reference, expected_hash):
    if not em.filled(reference) or not em.filled(expected_hash):
        return False
    relative = Path(reference.split("#", 1)[0])
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        return False
    return expected_hash in accepted_hashes(path)


def review_key(item):
    raw = item.get("key")
    if not isinstance(raw, dict) or set(raw) != set(em.KEY_FIELDS):
        raise ValueError("검토 key에 다섯 식별 필드가 필요하다")
    if not all(isinstance(raw[f], str) for f in em.KEY_FIELDS[:4]) or type(raw["ordinal"]) is not int:
        raise ValueError("검토 key의 타입이 잘못됐다")
    return em.key_tuple(raw)


def changed_inputs(measured, current):
    """측정 당시와 현재 review_context 에서 달라진 파일·항목 이름(최대 8개)."""
    measured_files, current_files = measured.get("files"), current.get("files")
    if not isinstance(measured_files, dict) or not isinstance(current_files, dict):
        measured_files, current_files = {}, {}
    names = sorted(name for name in set(measured_files) | set(current_files)
                   if measured_files.get(name) != current_files.get(name))
    names += [key for key in ("compiler", "measurement", "configs") if measured.get(key) != current.get(key)]
    return names[:8]


def evaluate(report, baseline, review, root, report_hash, current_context, safety):
    """증거의 최신성·분류 누락을 검사한다. 등가의 의미 판단은 검토자가 증거에 기록한다."""
    issues = []
    if not report.get("valid"):
        issues.append("측정 valid=false")
    probes = report.get("probes", {})
    for name, expected in (("kill", "killed"), ("survive", "survived")):
        probe = probes.get(name, {})
        if probe.get("expected") != expected or probe.get("verdict", {}).get("status") != expected:
            issues.append("{} probe 불성립".format(name))
    context = report.get("review_context")
    if not isinstance(context, dict) or not context.get("files") or not context.get("configs"):
        issues.append("입력 fingerprint가 없는 측정: 새 도구로 다시 측정한다")
    elif report.get("review_context_sha256") != em.review_context_hash(context) or context != current_context:
        changed = changed_inputs(context, current_context)
        issues.append("제품·헤더·테스트·러너·도구·컴파일러·구성이 측정 이후 바뀌었다: 다시 측정한다"
                      + (" (달라진 입력: {})".format(", ".join(changed)) if changed else ""))
    if review.get("target") != report.get("target") or review.get("report_sha256") != report_hash:
        issues.append("검토가 현재 측정 파일에 대응하지 않는다")
    if not artifact_matches(root, review.get("observation_matrix"), review.get("observation_matrix_sha256")):
        issues.append("관찰표가 없거나 검토 이후 바뀌었다")

    scope = set(report.get("functions", [])) or None
    if scope is not None and safety - scope:
        issues.append("안전 경로 미측정: " + ", ".join(sorted(safety - scope)))
    records = {}
    for record in review.get("reviews", []):
        key = review_key(record)
        if key in records:
            issues.append("중복 검토: " + em.key_text(key))
        records[key] = record
    results = {}
    for result in report.get("results", []):
        key = review_key(result)
        if key in results or (scope is not None and key[0] not in scope):
            issues.append("중복 또는 함수 범위 밖 측정: " + em.key_text(key))
        if result.get("status") not in ("killed", "survived", "invalid"):
            issues.append("잘못된 변이 판정: " + em.key_text(key))
        results[key] = result
    for key, item in baseline.items():
        if scope is not None and key[0] not in scope:
            continue
        status = results.get(key, {}).get("status")
        if (item["state"] == "kill" and status != "killed") or \
                (item["state"] != "kill" and status != "survived"):
            issues.append("기준선 사라짐/재생존: " + em.key_text(key))
    for key, result in results.items():
        if result.get("status") != "survived":
            continue
        item = baseline.get(key)
        if item is None or item["state"] in ("미검토", "kill"):
            issues.append("새 생존자 또는 미검토: " + em.key_text(key))
            continue
        record = records.get(key, {})
        if record.get("entry_sha256") != entry_hash(item):
            issues.append("현재 기준선 판정을 검토하지 않았다: " + em.key_text(key))
        if not artifact_matches(root, record.get("evidence"), record.get("evidence_sha256")):
            issues.append("검토 증거가 없거나 바뀌었다: " + em.key_text(key))
        if item["state"] == "조건부 등가" and not em.filled(record.get("premise_checked")):
            issues.append("현재 전제 확인 누락: " + em.key_text(key))
        if item["state"] == "제외" and record.get("exclusion_kind") not in \
                ("outside_contract", "unavailable_target"):
            issues.append("제외 범위 증명 누락: 관찰 부족은 테스트/시임으로 처리한다: " + em.key_text(key))
    return issues


def make_template(report, baseline, report_hash, derivation=None):
    records = []
    for result in report.get("results", []):
        if result.get("status") != "survived":
            continue
        key = review_key(result)
        item = baseline.get(key)
        record = {"key": result["key"], "entry_sha256": entry_hash(item) if item else "",
                  "evidence": "", "evidence_sha256": ""}
        if item and item["state"] == "조건부 등가":
            record["premise_checked"] = ""
        if item and item["state"] == "제외":
            record["exclusion_kind"] = ""
        records.append(record)
    template = {"target": report["target"], "report_sha256": report_hash,
                "observation_matrix": "", "observation_matrix_sha256": "", "reviews": records}
    if derivation is not None:
        measured = scope_tool.measured_functions(report.get("functions"), derivation.defined)
        template["scope"] = scope_tool.scope_clause(derivation, measured)
    return template


def refresh_hashes(review, root, report_hash):
    """증거 파일을 고친 뒤 검토 JSON 의 파일 hash 만 현재 값으로 갱신한다. 바뀐 항목 이름을 돌려준다.

    기준선 판정(entry_sha256)은 건드리지 않는다 — 판정이 바뀐 항목은 사람이 다시 검토한다.
    파일이 없거나 저장소 밖이면 그 항목은 갱신하지 않아 게이트가 계속 거부한다.
    """
    changed = []

    def refresh(holder, reference_field, hash_field, label):
        reference = holder.get(reference_field)
        if not em.filled(reference):
            return
        relative = Path(reference.split("#", 1)[0])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
            return
        if holder.get(hash_field) not in accepted_hashes(path):
            holder[hash_field] = canonical_hash(path)
            changed.append(label)

    if review.get("report_sha256") != report_hash:
        review["report_sha256"] = report_hash
        changed.append("report_sha256")
    refresh(review, "observation_matrix", "observation_matrix_sha256", "observation_matrix")
    for record in review.get("reviews", []):
        refresh(record, "evidence", "evidence_sha256", "evidence " + em.key_text(review_key(record)))
    return changed


def fill_review(review, baseline, root, observations=None, evidence=None, exclusion_sources=(),
                premise_lead=None):
    """검토 초안의 비어 있는 칸을 기준선·옛 검토 기록에서 기계적으로 채운다. 채운 칸 이름을 돌려준다.

    이미 채운 칸은 건드리지 않는다(사람의 판정을 덮지 않는다). 채우는 칸:
    - `observation_matrix`(+hash): `observations` 경로.
    - `evidence`(+hash): 함수별 `evidence` 재정의, 없으면 기준선 항목의 `ledger`.
    - `exclusion_kind`: `exclusion_sources`(옛 검토 JSON 들)에서 같은 key 의 값.
    - `premise_checked`: 조건부 등가 항목에 `premise_lead` + 기준선 전제. 이 문구는 전제를 실제로 확인했다는 진술이다.
    """
    changed = []
    evidence = evidence or {}
    if observations and not em.filled(review.get("observation_matrix")):
        path = (root / observations).resolve()
        review["observation_matrix"] = observations
        review["observation_matrix_sha256"] = canonical_hash(path)
        changed.append("observation_matrix")
    inherited = {}
    for source in exclusion_sources:
        for record in source.get("reviews", []):
            if record.get("exclusion_kind"):
                inherited[review_key(record)] = record["exclusion_kind"]
    for record in review.get("reviews", []):
        key = review_key(record)
        item = baseline.get(key)
        label = em.key_text(key)
        if not em.filled(record.get("evidence")):
            reference = evidence.get(key[0]) or (item or {}).get("ledger")
            path = (root / reference.split("#", 1)[0]).resolve() if em.filled(reference) else None
            if path is not None and path.is_relative_to(root.resolve()) and path.is_file():
                record["evidence"] = reference
                record["evidence_sha256"] = canonical_hash(path)
                changed.append("evidence " + label)
        if "exclusion_kind" in record and not em.filled(record["exclusion_kind"]) and key in inherited:
            record["exclusion_kind"] = inherited[key]
            changed.append("exclusion_kind " + label)
        if (item and item["state"] == "조건부 등가" and em.filled(premise_lead)
                and not em.filled(record.get("premise_checked"))):
            record["premise_checked"] = "{} 전제: {}".format(premise_lead, item.get("premise", ""))
            changed.append("premise_checked " + label)
    return changed


def current_review_context(root, target, method):
    """작업 트리의 현재 review_context(보고서와 대조하는 입력 fingerprint)."""
    runner, configs = vs.current_configs(root, target)
    compiler = subprocess.run([runner.CC, "--version"], check=True, capture_output=True,
                              text=True).stdout.splitlines()[0]
    return em.capture_review_context(root, configs, compiler, method)


def gate_stamp(report, derivation):
    """통과한 게이트를 검토 JSON 에 남기는 `gate` 절. pre-commit hook 이 측정 없이 이 절만 대조한다
    (test/tools/precommit_gate.py). 지문은 통과 시점의 보고서 지문이다."""
    return {"passed": True, "target": report["target"], "base": derivation.base,
            "diff_sha256": derivation.diff_sha256,
            "measurement": report["review_context"]["measurement"],
            "review_context_sha256": report["review_context_sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="exhaustive_mutation.py의 측정 JSON")
    parser.add_argument("--baseline", type=Path, help="생략하면 대상 제품의 기준선")
    parser.add_argument("--base", help="영향 범위를 도출할 커밋(커밋 전 HEAD, 커밋 후 HEAD~1)."
                                       " 주면 --write-template 이 scope 절을 쓰고 --review 가 검사한다")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--review", type=Path, help="검토 결과 JSON으로 완료 게이트 실행")
    action.add_argument("--write-template", type=Path, help="미검토 상태의 검토 초안 생성")
    action.add_argument("--refresh-hashes", type=Path, help="증거·관찰표·보고서 파일 hash 만 현재 값으로 갱신"
                                                          " (기준선 판정 hash 는 그대로 — 내용을 다시 읽은 뒤에 쓴다)")
    action.add_argument("--fill", type=Path, help="검토 초안의 빈 증거·제외 종류·전제 칸을 기준선과 옛 검토 기록으로"
                                                " 채운다(채운 칸은 건드리지 않는다). 아래 --observations·--evidence·"
                                                "--exclusion-from·--premise-lead 와 함께 쓴다")
    parser.add_argument("--observations", help="--fill: 관찰표 경로(저장소 기준)")
    parser.add_argument("--evidence", action="append", default=[], metavar="FUNCTION=PATH",
                        help="--fill: 기준선 ledger 대신 쓸 함수별 증거 경로. 여러 번 줄 수 있다")
    parser.add_argument("--exclusion-from", action="append", default=[], type=Path, metavar="REVIEW",
                        help="--fill: exclusion_kind 를 가져올 옛 검토 JSON. 여러 번 줄 수 있다")
    parser.add_argument("--premise-lead", help="--fill: 조건부 등가의 premise_checked 앞에 붙일 확인 진술."
                                               " 전제를 실제로 확인한 뒤에만 준다")
    parser.add_argument("--stamp", action="store_true",
                        help="--review --base 가 통과하면 검토 JSON 에 gate 절을 쓴다. pre-commit hook 이 이 절을"
                             " 확인한다(test/AGENTS.md \"커밋 hook\")")
    args = parser.parse_args(argv)
    if args.stamp and not (args.review and args.base):
        parser.error("--stamp 는 --review 와 --base 가 함께 필요하다")
    try:
        report = json.loads(args.report.read_text(encoding="utf-8-sig"))
        target = report["target"]
        path = (em.ROOT / target).resolve()
        if not path.is_relative_to(em.ROOT.resolve()) or not path.is_file() or path.suffix != ".c":
            raise ValueError("대상은 저장소 안의 제품 .c여야 한다")
        baseline = em.load_baseline(args.baseline or em.baseline_path(target), target)
        digest = file_hash(args.report)
        derivation = scope_tool.derive(em.ROOT, target, args.base) if args.base else None
        if args.write_template:
            args.write_template.write_text(json.dumps(make_template(report, baseline, digest,
                                                                     derivation),
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
            print("검토 초안 생성 — 관찰표·증거·전제 확인과 scope 해소를 채운 뒤 --review로 검증한다"
                  if derivation else "검토 초안 생성 — 관찰표·증거·전제 확인을 채운 뒤 --review로 검증한다")
            return 0
        if args.fill:
            review = json.loads(args.fill.read_text(encoding="utf-8-sig"))
            overrides = dict(item.split("=", 1) for item in args.evidence)
            sources = [json.loads(p.read_text(encoding="utf-8-sig")) for p in args.exclusion_from]
            changed = fill_review(review, baseline, em.ROOT, args.observations, overrides, sources,
                                  args.premise_lead)
            args.fill.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print("채움 {}칸".format(len(changed)) + " — 이어서 --review 로 검증한다")
            return 0
        if args.refresh_hashes:
            review = json.loads(args.refresh_hashes.read_text(encoding="utf-8-sig"))
            changed = refresh_hashes(review, em.ROOT, digest)
            args.refresh_hashes.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print("갱신: " + (", ".join(changed) if changed else "없음") + " — 이어서 --review 로 검증한다")
            return 0
        review = json.loads(args.review.read_text(encoding="utf-8-sig"))
        method = (report.get("review_context") or {}).get("measurement")
        if method not in em.METHODS:   # 측정 방식을 모르면 현재 입력과 대조할 수 없다 — 다시 측정한다
            raise ValueError("보고서에 측정 방식(review_context.measurement)이 없다: 새 도구로 다시 측정한다")
        context = current_review_context(em.ROOT, target, method)
        safety = em.safety_functions((em.ROOT / "docs/agents/host-test-project.md").read_text(encoding="utf-8")).get(target, set())
        em.check_safety_functions(target, path.read_text(encoding="utf-8"), safety)
        issues = evaluate(report, baseline, review, em.ROOT, digest, context, safety)
        measured = []
        if derivation is not None:
            measured = scope_tool.measured_functions(report.get("functions"), derivation.defined)
            issues += scope_tool.scope_issues(derivation, review.get("scope"), measured)
    except (em.ToolError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("검토 도구 오류: " + str(error), file=sys.stderr)
        return 1
    if issues:
        print("FAIL: 변이 검토 미완료")
        for issue in issues:
            print("- " + issue)
    else:
        print("PASS: 현재 입력·기준선·생존자 검토 증거 일치")
        if args.stamp:
            review["gate"] = gate_stamp(report, derivation)
            args.review.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print("gate 절 기록: " + str(args.review) + " — 이 파일을 커밋에 stage 한다")
    if derivation is not None:
        print(scope_tool.summary(derivation, issues, measured))
    return 2 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
