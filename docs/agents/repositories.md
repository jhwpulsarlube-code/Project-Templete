# 연결 저장소 포인터

홈 레포에서 작업할 수 있는 연결 저장소의 공개 포인터입니다. 필드의 의미와 등록·변경 절차는 [홈 레포 운영 설계서](../home-repo-spec.md) 2절을 따릅니다.

| 고정 저장소 식별자 | Git 원격 주소 | 규칙 문서 경로 |
| --- | --- | --- |
| `pulsarlube-r` | `https://github.com/klt-rnd/Pulsarlube-R.git` | `docs/agents/workflow-project.md` |

규칙 문서 경로는 작업 트리 루트 기준입니다.

## 사내 목록

여기에 없는 저장소의 포인터는 접근이 제어된 사내 목록에 있습니다. 위치와 접근 권한은 팀 내부 채널에서 목록 관리자에게 문의합니다. 목록을 읽을 수 없으면 설계서 3절의 "사내 목록을 읽을 수 없음" 조치를 따릅니다.

## 머신별 경로

작업 트리 경로는 커밋하지 않습니다. 각 머신의 `~/.config/project-templete/repositories.toml`에 고정 저장소 식별자와 기본 작업 트리 루트의 절대 경로를 적습니다.

```toml
[repositories]
pulsarlube-r = "C:/Users/<사용자>/orca/Pulsarlube-R"
```
