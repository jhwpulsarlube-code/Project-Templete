# 산출물 위치

스킬(teach·implement·grilling·research 등)이 만드는 파일은 아래 위치만 쓴다. 새 폴더가 필요하면 이 표부터 고친다.

| 산출물 | 위치 |
|---|---|
| spec·map·원장 | `.scratch/<feature>/spec.md`, `map.md`, `ledger.md` — 기능 루트에는 이 문서만 |
| Issues | Follow the project tracker policy in `docs/agents/workflow-project.md`; local trackers use numbered files under `issues/`. |
| 측정값·실기 취득·기준선 | `.scratch/<feature>/measure/` |
| 변이 검토 JSON·리뷰 | `.scratch/<feature>/review/mutation-review-<id>.json` |
| 관찰표·근거·생존자 문서 | `.scratch/<feature>/evidence/NN-observations.md`, `NN-evidence*.md` |
| 분류·진단 결과 | `.scratch/<feature>/classified/` |
| 일회성 스크립트 | `.scratch/<feature>/scripts/` |
| 탐색 노트·서브에이전트 중간 메모 | `.scratch/<feature>/notes/` — 커밋 제외(루트 `.gitignore`의 `.scratch/*/notes/`), 결론만 spec·티켓에 옮긴다 |
| teach 학습 워크스페이스 | `.learning/` (`MISSION.md`·`NOTES.md`·`RESOURCES.md`·`lessons/`·`reference/`·`learning-records/`·`assets/`) — teach 는 이 디렉터리에서 실행 |

하위 폴더 이름은 영어 소문자 단수다. 기존 파일을 옮기기 전에 검토 JSON과 기준선의 참조를 확인한다.
해시나 경로로 고정된 관찰표·근거·원장은 기존 경로와 내용을 보존한다. 정정은 새 파일에 기록하고 현재 검토에 반영할 때 경로와 해시를 갱신해 다시 검증한다.
이동 도구: `.claude/skills/scratch-layout/scripts/scratch_layout.py`. 공통 자산 갱신은 원본 저장소의 배포 흐름을 따른다.

The project tracker and issue-file policy are defined in `docs/agents/workflow-project.md`.
