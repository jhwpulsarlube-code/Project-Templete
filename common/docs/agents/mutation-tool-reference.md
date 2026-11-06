# 변이 도구 참조

`test/tools/exhaustive_mutation.py --help`로 함수/TU/variant·workers·보고서 옵션을 확인한다.
기본은 syntax 스키마타, `--per-mutant`는 변이별 컴파일이다. 빌드 실패는 invalid,
실행 실패와 재현 가능한 timeout은 killed다. 기준선은 읽기 전용이며 생존자 0이 목표는 아니다.
`compare_reports.py`로 동일 입력의 결과를 대조한다.

프로젝트 러너가 CC·CFLAGS·VARIANTS를 제공한다. `test/host-test.project.json`의
`product_pathspec`이 제품 범위를 정한다. 생략 시 C/H/mtpj에서 test와 scratch를 제외한다.
[도구 계약](../../.claude/skills/legacy-host-testing/TOOLING.md)의 fingerprint 계약을 따른다.
완료 게이트 적용/보류는 프로젝트 소유 `docs/agents/host-test-project.md`에서 결정한다.
