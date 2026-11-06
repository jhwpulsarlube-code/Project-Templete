### 산출물 위치

스킬이 만드는 파일은 아래 위치만 쓴다. 새 폴더가 필요하면 이 표부터 고친다.

| 산출물 | 위치 |
|---|---|
| spec·map·원장 | `{root}/<feature>/spec.md`, `map.md`, `ledger.md` — 기능 루트에는 이 문서만 (`.claude/hooks/guard_scratch_layout.py` 가 새 파일을 막는다) |
| 티켓 | `{root}/<feature>/issues/NN-<slug>.md` — **티켓만, 하위 폴더 없음** (`.claude/hooks/guard_scratch_layout.py` 가 새 파일을 막는다) |
| 측정값·기준선 | `{root}/<feature>/measure/` |
| 리뷰·변이 검토 JSON | `{root}/<feature>/review/` |
| 관찰표·근거·생존자 문서 | `{root}/<feature>/evidence/` |
| 분류·진단 결과 | `{root}/<feature>/classified/` |
| 일회성 스크립트 | `{root}/<feature>/scripts/` |
| 탐색 노트·서브에이전트 중간 메모 | `{root}/<feature>/notes/` — 커밋 제외, 결론만 spec·티켓에 옮긴다 |

하위 폴더 이름은 영어 소문자 단수다. 이 규칙 이전에 만든 기존 파일은 해당 기능을 다시 열 때 옮긴다.
