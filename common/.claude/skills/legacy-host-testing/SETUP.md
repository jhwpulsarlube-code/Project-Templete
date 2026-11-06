# 프로젝트 준비

공통 자산은 원본 저장소의 배포 흐름으로 받는다. 도구는 `test/tools/`, 규칙은
`test/AGENTS.md`, 절차는 `docs/agents/host-test-workflow.md`에 한 벌로 둔다.

1. 프로젝트 루트 규칙·tracker·ADR과 기존 러너·시임을 읽는다.
2. 프로젝트 소유 `test/run_tests.py`의 CC, CFLAGS, VARIANTS(name, extra)와 import 시 실행하지 않는 계약을 확인한다.
3. `test/host-test.project.json`의 `product_pathspec`에 제품 범위와 테스트 제외를 적는다.
4. 프로젝트 소유 `docs/agents/host-test-project.md`에 안전 경로·variant·타깃 빌드·완료 게이트 적용/보류 결정을 둔다.
5. 루트 규칙에서 공통 test 규칙과 절차를 변경 전에 읽게 연결한다.
6. 작은 격리 fixture로 빌드/실행 실패 구별, 양·음성 probe, 직렬/병렬 결과, 입력·증거 변경 거부와 red 증명을 검증한다.

완료: 배포본과 프로젝트 입력이 준비되고 알려진 답 검증이 통과했다.
프로젝트 설정 기록에 실제 명령·결과와 미완료 항목을 남긴다. 제품 테스트가 없으면 준비 중으로 기록한다.
