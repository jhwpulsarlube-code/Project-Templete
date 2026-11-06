# PR 작성과 병합

`main` 은 PR 로만 바뀐다. 로컬 `main` 에서 `git merge`·`git commit`·`git push` 를 하지 않는다(`.claude/hooks/guard_main_branch.py` 가 에이전트의 Bash·PowerShell 호출을 막는다).
"메인으로 머지" 는 PR 을 만들어 병합한다는 뜻이다.
hook 은 `git -C <경로>`·앞선 `cd <경로>` 가 가리키는 저장소의 브랜치를 본다. 경로를 읽지 못하거나 그 브랜치를 알 수 없으면 세션 저장소의 브랜치로 판단한다.

## 절차

1. 기능 브랜치에서 작업한다. `git status` 가 깨끗하고 `git log --oneline origin/main..HEAD` 가 병합할 커밋과 같은지 본다.
2. push 전에 **CI 가 못 하는 것만** 로컬에서 돌려 증거를 모은다(아래 "증거"). CI 가 이미 돌리는 게이트(`run_tests.py`·lint 등)는 로컬에서 되풀이하지 않는다.
   CI 러너(`host-gates.yml`)는 Linux 에 gcc·python 만 있어 다음 둘을 못 본다.
   - **CS+ 빌드**: 제품 `.c`/`.h` 를 바꿨으면 **필수**다. CI 는 빌드 오류·경고를 전혀 보지 않는다.
   - **Windows(MinGW) 고유 결함**: `test/`·도구를 바꿨으면 `python -B test/tools/precommit_gate.py --full --base origin/main` 을 **권장**한다(필수 아님. CI 왕복을 미리 잡는다).
3. 브랜치를 push 하고 `gh pr create --base main --title "<제목>" --body-file - <<'EOF'` … `EOF` 로 만든다. 본문은 `mattpocock-skills:pr` 스킬의 틀(Summary·Evidence·Merge Danger)을 쓰고, 파일을 남기지 않고 stdin 으로 넘긴다.
   Evidence 에는 로컬 결과를 적고, CI 가 돌릴 항목은 "CI 결과로 갱신 예정" 이라고 적는다.
4. `gh pr checks <번호>` 로 CI 결과를 본다. 실패하거나 진행 중이면 병합하지 않는다. 이 저장소는 GitHub Pro 가 아니라 branch protection 으로
   CI 를 필수로 걸 수 없다 — 병합 직전의 이 확인이 CI 를 거치지 않은 병합을 막는 유일한 장치다.
   CI 가 끝나면 `gh pr edit <번호> --body-file -` 로 본문 Evidence 에 체크 이름·결과·링크를 채운다. CI 가 돌린 명령을 확인하지 못했으면 "CI 가 대신함" 으로만 적는다.
5. `gh pr merge <번호> --merge`. **merge commit** 으로 병합해 커밋 이력을 보존한다(squash·rebase 를 쓰지 않는다. 방식을 바꾸려면 이 줄을 고친다).
6. 로컬 `main` 은 `git checkout main && git pull --ff-only` 로만 맞춘다(`git merge` 는 hook 이 막는다). 별도 요청이 없으면
   병합된 기능 브랜치를 로컬(`git branch -d <브랜치>`)과 원격(`git push origin --delete <브랜치>`)에서 함께 지운다.

## 버리는 원격 브랜치

CI 탐침처럼 한 번 돌리고 지울 브랜치는 로컬 브랜치 없이 만든다. 병합되지 않은 로컬 브랜치는 `-d` 로 지워지지 않고,
`-D` 는 머신의 deny 규칙에 막힐 수 있다.

1. `git worktree add --detach <저장소 밖 경로> origin/main` 에서 커밋하고 `git push origin HEAD:refs/heads/<브랜치>` 로 올린다.
2. 정리는 `git push origin --delete <브랜치>`, `gh run delete <id>`, `git worktree remove <경로>` 를 명령마다 따로 돌린다.
   한 줄로 묶으면 하나가 막힐 때 나머지도 함께 막힌다.

## 증거

PR 본문 Evidence 는 변경 종류별로 아래 명령의 출력을 쓴다. 돌린 것과 못 돌린 것을 구분해 적는다.
"로컬" 은 push 전에 직접 돌려 PR 본문에 적고, "CI" 는 CI 가 돌리므로 끝난 뒤 본문에 결과를 채운다.

| 바뀐 경로 | 명령 | 어디서 |
| --- | --- | --- |
| 제품 `.c`/`.h` | CS+ 빌드(`AGENTS.md` "머신 설정")의 `Build ended(Error:N, Warning:N)` 줄. ROM/RAM·`asm_compare` 기준이 필요하면 `build_report.py` | 로컬(필수) |
| 제품 `.c`/`.h`, `test/`(`test/tools/` 제외) | `python test/run_tests.py`, `python test/tools/lint_gate.py` | CI. 해당 TU 의 `run_tests.py <TU>` 는 로컬 |
| `test/tools/`, `.githooks/`, `.claude/init-machine.py` | `python -B -m unittest discover -s test/tools` (약 4분, `pytest` 는 설치돼 있지 않다) | CI |
| `test/`, 도구 | `precommit_gate.py --full --base origin/main` (Windows 고유 결함) | 로컬(권장) |

문서·`.scratch/` 만 바뀐 PR 은 돌릴 명령이 없다. 그렇다고 적는다.

## Merge Danger 를 채우는 기준

- 제품 코드(`application/` 등)를 건드리면 Blast Radius 에 "출하 펌웨어" 를 적고 로컬 CS+ 빌드 결과를 밝힌다(CI 는 빌드를 보지 않는다).
- 문서·테스트·도구만이면 two-way door 다. `git revert` 로 되돌린다.

## 전송과 기록

push 전에 `git log --oneline origin/main..HEAD`로 보낼 범위를 사용자에게 보여 준다.
PowerShell에서는 `@'` … `'@ | gh pr create … --body-file -`로 본문을 전달한다.
시스템이 지정한 표기가 있으면 본문 끝에 둔다. force push는 `--force-with-lease`를 쓴다.
이슈를 닫거나 게이트를 맞추는 PR은 만들기 전에 해당 이슈에 브랜치와 증거를 기록한다.
PR 번호와 실행 기록은 PR과 병합 커밋에서 찾는다.
외부 도구 인자 정책은 프로젝트 소유 `docs/agents/workflow-project.md`를 따른다.
