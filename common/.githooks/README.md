# 공통 Git hook

소비 프로젝트의 `.githooks/`로 이 디렉터리를 배포하고
`git config core.hooksPath .githooks`로 활성화한다. Python 3와 `sh`가 필요하다.
실행 가능한 Python 3 실행기를 `py`, `python` 순서로 찾고 선택한 이름을
`PY` 환경 변수로 제공한다.

`pre-commit`과 `post-commit`은 `common_checks.py`를 먼저 실행한 다음
프로젝트 소유의 `<hook>.d/` 파일을 이름순으로 `sh`로 실행한다.
확장 파일은 실행 권한이 없어도 된다. 하위 디렉터리는 실행하지 않는다.
모든 검사와 확장은 동일한 인자와 stdin 바이트를 받는다.
실패하면 해당 종료 코드로 중단한다. `post-commit` 실패는 이미 만들어진
커밋을 되돌리지 않는다.

공통 파일을 수정하지 않고 프로젝트 검사를 `.githooks/pre-commit.d/10-check`
같은 파일에 둔다. Python 실행은 `"$PY" path/to/check.py "$@"` 형태로 쓴다.

pre-commit은 프로젝트 검사 전에 배포본의 표류를 검사한다. 원본 저장소와 사용자
레벨 스킬 없이 Python 표준 라이브러리와 Git만으로 실행한다. 작업 파일과 Git
인덱스를 각각 검사하므로 프로젝트 입력과 재생성한 설정을 함께 스테이징해야 한다.
`.workflow-deployment.json`이 없거나 잘못되었으면 차단한다. 일반 배포본의 수정·삭제와
LF/CRLF 차이는 마지막 배포 기록의 SHA-256과 바이트 그대로 비교한다. 최신 정본을
조회하지 않으며 마지막 배포 상태를 유지하는 오래된 버전은 업데이트 대상이다.

`.claude/settings.json`은 기록 해시 대신 현재 공통 조각과 프로젝트 입력을 배포된
생성기로 병합한 바이트와 비교한다. 프로젝트 변경은 `.claude/settings.project.json`에
옮기고 `python -B .claude/compose-settings.py`로 재생성한다. 검사는 파일을 고치지 않는다.
일반 배포본의 차이는 검토 후 배포본을 복원하거나 원본 저장소에 역반영하고 재배포한다.
줄바꿈 차이가 반복되면 `.gitattributes`를 바로잡는다. 기록 해시 수동 수정은 해소 절차가 아니다.

작업 브랜치의 중간 커밋에만 명시적으로 표류 검사를 일회성 우회할 수 있다.
프로젝트 검사는 계속 실행하며, PR 병합 전에는 우회 없는 정상 검사를 통과해야 한다.
환경 변수를 영구 설정하지 않고 해당 명령에만 전달한다.

```sh
WORKFLOW_SKIP_DRIFT_ONCE=1 git commit -m "WIP: review distribution changes"
```

PowerShell에서는 자식 프로세스에만 전달한다.

```powershell
cmd /c 'set "WORKFLOW_SKIP_DRIFT_ONCE=1" && git commit -m "WIP: review distribution changes"'
```

PR 병합 전 소비 프로젝트 루트에서 `python -B .githooks/check_drift.py`로 정상 검사를
실행할 수도 있다. 이 명령은 우회 환경 변수와 무관하게 작업 파일과 인덱스를 검사한다.

원본 저장소의 통합 테스트: `python -m unittest discover -s tests -v`.
