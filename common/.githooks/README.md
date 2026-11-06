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

현재 공통 검사는 성공으로 종료하며, 표류 검사 연결은 후속 티켓의 범위다.
공통 파일을 수정하지 않고 프로젝트 검사를 `.githooks/pre-commit.d/10-check`
같은 파일에 둔다. Python 실행은 `"$PY" path/to/check.py "$@"` 형태로 쓴다.

원본 저장소의 통합 테스트: `python -m unittest discover -s tests -v`.
