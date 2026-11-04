# 현재 워크플로우 자산 전수 목록과 표류 분류

조사일: 2026-11-04. 대상 저장소(읽기만 함, 작업 트리 기준):

| 약칭 | 경로 | 비고 |
|---|---|---|
| O | `C:\Users\ju171g1-home\orca\Optime-LWX1P` | `.claude/` 없음. `docs/agents/` 3개만 있음 |
| LK | `C:\Users\ju171g1-home\orca\Pulsarlube-LK_BTr` | RL78. 커밋 메시지에서 R을 "L13", "R kit"으로 부름 |
| R | `C:\Users\ju171g1-home\orca\Pulsarlube-R` | RL78/L13. `AGENTS.md` 3행 "RL78/L13 (R5F10WLG)"에 근거해 L13 = R |
| T | `C:\Users\ju171g1-home\orca\Pulsarlube-T_N2` | R의 `.scratch/kit-portability/map.md` 7행 "L13 키트를 G16(`…/Pulsarlube-T_N2`)에 도입"에 근거해 G16 = T |
| SK-scr | `C:\Users\ju171g1-home\.claude\skills\scratch-layout` | 사용자 수준 설치 스킬 |
| SK-lht | `C:\Users\ju171g1-home\.claude\skills\legacy-host-testing` | 사용자 수준 설치 스킬(`seeds/` 포함). 파일 mtime은 2026-10-04 |

방법:
- 각 저장소에서 `git ls-files -co --exclude-standard` 결과를 바탕으로 해시 그룹을 만들었다. 원본 바이트의 SHA-1과,
  정규화(BOM 제거, CRLF를 LF로 변환, 줄 끝 공백 제거, 앞뒤 공백 제거)한 뒤의 SHA-1을 비교했다.
- 저장된 blob이 같은지는 `git ls-files -s`로 따로 확인했다.
- 차이 내용은 `diff --strip-trailing-cr`로 읽었다. 이력은 `git log -- <파일>`로 확인했다.
- 해시는 앞 8자리만 적었다. 정규화 해시가 같으면 "공백·EOL만 다름"으로 판정했다.

줄바꿈 환경: 네 저장소 모두 `core.autocrlf=true`이다. 그래서 작업 트리는 CRLF로 보이고 저장된 blob은 LF인 경우가 많다.
T는 `.gitattributes`에 `* -text`가 있어 바이트를 그대로 저장한다. 따라서 T의 blob은 CRLF와 LF가 파일마다 섞여 있다.
LK와 R은 `.githooks/* text eol=lf`를 둔다.

이 문서는 정본을 정하지 않는다. 사실과 분류만 적는다.
"계층 후보"에서 **공통**은 여러 저장소에서 같은 역할로 쓰여 템플릿으로 옮길 수 있다는 뜻이다.
**프로젝트**는 내용이 그 저장소의 제품, 경로 또는 정책에 묶여 있다는 뜻이다.

---

## 1. 요약 표

범례: ● = 있음, — = 없음. "그룹"은 정규화 해시 기준이다. 같은 문자가 붙은 저장소끼리는 정규화 후 내용이 같다.

