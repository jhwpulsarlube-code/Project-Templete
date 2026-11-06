# Claude 설정 생성

소비 프로젝트에는 다음 두 공통 배포본을 같은 `.claude/` 디렉터리에 둔다.

- `settings.common.json`: 공통 hook 배선.
- `compose-settings.py`: Python 3 표준 라이브러리만 사용하는 생성기.

프로젝트 소유 `.claude/settings.project.json`을 JSON 객체로 커밋한다. 추가 설정이
없으면 `{}`를 쓴다. 두 입력은 모두 필수이며, 입력 누락은 오류다.
소비 프로젝트 루트에서 다음 명령으로 `.claude/settings.json`을 생성한다.

```console
python .claude/compose-settings.py
```

생성기는 자신의 위치에서 입력을 찾으므로 호출자의 작업 디렉터리에 의존하지 않는다.
생성 결과도 소비 프로젝트에 커밋한다. 직접 편집한 설정은 프로젝트 입력으로 옮기고
재생성한다. 원본 저장소나 사용자 레벨 스킬, 외부 패키지는 실행에 필요하지 않다.

## 병합 계약

- 객체는 키별로 재귀 병합한다. 한쪽에만 있는 키는 보존한다.
- 모든 배열은 공통 입력, 프로젝트 입력 순서의 합집합이다. 처음 나타난 항목의
  순서를 보존하며, 한 입력 안의 중복과 중첩 배열의 중복도 제거한다.
- 배열 항목은 완전한 JSON 값으로 비교한다. 객체 키 순서는 무시한다.
  hook의 `matcher`가 같아도 내용이 다르면 별도 항목으로 보존한다.
  실행 순서를 바꾸는 matcher 기반 병합이나 배열 정렬은 하지 않는다.
- 같은 스칼라는 허용한다. 서로 다른 스칼라나 타입 충돌은 경로를 포함한 오류로
  끝낸다. `true`와 `1`도 충돌이다. 덮어쓰기 우선순위는 없다.
- 객체가 아닌 최상위 입력, 잘못된 JSON, 중복 객체 키, `NaN`/`Infinity`는 거부한다.
- 출력은 키 정렬, 공백 2칸 들여쓰기, UTF-8 BOM 없음, LF와 마지막 줄바꿈으로 고정한다.
  같은 입력은 같은 바이트를 생성한다.
- 입력 또는 병합 오류 시 종료 코드는 1이며 기존 생성 파일은 유지한다.

후속 sync·표류 검사에서도 배포된 스크립트의
`compose_settings(directory: pathlib.Path) -> bytes`를 호출해 같은 바이트를 얻을 수 있다.
이 함수는 파일을 쓰지 않으며 실패 시 `ValueError`를 발생시킨다.

## 현재 소비 프로젝트에서 분리한 근거

2026-11-06 로컬 체크아웃의 추적 설정 `.claude/settings.json`을 비교했다.
`settings.local.json`과 사용자 설정은 입력에 포함하지 않았다.

| 소비 프로젝트 | 확인한 HEAD | 프로젝트 입력에 보존할 내용 |
| --- | --- | --- |
| Pulsarlube-R | `90294730fa726a42af95195da3766008326489a7` | `permissions.allow` 전체: `git cherry`와 프로젝트 `test/run_tests.py` 실행 권한 |
| Pulsarlube-T_N2 | `6e988c06239c1de04acfbeb7ecfb4ba21da3d89b` | 제품 코드 미커밋 변경을 알리는 `hooks.Stop` |
| Optime-LWX1P | `b5f8fc76ba08b6937bae07cda4d8a85f17175ecc` | `permissions` 전체, `disableClaudeAiConnectors`, `disableRemoteControl`, `disableArtifact`, `block_egress.py` 배선 |
| Pulsarlube-LK_BTr | `25b3cc02cb21a1d044dcb47adcef53f8a72dfd7f` | Optime과 같은 경계. `WebSearch`는 Optime의 ask와 달리 deny에 보존 |

공통 조각은 설계서 7절에 따라 R의 hook 배선을 기준으로 삼았다.
`inject_coding_standards.py`의 Skill·UserPromptSubmit 연결,
`guard_scratch_layout.py`의 Write 연결, `guard_main_branch.py`의 Bash·PowerShell 연결,
`guard_temp_write.py`의 Write 연결을 포함한다. R의 temp-write 예외는 hook 구현의
프로젝트 확장 지점에서 다룰 내용이며, 공통 연결을 프로젝트 입력에 복제하지 않는다.

LK·Optime의 기존 `Bash|PowerShell` 항목은 전환 시 `block_egress.py`만 남긴 프로젝트
항목으로 나눈다. `guard_main_branch.py`는 공통의 Bash·PowerShell 항목에서 실행한다.
공통 목록이 먼저 실행되고 프로젝트 목록이 뒤에 실행되는 것이 새 생성 계약이다.
기존 한 항목 안의 hook 실행 순서를 그대로 유지하는 병합 계약은 아니다.

이 작업은 생성 계약과 공통 조각을 제공한다. hook 구현 수집과 확장 지점 연결,
실제 소비 프로젝트 입력 작성·배포·전환은 후속 작업이다. 아직 hook 구현이 수집되지
않았으므로 이 조각만 활성 소비 프로젝트에 복사하는 것은 전체 배포가 아니다.
원본 저장소에는 소비 프로젝트별 자산 트리를 두지 않는다.

## 검증

```console
python -B -m unittest discover -s tests -v
```

임시 소비 프로젝트에 생성기와 입력만 두고 Python 격리 모드(`-I -S`)로 CLI를 실행한다.
권한·hook 합집합, 충돌 거부와 기존 파일 보존, 입력 오류, 한국어·Windows 경로,
반복 생성의 바이트 동일성을 확인한다. 실제 hook 실행은 이 검증 범위에 포함하지 않는다.
