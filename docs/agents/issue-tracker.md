# 이슈 추적기: GitHub

이 저장소의 이슈와 스펙은 GitHub Issues에서 관리합니다. 모든 작업에 `gh` CLI를 사용합니다. 저장소는 `git remote -v`에서 확인하며, 클론 내부에서는 `gh`가 자동으로 판별합니다.

## 작업 방법

- 이슈 생성: `gh issue create --title "..." --body "..."`. 여러 줄 본문은 파일을 작성하고 `--body-file`을 사용합니다.
- 이슈 조회: `gh issue view <번호> --comments`. 라벨도 확인하고 필요하면 `jq`로 댓글을 필터링합니다.
- 이슈 목록: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`. 필요에 따라 `--label`과 `--state`로 필터링합니다.
- 댓글: `gh issue comment <번호> --body "..."`
- 라벨 추가 또는 제거: `gh issue edit <번호> --add-label "..."` 또는 `--remove-label "..."`
- 종료: `gh issue close <번호> --comment "..."`

## PR 트리아지

**PR을 요청 접수 대상으로 사용: no.** 외부 PR을 기능 요청으로 취급하려면 `yes`로 변경합니다. `/triage`가 이 값을 읽습니다.

`yes`일 때는 이슈와 같은 라벨과 상태를 적용합니다. `gh pr view <번호> --comments`와 `gh pr diff <번호>`로 읽고, `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`에서 `authorAssociation`이 `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, `NONE`인 PR만 남깁니다. 댓글, 라벨, 종료에는 `gh pr comment`, `gh pr edit`, `gh pr close`를 사용합니다. 이슈와 PR은 번호를 공유하므로 번호만 주어지면 `gh pr view`를 먼저 시도하고 실패하면 `gh issue view`를 사용합니다.

스킬이 이슈 추적기에 게시하라고 하면 GitHub 이슈를 생성하고, 관련 티켓을 가져오라고 하면 `gh issue view <번호> --comments`를 실행합니다.

## Wayfinder 작업

`/wayfinder`는 하나의 이슈를 맵으로, 하위 이슈를 티켓으로 사용합니다.

- 맵: `wayfinder:map` 라벨을 붙인 이슈에 Notes, Decisions-so-far, Fog를 기록합니다.
- 하위 티켓: GitHub sub-issues API로 맵에 연결합니다. 사용할 수 없으면 맵 본문에 작업 목록을 추가하고 하위 이슈 맨 위에 `Part of #<맵 번호>`를 기록합니다. 유형에 따라 `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, `wayfinder:task` 라벨을 붙입니다. 담당하면 작업자를 할당합니다.
- 차단 관계: GitHub 기본 이슈 의존성을 사용합니다. `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`로 추가합니다. ID는 `gh api repos/<owner>/<repo>/issues/<번호> --jq .id`로 얻는 숫자 데이터베이스 ID입니다. 지원하지 않으면 하위 이슈 맨 위에 `Blocked by: #<번호>, #<번호>`를 기록합니다. 모든 차단 이슈가 닫히면 차단이 해제됩니다.
- 다음 작업 조회: 맵 순서대로 열린 하위 이슈를 조회하고 열린 차단 이슈나 담당자가 있는 항목을 제외합니다. 첫 항목을 선택합니다. 기본 의존성의 `issue_dependencies_summary.blocked_by`는 열린 차단 이슈 수입니다.
- 담당: 세션의 첫 쓰기 작업으로 `gh issue edit <번호> --add-assignee @me`를 실행합니다.
- 완료: 답변을 댓글로 남기고 이슈를 닫은 뒤 맵의 Decisions-so-far에 맥락 포인터와 링크를 추가합니다.