| 자산 | O | LK | R | T | 설치 스킬 대응 | 그룹 | 분류 요지 | 계층 후보 |
|---|---|---|---|---|---|---|---|---|
| `.claude/hooks/guard_scratch_layout.py` | — | A | A | A(EOL만) | SK-scr `scripts/guard_scratch_layout.py` = A | 1 | **완전 동일**. T 작업 트리만 LF이고 blob은 모두 `94af5420` | 공통 |
| `.claude/hooks/inject_coding_standards.py` | — | A | A | — | — | 1 | 동일 | 공통 |
| `.claude/hooks/guard_main_branch.py` | — | A | B | — | — | 2 | 양쪽이 독립적으로 개선함(§2.1) | 공통 |
| `.claude/hooks/guard_temp_write.py` | — | A | B | — | — | 2 | R에만 도구 소유 Temp 예외가 있음(§2.2) | 공통 |
| `.claude/hooks/block_egress.py` | — | ● | — | — | — | 1 | LK에만 있음 | 프로젝트(소스 유출 정책). 정책을 공통으로 삼으면 공통 |
| `.claude/settings.json` (hook 배선) | — | A | B | C | install.py가 등록 형식을 정의 | 3 | §3 | 공통 골격 + 프로젝트 항목 |
| `.claude/init-machine.py` | — | A | B | C | — | 3 | LK가 R의 기능을 모두 포함하고 더 많음. T는 별도 분기(§2.3) | 공통 골격 + 프로젝트 능력 |
| `.claude/toolchain.local.example.json` | — | A | A | — | — | 1 | 동일 | 공통 |
| `.claude/machine.json` | — | ●(무시됨) | — | — | — | — | 머신 로컬 산출물이라 자산이 아님 | — |
| `.claude/skills/renesas-rl78/SKILL.md` | — | A | A | B | — | 2 | T가 R의 10-15 교정보다 이전 판(§2.4) | 공통(RL78 계열) |
| `…/renesas-rl78/UNVERIFIED.md` | — | — | A | B | — | 2 | R이 앞섬. T는 10-09 사본 | 공통(RL78) |
| `…/renesas-rl78/isr-and-atomicity.md` | — | A | B | C | — | 3 | R이 앞섬. LK는 R 판을 이 저장소에 맞게 고쳤고, T는 옛 판 | 공통 본문 + 프로젝트 국소화 |
| `…/renesas-rl78/measurement.md` | — | A | B | B(EOL만) | — | 2 | R=T는 EOL만 다름. LK는 임계구역 예시를 국소화함 | 공통 |
| `…/renesas-rl78/setup.md` | — | A | B | C | — | 3 | R이 앞섬. LK는 한 줄 국소화, T는 옛 판 | 공통 |
| `…/renesas-rl78/toolchain-traps.md` | — | A | B | C | — | 3 | R이 앞섬(재측정 반영). LK는 "출처 저장소에서 잼"으로 국소화, T는 옛 판 | 공통 |
| `.githooks/pre-commit` | — | A | A | B | — | 2 | LK=R 동일. T는 `verify_eol.py`와 `py` 런처가 추가된 변형 | 공통 골격 + 프로젝트 |
| `.githooks/post-commit` | — | — | — | ● | — | 1 | T에만 있음. 게이트 결과를 git note로 기록 | 프로젝트(T 도구에 의존) |
| `tools/issue-status.ps1` | — | A | A | B | — | 2 | LK=R(blob `479ec5dc`). T는 이전 판(§2.5) | 공통 |
| `docs/agents/domain.md` | A | B | C | D | — | 4 | 골격은 같고 세부는 프로젝트 내용 | 공통 골격 + 프로젝트 |
| `docs/agents/issue-tracker.md` | A(GitHub) | B | C | D | — | 4 | O만 GitHub Issues. 셋은 로컬 마크다운 | 공통(트래커 종류별 2종) |
| `docs/agents/triage-labels.md` | A | B | C | D | — | 4 | LK와 R은 거의 같음(Wayfinder 값 문구만 다름). T와 O는 축약된 한국어판 | 공통 |
| `docs/agents/scratch-layout.md` | — | A | B | C | SK-scr `references/layout-table.md` = D | 4 | T는 설치 템플릿을 `{root}=.scratch`로 렌더링한 것과 거의 같음. R과 LK는 확장함(§2.6) | 공통 |
| `docs/agents/host-test-workflow.md` | — | A | B | — | SK-lht `seeds/workflow.md` = C | 3 | R이 크게 앞섬. LK는 ADR 0005로 갈라짐(§2.7) | 공통 골격 + 프로젝트 |
| `docs/agents/mutation-tool-reference.md` | — | A | B | C | — | 3 | 세 저장소가 크게 갈라짐 | 공통 골격 + 프로젝트 |
| `docs/agents/templates/host-test-ticket.md` | — | A | B | — | SK-lht `seeds/ticket.md` = C | 3 | LK와 R은 6~7줄 차이. 시드는 일반화된 축약판 | 공통 |
| `docs/agents/templates/host-test-observations.md` | — | — | — | — | SK-lht `seeds/observations.md` | — | 어느 저장소에도 설치되지 않음 | 공통 |
| `docs/agents/review-host-tests.md` | — | — | — | — | SK-lht `seeds/review.md` | — | 어느 저장소에도 설치되지 않음 | 공통 |
| `docs/agents/code-review-codex.md` | — | A | B | — | — | 2 | LK가 크게 확장함(두 축 병렬 워커, 유출 규칙) | 공통 골격 + 프로젝트 |
| `docs/agents/codex-review.md` | — | — | — | ● | — | 1 | 같은 역할을 하는 T 판(다른 파일명) | 공통 후보(이름 표류) |
| `docs/agents/code-review-smells.md` | — | A | B | — | — | 2 | 우연한 차이(표기, 도구 이름 1개) | 공통 |
| `docs/agents/pull-request.md` | — | A | B | — | — | 2 | 양쪽에 각자만의 개선이 있음(§2.8) | 공통 |
| `docs/agents/nightly-e2e-check.md` | — | A | B | — | — | 2 | LK는 "주간"으로 이식하며 국소화함 | 프로젝트 |
| `docs/agents/nightly-schemata-check.md` | — | A | B | — | — | 2 | 위와 같음 | 프로젝트 |
| `docs/agents/temp-hygiene.md` | — | A(9줄) | B(75줄) | — | — | 2 | R이 크게 앞섬. hook 동작도 서로 다름(§2.2) | 공통 |
| `docs/agents/red-proof-reference.md` | — | — | — | ● | — | 1 | T에만 있음(G16 명세를 참조함) | 프로젝트. 골격은 공통 후보 |
| `docs/agents/ticket-closing.md` | — | — | — | ● | — | 1 | T에만 있음(kit-portability 실행 티켓용) | 프로젝트 |
| `docs/agents/README.md`, `claude-code.md`, `source-egress.md` | — | ● | — | — | — | 1 | LK에만 있음(문서 안내, 유출 정책) | README는 공통 후보. 나머지는 프로젝트 정책 |
| `docs/agents/templates/release-note.md` | — | — | ● | — | — | 1 | R에만 있음(제품명이 박혀 있음) | 프로젝트 |
| `test/tools/exhaustive_mutation.py` 외 시드 도구 | — | A | B | C | SK-lht `seeds/tools/*` = D | 4 | 시드는 R의 2026-10-04 무렵 판을 일반화한 것(§2.9) | 공통 |
| `test/AGENTS.md` (시드 `rules.md`의 설치 위치) | — | A(404줄) | B(260줄) | C(116줄) | `seeds/rules.md` 23줄 | 4 | 모두 프로젝트 본문. 시드와 겹치는 줄은 9/23 | 프로젝트(범위 밖, 대응 관계만 적음) |

