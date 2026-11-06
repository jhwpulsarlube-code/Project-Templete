# 저장소 작업 임시 파일

Codex·Claude가 이 저장소 작업을 위해 직접 만드는 일회성 중간 파일과 저장소 스크립트의 새 임시 출력은 저장소 루트의 `.repo-temp/` 아래에 둔다. 이 폴더는 Git에서 무시한다. 실행마다 고유한 하위 폴더를 만들고 성공한 실행의 일회성 출력은 제거한다. 실패 시 진단 파일은 남기고 위치를 보고한다.

```powershell
$run = Join-Path (git rev-parse --show-toplevel) ".repo-temp/<목적>-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory $run | Out-Null
try {
    # … 작업 …
    Remove-Item -Recurse -Force $run      # 성공하면 제거한다
} catch {
    "$(Get-Date -Format s) $($_.Exception.Message)" | Set-Content "$run/FAILED.txt"   # 실패하면 남기고 이유를 적는다
    throw
}
```

`FAILED.txt` 가 있는 폴더는 진단이 끝나면 바로, 늦어도 14일 뒤에 지운다. `.repo-temp/` 는 무시되므로 쌓여도 눈에 띄지 않는다. 작업을 시작할 때 오래된 `FAILED.txt` 폴더가 있으면 함께 정리한다.

## 도구의 임시 위치

2026-11-03 점검 결과다. 새 임시 출력은 `.repo-temp/` 에 두되, 저장소 밖이어야 뜻이 있는 것은 두지 않는다.

| 위치 | 대상 | 이유 |
| --- | --- | --- |
| `.repo-temp/` | `test/run_tests.py`(`hosttest/`), `codex_review.py` 의 기본 `gates.txt`(`CLAUDE_JOB_DIR` 가 없을 때), `docs/ux/tools/verify_document.py`, 재생 도구(`.scratch/pump-run-log-analysis/scripts/replay_merges.py`), 단위 테스트 픽스처(`test/tools/repo_temp.py` → `.repo-temp/unittest/`) | 일회성 산출물 |
| 저장소 밖 (OS 임시 폴더) | `exhaustive_mutation.py`·`red_proof.py`·`invalid_cause.py`·`check_pp_identity.py`·`verification_snapshot.py`·`tools/release.py` 의 격리 사본 | 사본이 저장소 안에 있으면 사본이 `.repo-temp/` 를 다시 품고, 제품 diff 해시·`gcc -MM` 범위가 흔들리며, 측정 중 작업 트리에 쓰면 측정이 무효가 된다. 문서도 "저장소 밖 경로"를 요구한다 |
| `%LOCALAPPDATA%` | `nightly_check.py`·`nightly_e2e.py` | 임시 파일이 아니라 야간 검사의 지속 상태 |

격리 사본 도구를 시험하는 `test_exhaustive_mutation.py`·`test_red_proof.py`·`test_verification_snapshot.py` 는 `tempfile.tempdir` 를
픽스처로 돌려 도구가 만드는 worktree 가 저장소 밖에 생기는지 본다. 이 픽스처를 `.repo-temp/` 로 옮기면 worktree 가 저장소 안에 생겨
오류가 났으므로(2026-11-03 측정) 옮기지 않는다. `.scratch/` 의 옛 증거 스크립트는 동결된 기록이라 고치지 않는다.

단위 테스트 픽스처는 `repo_temp.TemporaryDirectory()` 를 쓴다. `tempfile.tempdir` 를 전역으로 바꾸지 않는다 — 같은 프로세스의
격리 사본 도구가 저장소 안에 사본을 만든다. 이 함수는 `GIT_CEILING_DIRECTORIES` 를 `.repo-temp/unittest` 로 두어, `.git` 을 지운
픽스처를 git 이 위로 올라가 이 저장소로 찾지 않게 한다. 프로세스가 죽어 남은 `.repo-temp/unittest/*` 는 손으로 지운다.

## 증거

측정값, 로그, 리뷰 및 의사결정을 뒷받침할 증거는 `docs/agents/scratch-layout.md`에 따라 `.scratch/<feature>/measure/`, `evidence/`, `review/` 등에 보존한다. 필요한 증거는 임시 폴더를 정리하기 전에 보존하고 확인한다.

**Git이 무시하는 파일은 백업되지 않는다.** `*.log`, `.repo-temp/`, `.scratch/*/notes/`, `*-temp-artifacts/` 아래 격리 사본이 그렇다. 판정·결정을 뒷받침하는 증거는 추적되는 형식(`.md`/`.json`)으로 남긴다. 거기에 명령, 날짜, 측정한 HEAD, 핵심 출력, 원본 SHA-256 을 적는다. 원본이 로그나 격리 사본뿐이면 요약 `.md` 를 함께 두고, 원본 위치와 백업 위치(없으면 "백업 없음")를 근거 문서에 적는다.

