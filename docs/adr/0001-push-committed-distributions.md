# 원본 저장소가 배포본을 push하고 소비 프로젝트에 커밋한다

각 소비 프로젝트에서 따로 개선한 워크플로우 자산은 수정이 다른 프로젝트로 퍼지지 않아 표류했다. 정본을 한곳에서 관리하되 소비 프로젝트가 다른 PC와 CI에서 단독으로 동작하도록, 원본 저장소의 sync가 배포본을 쓰고 소비 프로젝트에 커밋하는 방식을 택한다.

git subtree는 하나의 접두 디렉터리를 전제로 하지만 자산은 `.claude/`, `docs/agents/`, `.githooks/`, `tools/`에 흩어져 있다. 소비 프로젝트가 설치 스크립트로 당겨 오는 방식은 그 스크립트 자체가 프로젝트별로 표류한다. 배포 도구를 원본 저장소에 두면 이 두 제약을 피하면서 소비 프로젝트에는 실행에 필요한 배포본을 남길 수 있다.

그 결과 정본 변경은 소비 프로젝트의 배포 커밋과 PR을 거쳐 반영되며, 전파 전까지 프로젝트별 배포 버전은 다를 수 있다. 운영 계약은 [설계서](../workflow-source-spec.md)의 배포·역반영 절을 따른다.

근거: [범위와 원칙](https://github.com/jhwpulsarlube-code/Project-Templete/issues/2#issuecomment-6630629320), [배포 메커니즘](https://github.com/jhwpulsarlube-code/Project-Templete/issues/4#issuecomment-6631838607).