---

## 2. 실제 차이의 요지와 분류

### 2.1 `guard_main_branch.py` — 양쪽에 각자만의 개선이 있음

이력:
- LK: 10-16 `e7b9d76` "import the three Claude Code hooks from L13"으로 R에서 가져옴 → 10-16 `092d7d2` "see quoted refs" → 10-23 `f987507`.
- R: 10-11 `c6323c1` 생성 → 10-28 `635e4d5` "PowerShell 도구의 git 호출도 막는다" → 10-31 `d553caa` "git -C·cd 대상 저장소의 브랜치를 본다".
- 두 저장소가 R에서 가져온 뒤 **각자 독립적으로** PowerShell 지원을 넣었다.

R에만 있는 것:
- `git -C <경로>`와 앞선 `cd`/`Set-Location`/`pushd`의 대상 저장소 브랜치로 판정한다(`target_branch`, `branch_in`).
- 위 판정을 테스트에서 주입할 수 있다.

LK에만 있는 것:
- `@"…"@` here-string 안의 `$(…)`를 코드로 남긴다(`here_string_code`).
- 공백 없는 따옴표 인자(`"main"`, `'HEAD:main'`)는 따옴표만 벗겨 ref로 읽는다(`unquote`).
- R은 따옴표 안 문자열을 모두 `""`로 지운다. 그래서 `git push origin "main"`을 놓칠 수 있다(코드 독해로 얻은 추론).

분류: **양쪽에 각자만의 개선이 있음**. 계층 후보: 공통.

