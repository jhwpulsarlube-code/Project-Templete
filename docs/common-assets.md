# 공통 정본과 전환 입력

이슈 #20은 R을 기본으로 `.claude/hooks`, RL78 스킬과 임시 파일 안내를 수집했다.
브랜치 보호는 R의 대상 저장소 판정과 LK의 here-string 실행식·quoted ref 처리를 합쳤다.
PR 안내는 R의 로컬/CI 증거 구분과 LK의 전송 범위·PowerShell·lease·브랜치 기록을 합쳤다.
R/LK의 외부 전송 정책이나 로컬 tracker 선택을 공통 정책으로 만들지 않는다.

정본은 `common/` 아래 배포 경로 그대로 한 벌이다. `.claude/skills/`의 프로젝트 스킬은
배포된 `docs/agents/`, `test/tools/`와 hook을 참조한다. 설치 스크립트와 seed 사본은 배포하지 않는다.
`test/run_tests.py`, 제품 테스트·fake·기준선·안전 경로, `build_report.py`의 release 연결,
nightly 대상 선택과 CI·release·코딩 표준은 프로젝트 소유로 남는다.
재사용 가능한 변이·검토·red·격리·결과 비교·인코딩·링크·assembly 도구는 공통으로 배포한다.

## 소비 프로젝트에서 준비할 확장 입력

| 입력 | 전환할 내용 |
| --- | --- |
| `.claude/temp-write.project.json` | R의 예외를 `{"allow_temp_top_level":["claude","codex","codex-artifact-session-*"]}`로 보존한다. 생략 시 예외 없음. |
| `.claude/settings.project.json` | MISRA·egress hook의 명령과 권한 정책. 공통 설정과 compose한다. |
| 프로젝트 소유 hook 파일 | LK/Optime의 `block_egress.py`, MISRA 구현을 유지한다. 공통 hook 목록에는 넣지 않는다. |
| `.claude/machine_checks.py` | 기존 머신 확장 계약으로 egress 정책·배선 검사를 연결한다. |
| `.githooks/pre-commit.d/` | 기존 프로젝트별 검사·완료 게이트를 sh 확장으로 연결한다. |
| `docs/agents/host-test-project.md` | 제품 범위·안전 경로 표·빌드·variant·프로젝트 ADR. LK는 ADR 0005의 완료 게이트/범위 도출/stamp 보류와 대체 완료 조건을 보존한다. |
| `test/host-test.project.json` | `product_pathspec` 배열과 선택적 `extra_includes` 배열. R의 기존 경로와 cryptography include를 옮긴다. |
| `docs/agents/workflow-project.md` | tracker 종류·저장소와 프로젝트 외부 도구 인자 정책. |
| 루트 `AGENTS.md`, `.gitignore` | 공통 안내 포인터와 임시 출력 ignore를 프로젝트가 소유한다. |

공통 문서의 프로젝트 문서 경로는 의도된 확장 지점이다. 배포 전에 소비 프로젝트에서 준비한다.
현재 작업은 정본 수집이며 소비 프로젝트 파일을 수정하거나 삭제하지 않는다.
R의 기존 scratch-layout 자산은 R 전환 검증 후, 사용자 레벨 두 스킬은 네 프로젝트 전환 후 정리한다.

## 검증

`python -B -m unittest discover -s tests`는 hook 대표 입력/출력, 프로젝트 temp 예외,
스킬 링크와 `core.autocrlf=true` 체크아웃 후 바이트 일치를 검사한다.
혼합 디렉터리 속성은 배포 파일명에만 적용하고 소비 프로젝트 루트 속성은 유지한다.

프로젝트 안전 경로 문서는 `### 안전 경로` 절의 파일/함수 Markdown 표를 제공한다.
측정할 안전 경로가 없으면 빈 표를 명시한다. 선택적 `#### 측정 불가 안전 경로` 표도 같은 문서에 둔다.
