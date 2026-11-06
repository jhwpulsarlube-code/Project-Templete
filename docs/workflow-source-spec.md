# 워크플로우 원본 저장소 운영 설계서

이 문서는 워크플로우 자산의 배포·표류 검사·역반영과 소비 프로젝트 전환에 필요한 최종 동작과 완료 조건을 정한다. 도구 구현과 실제 전환은 후속 작업이다. 용어는 [GLOSSARY.md](../GLOSSARY.md)를 따른다.

판단 배경은 각 절의 결정 티켓과 [ADR](adr/0001-push-committed-distributions.md)에 둔다. [결정 지도](https://github.com/jhwpulsarlube-code/Project-Templete/issues/1)는 원래 합의의 색인이다. 이 문서의 `sync`와 `reverse`는 명령 계약이며, 실행 파일명·배포 기록 파일명과 JSON 필드명·우회 수단의 이름은 구현 시 정한다.

## 1. 적용 범위와 소유권

소비 프로젝트는 `Pulsarlube-R`, `Pulsarlube-T_N2`, `Optime-LWX1P`, `Pulsarlube-LK_BTr`의 네 제품 저장소다. 같은 저장소의 다른 클론과 worktree는 별도 소비 프로젝트로 세지 않는다.

| 구분 | 포함 범위 |
| --- | --- |
| 공통 계층 | Claude hook과 연결 설정, 공통 스킬, `docs/agents`의 공통 문서, git hook, `issue-status.ps1`, `init-machine.py`, 공통 워크플로우 안내와 재사용 도구 |
| 프로젝트 계층 | 소비 프로젝트가 소유하는 확장 지점 파일, 프로젝트 고유 문서·검사·테스트 로직, `AGENTS.md` |
| 범위 밖 | 빌드·배포 도구(`release.py`), CI 정의(`host-gates.yml`), `CODING_STANDARDS.md`, 외부 범용 스킬 |

`Pulsarlube-nfc_tag`, `Pulsarlube-Docs`, `T_N2-kit-p5-os-log-baseline` 클론은 이번 전환 대상에서 제외한다.

공통 계층은 네 소비 프로젝트에 같은 자산 전부를 배포한다. 프로젝트별 선택·제외는 두지 않는다. 프로젝트 계층의 정본은 소비 프로젝트에만 둔다. 현재 네 프로젝트가 모두 RL78이므로 `renesas-rl78` 스킬과 gcc·ccrl·cppcheck 탐지도 공통 계층이다. RL78이 아닌 소비 프로젝트가 범위에 들어올 때 도메인 계층을 도입한다.

배포본은 소비 프로젝트에 커밋하며, 원본 저장소와 사용자 레벨 스킬 없이도 다른 PC와 CI에서 동작해야 한다. 공통 문서는 파일 단위로 소유하고, 프로젝트 고유 내용은 별도 파일로 분리해 링크한다. hook의 프로젝트 차이는 인자나 설정으로 받는다.

근거: [범위와 원칙](https://github.com/jhwpulsarlube-code/Project-Templete/issues/2#issuecomment-6630629320), [2계층 결정](https://github.com/jhwpulsarlube-code/Project-Templete/issues/6#issuecomment-6633632132).

## 2. 원본 저장소 구조와 줄바꿈

정본은 `common/` 아래에 소비 프로젝트의 배포 경로를 그대로 본떠 둔다. 다음은 구현할 배치의 예다.

```text
common/
  .claude/
    .gitattributes
    hooks/
    skills/{renesas-rl78,scratch-layout,legacy-host-testing}/
    settings.common.json
    compose-settings.py
    init-machine.py
  .githooks/
    .gitattributes
    pre-commit
    post-commit
  docs/agents/
    .gitattributes
  tools/
    .gitattributes
    issue-status.ps1
tools/
  sync.py
docs/
  workflow-source-spec.md
  adr/
```

`common/<경로>`는 소비 프로젝트의 `<경로>`로 배포한다. 경로 대응표(manifest)는 두지 않는다. 원본 저장소 전용 도구와 테스트는 `common/` 밖에 두고, 루트 `.claude/`는 이 저장소 자신의 설정으로 사용한다. 원본 저장소에 프로젝트별 자산 트리는 만들지 않는다.

정본은 한 벌만 둔다. 스킬 내부 seed와 사본을 배포 경로의 정본으로 통합하고, 스킬 본문은 그 경로를 가리킨다. `scratch-layout`과 `legacy-host-testing`도 `.claude/skills/`에 배포하는 프로젝트 스킬로 전환한다. `install.py`의 `project`·`skill` 모드는 모두 은퇴하며 사용자 레벨 링크나 설치 스크립트는 두지 않는다.

배포 디렉터리별 `.gitattributes`로 파일별 LF/CRLF와 `text` 속성을 고정하고, 같은 규칙을 배포한다. `tools/`처럼 프로젝트 파일이 섞인 디렉터리는 배포 파일명만 규칙에 적는다. 소비 프로젝트의 루트 `.gitattributes`는 유지한다. 같은 디렉터리의 프로젝트 소유 파일에도 해당 디렉터리 규칙이 적용된다. Git 체크아웃으로 바이트 차이가 반복되면 속성 설정을 바로잡는다.

근거: [구조·스킬·줄바꿈 결정](https://github.com/jhwpulsarlube-code/Project-Templete/issues/7#issuecomment-6634280133).

## 3. 진입점과 확장 지점

진입점은 sync가 관리한다. 일반 진입점은 정본과 바이트가 같아야 하며, 생성 진입점인 `settings.json`은 아래 생성 계약을 따른다. 프로젝트 고유 내용은 별도 확장 지점 파일로 커밋한다.

### Claude 설정

- 공통 배포본은 `.claude/settings.common.json`과 `.claude/compose-settings.py`다. 프로젝트 입력은 `.claude/settings.project.json`이다.
- `.claude/settings.json`은 두 입력을 병합해 생성한다. 배열(`allow`, `deny`, hook 목록)은 중복을 제거한 합집합으로 합치며, 스칼라 값 충돌은 오류로 끝낸다.
- sync와 소비 프로젝트는 같은 스크립트를 사용한다. 소비 프로젝트에서 `python .claude/compose-settings.py`만으로 재생성할 수 있어야 한다.
- 생성 결과는 같은 입력에서 같은 바이트를 내야 한다. 권한 UI를 포함한 `settings.json` 직접 변경은 재생성 결과와 다르면 표류다. 프로젝트 변경은 프로젝트 입력에 옮긴 뒤 재생성한다.

### Git hook

- `.githooks/pre-commit`과 `post-commit`은 공통 디스패처다. `py`/`python` 실행기를 선택하고 공통 검사를 실행한 뒤 `.githooks/<hook>.d/*`를 이름순으로 실행한다.
- `.d/`는 프로젝트 소유다. 각 파일은 `sh`로 실행하며 `$PY` 환경 변수, hook 인자와 stdin을 그대로 받는다. pre-commit은 실패 즉시 중단한다. 디렉터리가 없거나 비어 있으면 공통 검사만 실행한다.
- pre-commit의 공통 검사에는 5절의 표류 검사를 둔다. 프로젝트별 테스트 로직은 확장 지점에 둔다.

### 머신 설정

- `.claude/init-machine.py`는 공통 정본이며, 선택적 `.claude/machine_checks.py`를 읽는다.
- 프로젝트 모듈은 `capabilities() -> dict`, `errors() -> list[str]`를 제공한다. 공통 capability 키와 겹치면 오류로 끝낸다.
- `core.hooksPath`가 존재하지 않는 경로를 가리키면 `.githooks`로 바꾸고 알린다. 존재하는 다른 경로라면 유지하고 경고한다.
- 공통으로 흡수할 머신 검사는 7절을 따른다. 프로젝트별 egress 정책과 검사 배선은 확장 지점에 둔다.

근거: [진입점 계약](https://github.com/jhwpulsarlube-code/Project-Templete/issues/5#issuecomment-6632474294), [정본 확정의 후속 결정](https://github.com/jhwpulsarlube-code/Project-Templete/issues/10#issuecomment-6682110734). 선택 배경은 [ADR 0002](adr/0002-entrypoint-extension-ownership.md)에 둔다.

## 4. 배포 계약

원본 저장소의 도구를 Python 표준 라이브러리와 `git` CLI로 구현한다. 정본 변경을 커밋한 개발자가 `sync <소비 프로젝트 경로>`를 수동 실행한다. CI·예약 배포는 두지 않는다. 각 변경의 전파 시점은 6절을 따른다.

1. **사전 검사**: 원본 저장소에 미커밋 변경이 있거나, 소비 프로젝트 작업 트리가 깨끗하지 않거나, 소비 프로젝트 기본 브랜치의 최신 상태에서 브랜치를 만들 수 없으면 거부한다. 5절에 따라 기존 배포본과 첫 배포 충돌도 검사한다. 완료 조건은 모든 거부 사유와 충돌 경로가 판별된 상태다.
2. **계획**: 공통 자산 전체의 추가·갱신·삭제와 설정 생성 결과, 배포 기록 갱신을 계산한다. `--dry-run`은 파일을 바꾸지 않고 추가·갱신·삭제·표류로 멈춘 파일 목록을 보여 준다. 완료 조건은 쓸 파일과 지울 파일 전부가 계획에 포함된 상태다.
3. **적용**: 소비 프로젝트 기본 브랜치 최신 상태에서 `workflow-sync/<정본 SHA 앞자리>` 브랜치를 만들고 정본을 바이트 그대로 배포한다. 삭제는 이전에 sync가 배포한 파일에만 적용한다. 배포한 적 없는 프로젝트 파일은 보존한다. 완료 조건은 배포본·생성 파일·배포 기록이 같은 정본을 나타내는 상태다.
4. **검증과 결과물**: 표류 검사를 통과한 배포 변경을 커밋 하나로 남긴다. PR은 사람이 연다. 같은 정본과 같은 프로젝트 입력으로 재실행하면 변경이 없어야 한다.

마지막 배포 이후 수정되거나 삭제된 배포본은 경로 목록을 보여 주고 멈춘다. 정본에서 없어져 삭제할 파일도 같은 보호를 받는다. 차이를 검토한 뒤 명시적으로 `--force`를 줄 때만 덮어쓰기·삭제를 진행할 수 있다. `--force`는 작업 트리 청결 등 다른 사전 조건의 우회 수단이 아니다.

첫 배포도 같은 명령을 쓴다. 배포 기록이 없을 때 경로가 비어 있으면 추가하고, 기존 파일이 정본과 바이트가 같으면 배포 기록에 편입한다. 다르면 충돌 목록을 보여 주고 멈추며, 검토 후 `--force`로 처리할 수 있다. Optime-LWX1P에도 이 규칙을 적용한다.

근거: [배포 메커니즘](https://github.com/jhwpulsarlube-code/Project-Templete/issues/4#issuecomment-6631838607), [첫 배포 충돌](https://github.com/jhwpulsarlube-code/Project-Templete/issues/8#issuecomment-6681900474). 선택 배경은 [ADR 0001](adr/0001-push-committed-distributions.md)에 둔다.

## 5. 배포 기록과 표류 판정

소비 프로젝트에 배포 기록 파일 하나를 커밋한다. 원본 저장소의 전체 커밋 SHA와 배포 경로별 SHA-256을 저장하며, 파일 헤더는 변경하지 않는다. 배포 기록은 배포 이력이며 2절에서 두지 않기로 한 경로 대응표와 용도가 다르다.

sync 전과 소비 프로젝트 pre-commit에서 검사한다. 소비 프로젝트 검사는 원본 저장소 없이 동작해야 한다.

| 비교 대상 | 기준과 판정 |
| --- | --- |
| 일반 배포본 | 마지막 배포 기록의 경로별 해시와 비교한다. 수정·삭제는 표류다. |
| 생성된 `settings.json` | 현재 공통 조각과 프로젝트 입력을 배포된 스크립트로 병합한 결과와 바이트 비교한다. 직접 변경이나 재생성 누락은 표류다. |
| 배포 버전 | 마지막 배포 상태는 유지하나 최신 정본보다 오래되었으면 업데이트 대상이다. 이를 표류로 막지 않는다. |
| 첫 배포의 기존 파일 | 배포할 정본과 바이트 비교하고 4절의 편입·충돌 규칙을 적용한다. |

비교 중 줄바꿈을 정규화하지 않는다. LF/CRLF만 달라도 표류다. 생성 파일에는 경로별 기록 해시만으로 판정하는 일반 파일 규칙을 적용하지 않고, 현재 입력의 재생성 결과를 기준으로 삼는다.

pre-commit에는 작업 브랜치 중간 커밋을 위한 명시적인 일회성 표류 검사 우회 수단을 둔다. PR 병합 전에는 정상 검사를 통과해야 한다. 배포 기록의 해시를 수동으로 고쳐 표류를 숨기는 방식은 허용하지 않는다.

완료 조건은 원본 저장소가 없는 소비 프로젝트에서도 배포본 수정·삭제·줄바꿈 변경과 생성 결과 불일치를 탐지하고, 정상 재생성과 업데이트 대상을 통과시키는 것이다.

근거: [검사·배포 기록·바이트 판정](https://github.com/jhwpulsarlube-code/Project-Templete/issues/8#issuecomment-6681900474), [중간 커밋과 업데이트 대상](https://github.com/jhwpulsarlube-code/Project-Templete/issues/9#issuecomment-6682210302). 선택 배경은 [ADR 0003](adr/0003-byte-level-drift-from-deployment-record.md)에 둔다.

## 6. 역반영과 전파

배포본을 수정한 개발자가 같은 작업 안에서 역반영을 책임지고, 소비 프로젝트 PR을 병합하기 전에 표류를 해소한다. 날짜로 정한 별도 유예 기간은 없다.

1. 원본 저장소의 `reverse <소비 프로젝트 경로>`로 차이를 미리 보고 파일별로 선택한다. 완료 조건은 각 차이가 공통 개선인지 프로젝트 전용 변경인지 분류된 상태다.
2. 공통 개선은 정본에 반영한다. 프로젝트 전용 변경은 소비 프로젝트 확장 지점으로 옮긴다. 생성된 `settings.json`은 통째로 역복사하지 않고 공통 조각과 프로젝트 입력으로 나누어 처리한다. 완료 조건은 선택한 차이가 각각의 소유 파일에 반영된 상태다.
3. 정본 변경을 커밋하고 sync·배포 PR 흐름으로 수정한 소비 프로젝트의 배포본과 배포 기록을 맞춘다. 완료 조건은 소비 프로젝트 PR 병합 전에 우회 없는 정상 표류 검사가 통과하는 것이다.
4. 변경별로 다른 소비 프로젝트에 즉시 전파할지 추후 일괄 전파할지 판단한다. 전파에는 같은 sync·배포 PR 흐름을 사용한다. 다른 프로젝트 전부에 즉시 배포하는 것을 개별 역반영의 완료 조건으로 삼지 않는다.

근거: [역반영의 책임·도구·전파 시점](https://github.com/jhwpulsarlube-code/Project-Templete/issues/9#issuecomment-6682210302).

## 7. 초기 정본 확정안

Pulsarlube-R을 기본으로 삼고 파일별 개선을 흡수한다. 다음 표는 자산을 옮길 때 적용할 확정안이다.

| 자산·차이 | 반영할 내용 |
| --- | --- |
| `guard_main_branch.py` | R의 `-C`/`cd` 대상 저장소 판정과 LK의 here-string 내부 `$()`·따옴표로 감싼 ref 처리를 합친다. |
| `pull-request.md` | R과 LK 각각의 개선을 합친다. |
| `init-machine.py` | LK의 CubeSuite 탐지·post-link·egress 배선 검사와 T_N2의 백신 차단 진단을 공통 구현에 흡수한다. 프로젝트별 정책은 확장 지점에서 받는다. |
| `temp-hygiene.md`, `renesas-rl78` 스킬 | R 버전을 기준으로 삼는다. |
| LK의 host-test 완료 게이트 보류 | 소비 프로젝트 ADR 0005의 선택을 프로젝트 확장 지점에 보존한다. |
| R의 `guard_temp_write.py` 예외 | 프로젝트 확장 지점으로 옮긴다. |
| `misra_gate.py`, egress 차단 | 프로젝트 고유 동작과 배선을 확장 지점에 둔다. |
| host-test 안내와 테스트 도구 | 공통 워크플로우 안내와 재사용 도구는 공통 계층에 둔다. 프로젝트별 테스트 로직은 소비 프로젝트가 소유한다. `test/AGENTS.md`·`test/run_tests.py`·`test/tools/`도 이 경계로 나눈다. |
| 기존 스킬 seed | 뒤처진 seed 대신 프로젝트 쪽 버전을 기준으로 정본을 한 벌로 합친다. setup 안내는 배포 흐름으로 바꾸고 `install.py` 참조를 제거한다. |

[전수 목록](https://github.com/jhwpulsarlube-code/project-templete/blob/research/workflow-asset-inventory/research/workflow-asset-inventory.md)은 비교 자료다. 이후 재확인에서 Optime-LWX1P에도 워크플로우 자산이 있으며 `block_egress.py`와 `misra_gate.py`는 LK와 같았다. 첫 배포 때 현재 파일을 다시 비교하고, 과거의 빈 프로젝트 가정을 적용하지 않는다.

완료 조건은 배포 대상 파일마다 정본이 한 벌이고, 공통 개선과 프로젝트 고유 동작이 위 소유권 경계에 맞게 분리되며, 스킬 참조가 실제 배포 경로를 가리키는 것이다.

근거: [전수 조사](https://github.com/jhwpulsarlube-code/Project-Templete/issues/3#issuecomment-6630979735), [최종 정본 결정](https://github.com/jhwpulsarlube-code/Project-Templete/issues/10#issuecomment-6682110734).

## 8. 전환 순서와 검증

전환은 **Pulsarlube-R → Pulsarlube-T_N2 → Optime-LWX1P → Pulsarlube-LK_BTr** 순서로 진행한다. 각 프로젝트에서 다음 조건을 모두 통과한 뒤 다음으로 진행한다.

- 배포 PR이 병합되고 기존 hook·스킬과 프로젝트 고유 동작이 유지된다.
- 정상 표류 검사가 통과하며, 같은 정본으로 다시 배포하면 변경이 없다.
- 배포본을 수정하면 검사가 막고, 역반영·재배포로 해소할 수 있다.

R의 `tools/skills/scratch-layout/`은 R에 프로젝트 스킬 배포본이 생기고 전환 검증까지 마친 뒤 삭제한다. 사용자 레벨 `~/.claude/skills/scratch-layout`과 `~/.claude/skills/legacy-host-testing`은 네 프로젝트 모두 전환한 뒤 삭제한다.

첫 소비 프로젝트의 구현은 다음 단위로 나눌 수 있다. 앞 단위의 완료 조건을 충족한 뒤 다음 단위를 연결한다.

| 구현 단위 | 완료 조건 |
| --- | --- |
| 정본 수집과 확장 지점 분리 | 7절 확정안이 `common/`과 R의 확장 지점으로 나뉘고, seed 중복과 설치 참조가 정리된다. |
| 진입점과 줄바꿈 계약 | 설정 병합·충돌 거부, hook 전달·실행 순서, 머신 확장 키 충돌과 hooksPath 처리가 2·3절을 충족한다. |
| 배포 기록과 표류 검사 | 원본 저장소 없는 검사, 수정·삭제·줄바꿈 탐지, 생성 파일 판정과 일회성 우회가 5절을 충족한다. |
| sync | 사전 거부·미리보기·첫 배포 충돌·수정 파일 보호·삭제 범위·설정 생성·단일 커밋·재실행 무변경이 4절을 충족한다. |
| reverse | 차이 미리보기·파일별 선택·소유권 분리와 역반영 후 정상 검사 통과가 6절을 충족한다. |
| R 시범 전환 | 이 절의 세 검증 조건을 모두 통과하고 R의 기존 스킬 원본을 정리한다. |

근거: [전환 순서와 정리 시점](https://github.com/jhwpulsarlube-code/Project-Templete/issues/11#issuecomment-6682311891), [설계서 역할](https://github.com/jhwpulsarlube-code/Project-Templete/issues/14#issuecomment-6682366692).