### 2.2 `guard_temp_write.py`와 `temp-hygiene.md` — R이 앞섬. 동작 차이는 의도로 보임

- R(10-31 `a63317c`, 커밋 1개)은 처음부터 `%TEMP%\claude`, `codex`, `codex-artifact-session-*`를 통과시킨다.
- LK(11-01 `d15c7db`)에는 이 예외가 없다.
- LK `temp-hygiene.md`는 "도구 관리 하위 폴더를 포함해 차단한다"고 명시한다.
  따라서 LK 쪽 차이는 우연이라기보다 **프로젝트 고유 선택**으로 보인다. 다만 근거 커밋 메시지는 없다.
- 문서 분량은 R 75줄(도구의 임시 위치, 격리 사본, 잔재 정리 절), LK 9줄이다. 문서 면에서는 **R이 앞선다**.

### 2.3 `init-machine.py` — LK가 R의 기능을 모두 포함하고 더 많음. T는 별도 분기

LK:
- 10-17 `c3d2cc9` "copy init-machine … from L13 as is"로 R에서 가져왔다.
- 이후 CubeSuite 탐지, post-link 의존성, `can_run_post_link`, egress hook 배선 검사(`egress_hook_wired`, `egress_errors`)를 더했다.
- R에만 있는 줄은 프로젝트 파일명(`Pulsarlube_R.mtpj`)과 `report_nightly`뿐이다. LK에서는 `report_errors`로 일반화돼 있다.
- 분류: **LK가 앞섬**(R 판을 포함함). LK 판의 일부는 프로젝트 고유 내용이다.

T:
- 10-15 `82f4a90`로 L13 판을 그대로 가져온 뒤, 10-15 `4727de9`에서 G16에 맞췄다.
- nightly와 cppcheck 역할이 없다.
- 대신 백신이 새로 빌드한 실행 파일을 막는지 진단하는 기능(`launch_problem`, 10-16 `7c4ddf5`)과 `can_run_misra_gate`가 **T에만** 있다.

세 판의 공통 골격:
- `install_hooks`: `core.hooksPath=.githooks`. 다른 값이 이미 설정돼 있으면 건드리지 않는다.
- `toolchain.local.json` 조회.
- `machine.json`의 `capabilities` 기록.

관찰 사실: T 체크아웃의 `.git/config`에는 `core.hooksPath=tools/hooks`가 설정돼 있지만 `tools/hooks`는 존재하지 않는다.
`install_hooks`는 이미 다른 값이 있으면 바꾸지 않으므로, **현재 T 체크아웃에서는 `.githooks/pre-commit`과 `post-commit`이 실행되지 않는 상태**다.

### 2.4 `renesas-rl78` 스킬 — R이 원본이고 앞섬. T는 옛 사본, LK는 국소화된 사본

이력:
- R: 10-07 `adf7a1d` "전역에서 저장소로 옮긴다" → 10-15 `16a3d50`과 `36293ed` "이 저장소 코드에 없는 심볼에 걸린 규칙을 UNVERIFIED로 옮김, 재측정으로 CC-RL 주장 교정" → 10-16 `37af3c5` "V1.16.00에서도 같음".
- T: 10-09 `3fdce36` 한 번뿐이다. R의 교정 이전 판이다.
  - T에 남은 내용: `OS_CRITICAL_EXIT` 행, `OS_NEAR` 행, `src/core|product|target` 트리, `LENIENT_DIAG` 예시.
  - R의 `UNVERIFIED.md`에 따르면 이 항목들은 "G16 코드에서도 0건"이다.
- LK: 10-16 `3a12eff`로 R의 최신 판을 가져왔다. `137b64a`에서 `application/serial_cs.h` 임계구역 규칙, "출처 저장소에서 V1.14로 잼, 여기서는 다시 재지 않음" 같은 국소 문구로 바꿨다.

분류:
- R과 T의 차이는 **R이 앞섬**. T는 이식 전 사본이다.
- R과 LK의 차이는 **프로젝트 고유 내용**(국소화)이다.
- `measurement.md`는 R과 T가 정규화 후 동일하다. blob은 다르지만 T가 CRLF로 저장한 차이일 뿐이다.

