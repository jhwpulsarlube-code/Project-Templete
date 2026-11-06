# 머신 설정과 프로젝트 검사

소비 프로젝트에서 `python .claude/init-machine.py`를 실행한다. Python 표준 라이브러리와 Git만 필요하다. `.claude/machine.json`과 `.claude/toolchain.local.json`은 소비 프로젝트에서 ignore한다. 후자는 선택적 `gcc`, `ccrl`, `cppcheck`, `cubesuite` 실행 파일 경로를 받으며 수정하지 않는다. `CC` 환경 변수와 PATH도 탐지한다.

`--refresh`는 공통 capability를 다시 기록한다. `--nightly`는 cppcheck가 필요한 머신으로 기록하며 이후 refresh에서도 역할을 유지한다. 프로젝트 검사와 gcc 실행 진단은 캐시 여부와 무관하게 실행한다. 실행 진단 실패는 백신 로그 확인을 안내하는 경고이며 실패 종료 사유가 아니다.

공통 키는 `can_run_host_tests`, `can_build_product`, `can_verify_cc_rl_hex`, `can_run_cppcheck`, `has_cubesuite`, `can_run_post_link`다. 제품 프로젝트는 루트의 `*.mtpj`로 판별한다. CubeSuite는 설정된 `CubeSuite+.exe`만 인정하며 GUI인 `CubeSuiteW+.exe`는 인정하지 않는다. post-link는 PATH의 `python`이 intelhex를 import할 수 있는지 검사한다. CubeSuite 부재 자체는 실패하지 않으며 필수 여부는 프로젝트 검사가 정한다.

프로젝트가 소유하는 선택적 `.claude/machine_checks.py`는 인자 없는 두 함수를 제공한다.

```python
from machine_common import egress_errors, find_cubesuite


def capabilities() -> dict:
    return {"project_check_available": True}


def errors() -> list[str]:
    problems = []
    if not find_cubesuite():
        problems.append("CubeSuite+.exe 경로를 toolchain.local.json에 설정하세요")
    # 이 프로젝트에서 egress 차단을 요구할 때만 호출한다.
    problems.extend(egress_errors())
    return problems
```

공통 모듈은 실행 중 `machine_common`으로 제공한다. 확장은 프로젝트 capability만 반환한다. 공통 키와 겹치거나 함수 반환 계약을 어기거나 확장 로딩이 실패하면 오류로 종료하고 기존 기록을 보존한다. `errors()`의 오류는 stderr로 출력하고 종료 코드 1을 반환한다. capability 값은 정보용이며 공통 도구가 프로젝트 정책으로 해석하지 않는다.

`egress_errors`는 기본적으로 `block_egress.py`의 Bash·PowerShell PreToolUse 연결, WebFetch·WebSearch deny, connector 비활성화, 프로젝트·로컬·사용자 설정의 disableAllHooks를 검사한다. 프로젝트는 `settings_path`, `hook_path`, `other_settings`, `shell_tools`, `denied_tools`, `disable_connectors` 인자로 정책을 지정한다. 확장에서 호출하지 않으면 egress 검사는 실행하지 않는다. hook 구현과 실제 연결 설정은 프로젝트 소유다.

Git hooksPath가 비어 있으면 `.githooks`를 설정한다. 다른 경로가 존재하지 않으면 교체 사실을 알리고 `.githooks`로 바꾼다. 존재하는 다른 경로는 유지하고 경고한다. 상대 경로는 소비 프로젝트 루트 기준으로 확인한다.

검증: `python -m unittest discover -s tests -p test_init_machine.py -v`. 테스트는 임시 소비 프로젝트에 진입점을 복사하고 Git 설정을 검사하므로 원본 저장소나 실제 머신 설정을 변경하지 않는다.
