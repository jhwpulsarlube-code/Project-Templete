---
name: scratch-layout
description: Set up where skills put generated files (specs, tickets, evidence, review JSON, scripts, notes) in a project, then move existing misplaced files. Use when teach/implement/grilling outputs land in inconsistent places, when `issues/` holds non-tickets, or when installing the output-layout rules in a new project.
---

# Scratch layout

스킬이 만드는 파일이 `.scratch/<feature>/` 안에서 한 곳에 모이게 한다. 강제하는 것은 hook 규칙 둘 — **`issues/` 에는 티켓만, 기능 루트에는 spec·map·ledger 만** —
이고, 나머지 폴더(`measure/ review/ evidence/ classified/ scripts/ notes/`)는 프로젝트의
`docs/agents/scratch-layout.md` 표로 안내한다. 스크립트는 모두 이 스킬의 `scripts/` 에 있다.

## 1. 배포 상태 확인

공통 자산은 원본 저장소의 배포 흐름으로 프로젝트 경로 그대로 배포한다.
`.claude/hooks/guard_scratch_layout.py`, `.claude/settings.common.json`,
`docs/agents/scratch-layout.md`의 누락은 공통 배포로 보완한다.
프로젝트 루트 규칙에 산출물 작업 전 위 안내를 읽는 포인터를 둔다.
`.repo-temp/`와 `.scratch/*/notes/`의 ignore는 프로젝트에서 보존한다.

완료: 배포본과 루트 포인터가 준비됐다.

## 3. 기존 위반을 측정한다 (설치와 별개)

```
python .claude/skills/scratch-layout/scripts/scratch_layout.py measure --repo <프로젝트> --root <이름>
```

기능 루트(`<root>/<feature>/`)와 `issues/` 를 모두 본다. `MOVE` 는 규칙으로 목적지가 정해진 파일, `UNKNOWN` 은 Status 줄이 없는 `.md` 처럼 **판단이 필요한 파일**이다.
`UNKNOWN` 마다 티켓인지 근거 문서인지 사용자에게 묻고, 티켓은 `--keep GLOB` 으로 지정한다. 근거 문서는 파일명에
`observations`·`evidence`·`survivor` 가 들어가게 한 뒤 다시 측정한다 (이름 규칙을 임의로 바꾸지 말고 먼저 묻는다).

이동을 막는 제약이 있는지 묻는다. 다른 파일이 그 파일의 **바이트 해시**를 들고 있으면(예: 검토 JSON 이 관찰표의
SHA-256 을 기록) 그 파일은 `--freeze GLOB` 으로 지정한다 — 이동만 하고 내용(링크)은 고치지 않는다.

끝: `UNKNOWN` 0개. 제약을 사용자가 "없다" 또는 구체적 `--freeze` 목록으로 답했다.

## 4. 이동한다

```
python .claude/skills/scratch-layout/scripts/scratch_layout.py migrate --repo <프로젝트> --root <이름> [--keep ...] [--freeze ...]        # 계획
python .claude/skills/scratch-layout/scripts/scratch_layout.py migrate --repo <프로젝트> --root <이름> [--keep ...] [--freeze ...] --apply
```

`git mv` 로 이력을 보존하고, 전체 경로와 마크다운 상대 링크를 함께 고친다. 목적지가 이미 있으면 아무것도 옮기지 않는다.
이동 뒤 프로젝트의 테스트와, 문서 링크가 실제로 이어지는지(깨진 링크 수 이동 전후 비교)를 측정한다.
해시가 걸린 파일은 이동 전후 **해시 불일치 수가 같은지** 확인한다.

끝: `issues/` 안 비티켓 0개(기존 하위 폴더 티켓 제외), 깨진 링크와 해시 불일치가 이동 전보다 늘지 않았다.

## 한계

- hook 은 `Write` 만 본다. Bash 리다이렉션으로 만드는 파일은 막지 못한다.
- 티켓 판정은 파일명 `NN[a-z]?-slug.md` 뿐이다. 이름만 맞춘 비티켓은 hook 을 통과한다.
- `references/layout-table.md` 의 폴더 이름은 기본값이다. 프로젝트가 AGENTS.md 표에서 빼거나 더해도 hook 은 영향받지 않는다.