### 2.5 `tools/issue-status.ps1` — LK=R. T는 이전 판

- LK 10-26 `cda86d5` "hide closed tickets unless -All"은 R 10-26 `006dae7`과 같은 날 같은 내용이다. 저장된 blob이 동일한 `479ec5dc`이므로 **R에서 이식**한 것이다.
- T(10-09, 커밋 1개)에는 `-All`이 없다. 상태 값 `open`, `resolved`, `split`을 더 받는다.
- R은 10-15 `4221915`에서 Status를 6값으로 정리했다. 따라서 T 판은 그 이전의 어휘다.
- 분류: **R/LK가 앞섬**.

### 2.6 `scratch-layout.md`와 설치 스킬

- SK-scr의 파일 5개는 R `tools/skills/scratch-layout/`과 정규화 후 **모두 동일**하다(R 10-09 `a9d65f4`). R 쪽에는 `tests/test_scratch_layout.py`가 더 있다.
- 따라서 R이 설치 스킬의 원본 저장소다.
- T의 문서는 템플릿을 `{root}=.scratch`로 렌더링한 결과와 같다. 제목 수준(`###`→`#`) 1곳만 다르다. 10-09 `6c74361`의 설치 결과로 보인다.
- R 문서에만 있는 것: `.learning/` 행, `notes/` 무시 규칙, 원본 도구 경로 안내.
- LK 문서(10-23 `d9dbdfa`)에만 있는 것:
  - `measure/mutation/` 전수 보고서 행.
  - "이 저장소 `.gitignore`에는 notes 무시 규칙이 없다"(프로젝트 사실).
  - 기존 파일 예시.
- 분류:
  - 템플릿과 T의 차이: 우연한 차이(제목 수준).
  - R과 LK의 차이: 양쪽에 각자만의 확장이 있음. LK의 `measure/mutation` 행은 공통으로 쓸 수 있는 개선 후보다.

### 2.7 `host-test-workflow.md` — R이 크게 앞섬. LK는 ADR로 갈라짐

- R은 커밋 26개, 마지막은 11-03 `002436c`이다. LK는 커밋 3개, 10-17에 멈췄다.
- R의 §4 "완료 게이트"는 약 58줄이고 `check_mutation_review` 검토 JSON 게이트를 쓴다.
- LK의 §4 "완료 판정"은 6줄이다. 이유는 "[ADR 0005] 검토 JSON·완료 게이트 계층 보류"이고, `CODING_STANDARDS.md` §6과 `--function` 종료 코드 0으로 판정한다.
- LK에는 `check_mutation_review.py`가 없다.
- 시드 `workflow.md`(52줄)는 두 문서와 13~14줄만 겹치는 일반화 요약이다.
- 분류:
  - 게이트 구성은 **R이 앞섬**. 단 LK가 따르지 않는 이유는 프로젝트 ADR에 있다(**프로젝트 고유 선택**).
  - LK에만 있는 "도구 전용 티켓의 아카이브 기준선 절차" 등은 **LK에만 있는 개선**이다.

### 2.8 `pull-request.md` — 양쪽에 각자만의 개선이 있음

R에만 있는 것(11-04 `c31bef0`):
- "CI가 못 하는 로컬 검사만" 돌리는 구조.
- CS+ 빌드 필수, `precommit_gate --full` 권장.
- branch protection이 없다는 설명.

LK에만 있는 것(11-02 `dce9033`):
- push 전에 커밋 범위를 사용자에게 보여 준다.
- 본문을 stdin으로 넘기고, 제목과 본문에 제품명·칩명을 쓰지 않는다(유출 정책).
- 이슈에 `브랜치:` 줄을 적는다.
- `--force-with-lease`만 허용한다.

### 2.9 시드 도구(`seeds/tools/*`)와 `test/tools/*`

- 시드 `exhaustive_mutation.py`는 R의 2026-10-04 판(`1d75b44`)과 diff 55줄 차이다.
  차이는 일반화 수정이다: `PRODUCT_PATHSPEC` 일반화, ADR 번호 제거, `review_context` 추가, `##`/`###` 안전 경로 표 허용.