## 격리 사본

사본을 만드는 방법은 [호스트 테스트 워크플로우](host-test-workflow.md)를 따른다(`git worktree add --detach`, 저장소 밖 `workflow-<목적>-` 경로, 끝나면 `git worktree remove --force`).

증거로 사본을 보존해야 하면 아래를 지킨다. 사본의 `.git` 은 worktree 등록을 가리키는 파일이다. 등록을 지운 채 사본만 남기면 gitdir 대상이 사라져 `git status` 가 실패하고, 미커밋 변경이 있었는지 나중에 판정할 수 없다.

1. 보존하기 전에 사본의 `.git` **파일**(디렉터리 아님)을 지우고 저장소에서 `git worktree prune` 한다.
2. 사본 안에 `BASE_HEAD.txt` 로 만든 시점의 `git rev-parse HEAD` 와 변경 파일 목록을 적는다.
3. `.scratch/<feature>/measure/<번호>-temp-artifacts/` 아래에 둔다. 이 아래 디렉터리는 `.gitignore` 가 무시한다.

## 훅의 범위

`.claude/hooks/guard_temp_write.py` 는 안전망이지 보증이 아니다.

- Claude `Write` 호출만 검사한다(`.claude/settings.json` 의 matcher). `Edit`·`NotebookEdit`·셸 명령·도구 내부 쓰기와 상대 경로는 검사하지 않는다. Codex에는 이 문서와 AGENTS.md가 적용되지만 Claude 훅은 적용되지 않는다.
- 막는 곳은 시스템 Temp 아래 절대 경로뿐이다. 홈·`orca` 등 그 밖의 외부 폴더(과거의 `em`, `pl_scratch`, `_m19`)는 막지 않는다. 다른 저장소나 `~/.claude` 처럼 정상적으로 쓰는 외부 경로와 구분할 허용 목록이 아직 없다.
- 장애 시 통과(fail-open)한다. 입력 JSON을 읽지 못하거나 경로 해석에 실패하면 막지 않는다.
- 시스템 Temp 아래 예외는 프로젝트 소유 `.claude/temp-write.project.json`의 `allow_temp_top_level` glob 목록으로 받는다. 기본값은 빈 목록이다.

## 건드리지 않는 것

프로세스 전체 `TEMP`/`TMP`를 바꾸지 않는다. Codex·Claude·Orca 및 다른 도구가 관리하는 세션, 상태, 캐시 경로는 건드리지 않는다. 다른 저장소의 파일과 소유 불명 파일도 건드리지 않는다. 외부 출력이 명시된 릴리스·빌드 계약과 기존 도구 동작은 별도로 검토해 변경한다.

## 이미 생긴 잔재 정리

정리는 사용자가 지시했을 때만 한다. 조사 문서(`inventory.md` 같은)는 시점 기록이라 낡는다. 지울 때는 문서가 아니라 파일시스템을 다시 측정한다.

1. 항목마다 소유를 확인한다. 이슈 상태, 병합 커밋이 HEAD 의 조상인지, `.scratch` 에 같은 해시 사본이 있는지, 사본에 미커밋·미병합 내용이 있는지 본다. 하나라도 못 채우면 보존한다.
2. 후보 목록(이름·크기·안의 최신 파일 수정 시각)을 `.scratch/<feature>/classified/` 에 먼저 저장한다.
3. 소유가 확인된 항목은 격리 폴더로 먼저 옮기고, 이동 전후 이름·크기·해시를 대조한다. 영구 삭제는 별도로 승인받는다.
4. 소유 불명 항목은 사용자가 지시하면, 안의 최신 파일이 14일 이상 지났거나 비어 있는 것만 삭제 후보로 올린다. 비어 있지 않은 항목은 목록을 보인 뒤 승인받아 삭제한다.
5. 다음은 대상에서 뺀다. 도구 관리 이름(`claude`, `codex*`, `orca-claude-statusline-last-*`), 실행 중일 수 있는 항목(`lock*`, `scop*`, `HeadlessEdg*`, `PSES*`, `exthost*`), 최근 24시간 안에 수정된 빈 폴더, 잠겨서 삭제에 실패한 항목이다.
6. 다른 저장소 소속으로 보이는 파일은 지우지 않고 해당 저장소 `.scratch` 로 옮기자고 제안한다.
