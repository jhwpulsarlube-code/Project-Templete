# 호스트 테스트 워크플로우

새 TU 구축, 기존 TU·fake·시임 확장, 제품 동작 변경 전에
[test 규칙](../../test/AGENTS.md)과 프로젝트 소유 `docs/agents/host-test-project.md`를 읽는다.
프로젝트 문서에서 제품 범위·안전 경로·variant·빌드·완료 게이트 적용 여부를 확인한다.

1. [관찰표](templates/host-test-observations.md)에 입력·이전 상태·출력·후속 실행을 연결한다.
   유지/부재는 additive, 쓰기/전이는 inert 또는 인자 교체로 반증한다.
2. 프로젝트 러너 `python test/run_tests.py <TU>`로 원형 제품 소스를 빌드한다.
   플래그·variant는 러너 한 곳에서 제공한다. 호스트 한계를 기록한다.
3. `python -B test/tools/exhaustive_mutation.py <제품.c> --json <보고서>`로 측정한다.
   기준선은 읽기 전용이다. 현재 입력의 양·음성 probe와 fingerprint를 확인한다.
   동시 측정은 CPU 예산을 나눠 쓰고 측정 도중 입력을 바꾸지 않는다.
4. 도달 가능한 비등가는 테스트·시임으로 검출한다. 등가·조건부 등가·제외는 현재 근거를 연결한다.
   완료 게이트 적용 프로젝트는 `check_mutation_review.py`로 증거·현재 입력을 대조한다.
   보류 프로젝트는 프로젝트 ADR의 대체 완료 조건을 적용한다.
5. 리뷰 반영 뒤 최종 측정·red 증명·적용 프로젝트의 stamp를 갱신한다.
   전체 호스트 검증과 타깃 확인 결과를 티켓에 기록한다. 미완료 실기는 사람 작업으로 남긴다.

[도구 계약](../../.claude/skills/legacy-host-testing/TOOLING.md),
[도구 참조](mutation-tool-reference.md), [리뷰 기준](review-host-tests.md)을 따른다.
격리 사본은 저장소 밖에 두고, 일회성 출력과 증거는 [임시 파일 안내](temp-hygiene.md)를 따른다.