- 정규화 후 시드와 겹치는 줄 수: R 1033/1339, LK 1005, T 1004. 세 저장소 모두 이후 수십 커밋씩 진행했다.
  - R: 28개, 10-27까지.
  - T: 8개. G16 러너, 백신 진단을 반영했다.
  - LK: 3개. P3 이식분이다.
- `check_mutation_review.py`와 `test_mutation_review.py`는 R에만 있다. 시드는 R 판의 축약 출발점이다(겹치는 줄 150/165, 120/122).
- 시드 `exhaustive_mutation.py`와 `test_exhaustive_mutation.py`는 **한 파일 안에 CRLF와 LF가 섞여 있다**(1281/1339줄, 581/640줄이 CRLF).
- 분류: 시드는 **모든 저장소보다 뒤처짐**. 저장소끼리는 각자 앞선 부분이 있다.

### 2.10 그 밖의 소규모 차이

- `triage-labels.md` LK와 R: "The five roles"→"first four roles", Wayfinder 값에 `open` 추가. **우연한 차이**에 가깝다.
- T `issue-tracker.md`는 Wayfinder 담당과 완료를 `Status: claimed`/`resolved`로 적는다.
  R과 LK는 별도 `Wayfinder:` 줄을 쓰고 `Status:`에 넣지 말라고 한다. T가 **이전 규약**이다.
- `issue-tracker.md` R과 LK:
  - R에만 `notes/` 무시 문구, 중첩 `.scratch/.scratch/` 경고, host-test 티켓 절이 있다.
  - LK에만 "트래커를 커밋에 두는 이유(worktree 간 일치)" 문단이 있다.
- `domain.md`:
  - R, LK, T는 `CONTEXT.md`를 쓴다. **O는 `GLOSSARY.md`**를 쓴다(project-templete 작업 트리에도 미추적 `GLOSSARY.md`가 있다).
  - R과 LK의 차이는 "기존 설계 기록" 단락뿐이다(프로젝트 고유 내용).
- `code-review-smells.md`: "코덱스"→"codex" 표기, 도구 목록에 MISRA 추가. **우연한 차이**와 프로젝트 국소화다.
- `nightly-*.md`: LK는 10-26에 L13 판을 "주간"으로 이식하며 같은 머신의 L13 점검과 겹치지 않게 국소화했다. 이후 R이 10-30과 11-03에 더 진행했다. **프로젝트 고유 내용 + R이 앞섬**.
- hook 단위 테스트(`test/tools/test_guard_*.py`, `test_hook_wiring.py`, `test_inject_coding_standards.py`, `test_init_machine.py`)도 LK와 R 사이에 모두 해시가 다르다. 각 hook의 차이를 따라간 것으로 보인다(세부는 확인하지 않음).

---

## 3. `settings.json`의 hook 배선 차이

| 이벤트 / matcher | LK | R | T |
|---|---|---|---|
| PreToolUse / `Skill` → `inject_coding_standards.py` | ● | ● | — |
| UserPromptSubmit → `inject_coding_standards.py` | ● | ● | — |
| PreToolUse / `Write` → `guard_scratch_layout.py` (인자 없음 = 루트 `.scratch`) | ● | ● | ● |
| PreToolUse / `Write` → `guard_temp_write.py` | ● | ● | — |
| PreToolUse → `guard_main_branch.py` | matcher `Bash\|PowerShell` 1개 | `Bash`, `PowerShell` 항목 2개 | — |
| PreToolUse / `Bash\|PowerShell` → `block_egress.py` | ● | — | — |
| Stop → 인라인 `git diff --stat -- '*.c' '*.h'` 변이 오염 경고 | — | — | ● |
| `permissions.allow` | `run_tests.py` 2개 | `git cherry *` + `run_tests.py` 2개 | 없음 |
| `permissions.deny` | 54개(유출 차단) | 없음 | 없음 |
| `disableBypassPermissionsMode`, `disableClaudeAiConnectors`, `disableRemoteControl`, `disableArtifact` | ● | — | — |

- 모든 hook은 `python -B "$CLAUDE_PROJECT_DIR/.claude/hooks/<이름>"` 형식이며 `timeout: 10`이다.
  이 형식은 SK-scr `install.py`가 쓰는 등록 형식과 같다.
