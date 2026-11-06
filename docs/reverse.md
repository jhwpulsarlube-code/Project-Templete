# 역반영과 재배포

배포본을 수정한 개발자는 같은 작업 안에서 역반영을 책임진다. 소비 프로젝트 PR을
병합하기 전에 표류를 해소하며 날짜로 정한 유예 기간은 없다. Python 표준 라이브러리와
Git을 사용한다. 원본 저장소에서 실행하며, 원본 작업 트리는 깨끗해야 한다.
소비 프로젝트는 미커밋·커밋된 배포본 수정 모두 읽는다. Git 작업·잠금 중에는 거부한다.
마지막 배포 기록의 정본 커밋을 로컬에서 읽을 수 있어야 하므로 필요하면 원본 저장소를
fetch한다. 다른 클론이나 worktree를 소비 프로젝트로 지정할 수 없다.

```powershell
python -B tools/reverse.py C:\path\consumer
python -B tools/reverse.py C:\path\consumer --common docs/agents/domain.md
python -B tools/reverse.py C:\path\consumer --common docs/agents/domain.md --apply
```

기본 실행과 `--apply` 없는 선택은 파일·인덱스·브랜치를 변경하지 않는다. 마지막 배포
정본과 현재 작업 파일의 바이트 차이를 보여 주며, 삭제와 바이너리 변경도 표시한다.
설정은 현재 소비 프로젝트 입력의 재생성 결과와 비교한다. 최신 정본보다 오래되었어도
마지막 배포 상태를 유지하는 파일은 역반영 대상으로 표시하지 않는다.
`--common PATH`는 반복해서 파일별로 선택한다. 선택하지 않은 차이는 남는다.
정본도 배포 이후 달라졌으면 자동으로 덮어쓰지 않는다. 최신 정본과 소비 프로젝트
변경을 수동으로 조정하고 다시 검토한다. 공통 파일 삭제를 선택하면 정본도 삭제한다.

프로젝트 전용 변경은 먼저 해당 확장 계약에 맞는 내용만 별도 입력 파일로 추출한다.
예를 들어 pre-commit에 추가한 프로젝트 검사를 별도 sh 파일로 준비한다.

```powershell
python -B tools/reverse.py C:\path\consumer --project .githooks/pre-commit .githooks/pre-commit.d/20-project C:\review\project-check.sh
python -B tools/reverse.py C:\path\consumer --project .githooks/pre-commit .githooks/pre-commit.d/20-project C:\review\project-check.sh --apply
```

`--project PATH TARGET INPUT`는 반복 가능하며, 전체 배포본을 확장 파일로 복제하지 않는다.
준비한 INPUT의 전체 내용이 프로젝트 소유 TARGET에 반영되므로 기존 확장 파일을
바꾸면 그 내용도 보존한 후보를 준비한다. 관리되는 배포 경로·기록·설정 경로, 중복 대상,
저장소 밖 경로와 심볼릭 링크는 거부한다. 머신 검사는 `.claude/machine_checks.py`,
Git hook은 `.githooks/<hook>.d/`, 문서는 별도 프로젝트 문서와 링크 등 기존 확장 계약을
따른다. 개발자가 연결과 동작을 확인한다. 자동으로 코드 의미를 분리하거나 링크를 만들지 않는다.
한 파일에 공통·프로젝트 변경이 섞였으면 프로젝트 부분을 먼저 추출하고 배포본에 공통
부분만 남겨 각각 선택한다.

## 생성 설정 분리

`settings.json`은 공통 파일 선택과 프로젝트 파일 선택 모두에서 통째 역복사를 거부한다.
변경을 검토해 공통 입력과 프로젝트 입력의 **전체 후보 JSON**을 준비한다.
공통 옵션을 생략하면 최신 정본의 공통 입력, 프로젝트 옵션을 생략하면 현재
소비 프로젝트의 프로젝트 입력을 사용한다.

```powershell
python -B tools/reverse.py C:\path\consumer --settings-common C:\review\common.json --settings-project C:\review\project.json
python -B tools/reverse.py C:\path\consumer --settings-common C:\review\common.json --settings-project C:\review\project.json --apply
```

최신 정본 생성기로 두 후보를 병합해 현재 `settings.json`과 바이트가 같은지 먼저 검증한다.
충돌이나 불일치는 아무 파일도 쓰지 않고 거부한다. 직접 편집한 설정의 포맷이 다르면
후보를 별도 디렉터리에서 생성기로 병합한 결과를 소비 프로젝트 `settings.json`에 놓고
다시 검토한다. 후보의 공통 값은 `common/.claude/settings.common.json`, 프로젝트 값은
소비 프로젝트 `.claude/settings.project.json`에만 쓴다. 배포본·생성 파일·기록은
역반영 도구가 수정하지 않는다. 프로젝트 입력만 정상 변경한 경우는 소비 프로젝트에서
생성기를 실행하면 되며, 역반영할 표류가 아니다.

## 완료 절차와 전파 시점

1. 선택된 변경을 소유 파일에서 검토한다. 원본 저장소의 정본 변경을 커밋한다.
   프로젝트 확장 지점과 연결도 검토하고 소비 프로젝트에 커밋한다.
2. 소비 프로젝트 기본 브랜치에 재배포 기준과 프로젝트 입력을 준비한다. 기존 sync는
   현재 작업 브랜치가 아닌 최신 기본 브랜치를 사용한다. 중간 표류 커밋이 필요하면
   [Git hook 안내](../common/.githooks/README.md)의 `WORKFLOW_SKIP_DRIFT_ONCE=1`을
   그 커밋 한 번에만 사용한다. 중간 커밋의 표류 상태로 PR을 병합하지 않는다.
   별도 배포 PR에 기본 브랜치 입력을 먼저 준비할 수 없다면 기본 브랜치를 기준으로
   배포 PR을 만들고 작업 브랜치의 확장 변경을 가져와 설정을 재생성한 뒤 검사한다.
3. [기존 배포 절차](deployment.md)를 따른다. 양쪽 작업 트리를 깨끗하게 준비하고,
   `python -B tools/sync.py C:\path\consumer --force --dry-run`으로 남은 표류와 삭제를
   검토한 뒤 `--force`로 배포한다. 이 옵션은 검토한 충돌 보호만 해제하며 정상 검사나
   작업 트리 청결 검사를 우회하지 않는다. 배포 기록 해시는 수동으로 고치지 않는다.
4. 소비 프로젝트에서 `python -B .githooks/check_drift.py`와 프로젝트 검사를 실행한다.
   우회 환경 변수 없이 일반 커밋도 성공해야 한다. 배포본과 배포 기록을 맞춘 배포 PR을
   사람이 만들고 병합하며, 원래 작업 PR에도 이 결과를 합쳐 정상 검사를 확인한 뒤 병합한다.
5. 변경별로 다른 소비 프로젝트에 즉시 전파할지 추후 일괄 전파할지 판단해 변경 설명에
   대상과 시점을 남긴다. 둘 다 같은 sync·배포 PR 흐름을 쓴다. 다른 소비 프로젝트
   전체의 즉시 배포는 이 역반영의 완료 조건이 아니다.

검증: `python -B -m unittest discover -s tests -p test_reverse.py`.
임시 원본·소비 저장소와 로컬 bare 원격에서 역반영·재배포와 우회 없는 정상 검사를 확인한다.
