# 도구 이식 계약

기존 도구가 아래 계약을 만족하면 그대로 사용한다. 공통 도구는 배포된 `test/tools/`를 사용한다. 시드는 C/GCC 단일 TU용 출발점이다.
다른 언어·컴파일러는 같은 증거 계약으로 구현한다.

## 시드의 프로젝트 결합부

- test/run_tests.py는 import 시 실행하지 않으며 CC, CFLAGS, VARIANTS(name, extra)를 노출한다.
  기본 테스트 경로는 test/test_*.c, 제품을 include하는 단일 TU다. 다른 링크/구성은
  load_runner·select_configs·run_config를 실제 러너에 맞춘다.
- ROOT·PRODUCT_PATHSPEC·격리 사본과 untracked 복사 범위를 제품/하네스 경로에 맞춘다.
  공백 경로·중첩 디렉터리·외부 의존성을 포함한 fixture로 의존성 파싱과 fingerprint를 검증한다.
- 안전 경로는 docs/agents/host-test-project.md의 안전 경로 절 아래 파일/함수 표에서 읽는다.
  위치를 바꾸면 측정기와 완료 게이트 reader를 함께 바꾼다.
- 기준선/report schema와 검토 도구를 함께 이식한다. 새 adapter와 설정 파일도 fingerprint에 넣는다.
  제품 예시·ADR 번호·제품 경로를 프로젝트에 맞추고 실제 제품 E2E를 선정 모듈에 추가한다.
- fixture 테스트와 test_mutation_review.py를 함께 설치한다. 저장소 도구가 설치 후 정본이다.

## 완료 계약

| 도구 | 검증할 동작 |
|---|---|
| 러너 | 제품을 원형 그대로 빌드. TU/variant/플래그의 정본. 격리 사본의 #error는 build failure, 실패 단정은 run failure. -Wall -Wextra -Werror와 근거 있는 좁은 억제 |
| 시임 조사 | 실제 플래그의 전처리 결과·의존성과 nm -u로 SFR/외부 심볼 측정 |
| 전수 측정 | 안정 키, 함수/TU/variant 범위, inactive/out-of-scope, killed/survived/invalid 보존. 빌드 실패 invalid. 실행 timeout은 재현 가능한 실행 제한으로 killed 판정하고 이유 기록. 매 실행 양·음성 probe. 기준선 읽기 전용 |
| fingerprint | 제품·헤더·테스트·runner/adapter·측정기·설정 파일 내용, 실제 빌드 인자·컴파일러·선정 구성. 격리 사본과 원본 경로 정규화 |
| 완료 게이트 | 현재 fingerprint/report hash/기준선 항목/관찰표/키별 근거 대조. 조건부 현재 전제, 제외 범위, 안전 경로 요구. 새 생존자·미검토·사라짐·입력/증거 변경·probe 실패는 nonzero |
| red 증명 | 부모 제품 + 새 테스트를 격리 실행. 관련 단정만 실패하면 PROVEN. 빌드 실패·무관한 실패·주석 변경은 NOT PROVEN |

도구 테스트와 fixture CLI를 검증한다. 빈 초안은 실패, 근거를 채운 검토는 통과,
헤더·테스트·설정·새 TU/variant 변경 뒤 기존 검토는 실패해야 한다.
직렬/병렬의 변이 결과·범위·probe·fingerprint가 동일해야 한다.
의미상 등가는 사람이 근거를 리뷰한다. 공통 도구 갱신은 배포 흐름을 따른다.