- `guard_main_branch` matcher가 1개냐 2개냐는 동작이 같은 **우연한 차이**다.
- R의 `settings.local.json`(로컬 Temp 조회 허용 1개)은 머신 로컬 파일이므로 자산에서 제외했다.

---

## 4. 공백·EOL·BOM만 다른 쌍(정규화 후 동일)

| 자산 | 다른 쪽 | 성격 |
|---|---|---|
| `.claude/hooks/guard_scratch_layout.py` | T(작업 트리 LF) 대 LK·R·SK-scr(CRLF) | blob은 네 곳 모두 `94af5420`로 동일하다. 체크아웃 설정 차이일 뿐이다 |
| `.claude/skills/renesas-rl78/measurement.md` | R 대 T | 작업 트리 바이트는 같다. blob만 다르다(R은 LF 저장, T는 `-text`라 CRLF 저장) |
| (참고, 범위 밖) `test/tools/int16_canary.c` | R(CRLF) 대 T(LF) | 정규화 후 동일 |

이 밖의 쌍은 원본 해시가 다르면 정규화 해시도 달랐다. 즉 공백만 다른 "가짜 표류"는 위 경우뿐이다.

원본 바이트까지 같은 그룹:
- `inject_coding_standards.py`, `toolchain.local.example.json`, `.githooks/pre-commit`: LK=R.
- `issue-status.ps1`: LK=R(BOM 포함).
- `renesas-rl78/SKILL.md`: LK=R.
- `guard_scratch_layout.py`: LK=R=SK-scr.

---

## 5. 한쪽에만 있는 자산

- **LK만**: `block_egress.py`, `docs/agents/README.md`, `claude-code.md`, `source-egress.md`, settings의 deny와 기능 비활성 플래그.
- **R만**: `renesas-rl78/UNVERIFIED.md`(T에는 옛 판 있음), `templates/release-note.md`, `check_mutation_review.py`·`test_mutation_review.py`, `tools/skills/scratch-layout/`(설치 스킬 원본).
- **T만**: `.githooks/post-commit`, Stop hook, `codex-review.md`(R/LK `code-review-codex.md`에 해당), `red-proof-reference.md`, `ticket-closing.md`.
- **O**: `docs/agents/` 3개(`domain.md`, `issue-tracker.md`, `triage-labels.md`)만 있다. 10-28 `ff910ba` 한 커밋이며 GitHub Issues 트래커를 쓴다. `.claude/`, `.githooks/`, `tools/issue-status.ps1`이 없다.
- **설치 스킬만**(어느 저장소에도 설치되지 않음): `seeds/observations.md`→`templates/host-test-observations.md`, `seeds/review.md`→`review-host-tests.md`. SETUP.md에 대응 경로가 있다.

---

## 6. "R에 맞추기" 흔적

커밋 메시지에서 확인한 이식(L13 = R):
- **LK**: 10-16~10-26에 hook 3종, init-machine, toolchain 예시, precommit_gate, stack_depth, int16 canary, nightly 3종, codex 리뷰 절차를 "from L13 as is" 또는 "from the R kit"으로 가져왔다.
  이후 LK에서 고친 파일은 대부분 국소화이거나 독자적인 개선이다.
  `issue-status.ps1`은 10-26 같은 날 R과 같은 blob이 됐다.
- **T**: 10-15에 mutation 도구, red-proof, commit-gate 도구를 "import L13 … verbatim"으로 가져온 뒤 바로 "adapt … to G16" 커밋으로 고쳤다.
  반면 T의 `.claude/`(renesas-rl78, guard_scratch_layout, settings)와 `issue-status.ps1`, `docs/agents/*`는 10-09 이후 R을 따라가지 않았다.
- 결과적으로 이식 시점 이후 **R이 계속 앞서간 자산**이 있다:
  renesas-rl78 교정, host-test-workflow 게이트, temp-hygiene, pull-request CI 구조, nightly 확장.
- 동시에 **이식받은 쪽이 독자적으로 앞선 자산**도 있다:
  LK의 init-machine과 guard_main_branch 따옴표 처리, T의 백신 진단과 post-commit 게이트 노트.
