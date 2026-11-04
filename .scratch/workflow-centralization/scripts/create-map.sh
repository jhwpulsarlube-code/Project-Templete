#!/usr/bin/env bash
# wayfinder 맵(GitHub 이슈)과 첫 티켓들을 만든다. 사용자가 직접 실행한다(에이전트의 gh 호출은 egress 정책이 막는다).
# 사용: bash .scratch/workflow-centralization/scripts/create-map.sh
set -euo pipefail

REPO="jhwpulsarlube-code/project-templete"
ME="$(gh api user --jq .login)"
echo "대상 저장소: $(gh repo view "$REPO" --json nameWithOwner --jq .nameWithOwner)"

for l in map research prototype grilling task; do
  gh label create "wayfinder:$l" --repo "$REPO" --color 5319e7 --force >/dev/null
done

num() { basename "$1"; }                                       # 이슈 URL → 번호
dbid() { gh api "repos/$REPO/issues/$1" --jq .id; }            # 번호 → database id
new() {  # new <type> <title>  (본문은 stdin)
  num "$(gh issue create --repo "$REPO" --label "wayfinder:$1" --title "$2" --body-file -)"
}
child() { gh api --method POST "repos/$REPO/issues/$MAP/sub_issues" -F sub_issue_id="$(dbid "$1")" >/dev/null; }
block() { # block <child> <blocker>...
  local c="$1"; shift
  for b in "$@"; do
    gh api --method POST "repos/$REPO/issues/$c/dependencies/blocked_by" -F issue_id="$(dbid "$b")" >/dev/null
  done
}

MAP=$(new map "워크플로우 자산을 원본 저장소로 모아 관리하기" <<'EOF'
(본문은 스크립트 마지막에 채운다)
EOF
)

T01=$(new grilling "범위와 원칙" <<'EOF'
## Question

워크플로우 자산을 원본 저장소로 모아 관리하는 것이 유지보수에 유리한가? 그렇다면 범위, 소비 프로젝트, 계층, 배포본, 표류의 기본 원칙은 무엇인가?
EOF
)

T02=$(new research "현재 워크플로우 자산 전수 목록과 표류 분류" <<'EOF'
## Question

소비 프로젝트 4개(Optime-LWX1P, Pulsarlube-LK_BTr, Pulsarlube-R, Pulsarlube-T_N2)에 있는 워크플로우 자산을 모두 나열하라. 대상은 `.claude/hooks`, `.claude/settings.json`의 hook 연결 설정, `.claude/skills`, `.claude/init-machine.py`, `docs/agents/**`, `.githooks/*`, `tools/issue-status.ps1`이다. 사용자 레벨 설치형 스킬 `~/.claude/skills/scratch-layout`과 `legacy-host-testing`의 seed 파일도 포함한다.

자산마다 다음을 적는다.
- 어느 프로젝트에 있는가
- 내용 해시 그룹(줄바꿈만 다른 경우는 따로 표시)
- 그룹 사이 차이의 요지: 우연한 차이, 한쪽에만 있는 개선, 프로젝트 고유 내용 중 무엇인가
- 공통 계층 후보인가, 프로젝트 계층 후보인가

판정은 하지 않는다. 판정은 "정본 확정"에서 한다.
EOF
)

T03=$(new grilling "배포 메커니즘" <<'EOF'
## Question

원본 저장소의 정본을 소비 프로젝트의 배포본으로 어떻게 옮기는가? 후보는 다음 셋이다.
- 원본 저장소의 sync 스크립트가 각 소비 프로젝트에 파일을 쓰고 브랜치나 PR을 만드는 방식
- git subtree
- 기존 설치형 스킬(`scratch-layout`의 `install.py`) 방식을 확장해 소비 프로젝트 쪽에서 당겨 오는 방식

전제: 배포본은 소비 프로젝트에 커밋되어 단독으로 동작해야 한다(CI, 다른 PC).
EOF
)

T04=$(new grilling "섞일 수밖에 없는 진입점의 확장 지점" <<'EOF'
## Question

`.claude/settings.json`(hook 연결), `.githooks/pre-commit`, `.claude/init-machine.py`는 공통 자산과 프로젝트 고유 내용이 한 파일에 모이는 진입점이다. "파일 단위 소유" 원칙과 어떻게 맞출 것인가? 예: 진입점은 프로젝트가 소유하고 공통 조각을 include하는 방식, 생성 파일로 두는 방식, 설정 파일에 맡기는 방식.
EOF
)

T05=$(new grilling "도메인 계층 유지 여부" <<'EOF'
## Question

nfc_tag와 Docs를 제외하면서 남은 소비 프로젝트는 모두 rl78-firmware다. 공통→도메인→프로젝트 3계층을 그대로 둘 것인가, 아니면 공통+프로젝트 2계층으로 줄이고 도메인은 필요해질 때 도입할 것인가?
EOF
)

T06=$(new grilling "원본 저장소 디렉터리 구조와 사용자 레벨 스킬 연결" <<'EOF'
## Question

원본 저장소 안에서 계층, 자산 종류, 설치형 스킬을 어떤 디렉터리 구조로 둘 것인가? `~/.claude/skills`의 설치형 스킬은 이 저장소와 어떻게 연결되는가(심볼릭 링크, 설치 스크립트 등)?
EOF
)

T07=$(new grilling "표류 탐지와 배포본 버전 표기" <<'EOF'
## Question

배포본이 정본과 달라진 것을 어디서 어떻게 탐지하는가? 후보: 소비 프로젝트의 pre-commit, CI, 원본 저장소 쪽 스캔. 배포본이 어느 정본 버전에서 왔는지는 어떻게 표기하는가? 예: manifest와 해시, 파일 헤더, 커밋 SHA.
EOF
)

T08=$(new grilling "역반영 절차" <<'EOF'
## Question

소비 프로젝트에서 배포본을 고쳤을 때 그 수정을 원본 저장소의 정본으로 끌어올리고 다른 소비 프로젝트로 퍼뜨리는 절차는 무엇인가? 누가, 언제, 어떤 도구로 하며, 역반영되지 않은 표류는 얼마나 오래 허용하는가?
EOF
)

T09=$(new grilling "정본 확정" <<'EOF'
## Question

"현재 워크플로우 자산 전수 목록과 표류 분류"에서 나온 차이마다 무엇으로 판정하는가? 선택지는 정본에 흡수, 확장 지점으로 이전, 폐기다. 기본 원칙은 R 버전을 기본으로 하고 파일마다 더 나은 개선을 흡수하는 것이다.
EOF
)

T10=$(new grilling "전환 순서와 시범 프로젝트" <<'EOF'
## Question

어느 소비 프로젝트를 먼저 전환할 것인가? 나머지는 어떤 순서로 전환하며, 무엇을 확인하면 다음 프로젝트로 넘어가는가?
EOF
)

for t in $T01 $T02 $T03 $T04 $T05 $T06 $T07 $T08 $T09 $T10; do child "$t"; done

block "$T06" "$T03" "$T05"
block "$T07" "$T03"
block "$T08" "$T07"
block "$T09" "$T02" "$T04" "$T05"
block "$T10" "$T06" "$T07" "$T09"

# 이미 결정된 "범위와 원칙"을 해결 처리한다.
gh issue edit "$T01" --repo "$REPO" --add-assignee "$ME" >/dev/null
gh issue comment "$T01" --repo "$REPO" --body-file - <<'EOF'
## Answer

유지보수에 유리하다. 근거는 현재 표류다. 예를 들어 `guard_main_branch.py`의 PowerShell 경로 수정이 R에만 있고 LK_BTr에는 퍼지지 않았다. 또 `triage-labels.md`는 6개 프로젝트에 6개 버전이 있다. 표류의 원인은 이렇다. 각 프로젝트가 따로 출발해 개별적으로 고도화했고, 이후 R을 기준으로 맞추려 했지만 이미 생긴 차이 때문에 조금씩 다르게 굳었다.

- **소비 프로젝트**: Optime-LWX1P, Pulsarlube-LK_BTr, Pulsarlube-R, Pulsarlube-T_N2. 다른 클론인 T_N2-kit-p5-os-log-baseline, 그리고 Pulsarlube-nfc_tag와 Pulsarlube-Docs는 제외한다.
- **워크플로우 자산의 범위**: Claude hook, hook 연결 설정, `docs/agents`, 공통 스킬, `.githooks`, `issue-status.ps1`, `init-machine.py`. 빌드·배포 도구, CI, `CODING_STANDARDS.md`는 제외한다.
- **배포본**: 소비 프로젝트에 커밋한다. 원본 저장소 없이도 CI와 다른 PC에서 단독으로 동작해야 한다.
- **표류**: 확장 지점을 거치지 않은 차이는 결함이다.
- **계층**: 공통→도메인→프로젝트. 도메인 계층을 유지할지는 "도메인 계층 유지 여부"에서 다시 정한다.
- **정본 기준**: R 버전을 기본으로 하고, 파일마다 더 나은 개선을 흡수한다.
- **수정 위치**: 소비 프로젝트에서 고쳐도 된다. 표류를 탐지하고 역반영한 뒤에야 다른 프로젝트로 퍼진다.
- **확장 지점**: hook은 인자나 설정으로 받는다. 문서는 파일 단위로 소유하며, 프로젝트 고유 내용은 별도 파일에 두고 링크한다. `AGENTS.md`는 프로젝트가 소유한다.
- **설치형 스킬**: `scratch-layout`과 `legacy-host-testing`의 정본도 원본 저장소로 옮긴다.

용어는 저장소 루트의 `GLOSSARY.md`를 따른다.
EOF
gh issue close "$T01" --repo "$REPO" >/dev/null

U="https://github.com/$REPO/issues"
gh issue edit "$MAP" --repo "$REPO" --body-file - <<EOF
## Destination

**원본 저장소 운영 설계서(spec)**: 배포 메커니즘, 표류 탐지, 역반영 절차, 배포본 버전 표기, 원본 저장소 구조, 정본 확정안, 전환 순서가 모두 결정된 상태. 이 설계서만 보고 첫 소비 프로젝트의 전환 구현을 티켓으로 쪼갤 수 있으면 도달한 것이다.

## Notes

- 도메인: 에이전트·개발 워크플로우 자산 관리. 용어는 저장소 루트의 \`GLOSSARY.md\`를 따른다.
- 모든 세션은 grilling과 domain-modeling 스킬을 쓰고, 용어가 확정되면 \`GLOSSARY.md\`를 바로 갱신한다.
- 결정만 다룬다. 파일 이동과 전환 구현은 설계서가 나온 뒤 별도로 진행한다.
- 회사 egress 정책 때문에 에이전트는 \`gh\`를 호출할 수 없다. 이슈 조회, 생성, 수정 명령은 사용자가 \`!\` 접두사로 실행한다.
- 소비 프로젝트 경로: \`C:\\Users\\ju171g1-home\\orca\\<프로젝트>\`

## Decisions so far

- [범위와 원칙]($U/$T01): 유지보수에 유리함. 소비 프로젝트 4개(rl78-firmware), 배포본 커밋, 확장 지점 밖의 차이는 표류(결함), R 기준으로 정본 확정, 프로젝트에서 수정 후 역반영

## Not yet specified

- 결정을 ADR로 남길지, 남긴다면 어느 결정을 남길지
- 워크플로우 자산이 거의 없는 Optime-LWX1P(\`docs/agents\` 3개, 13줄짜리 \`AGENTS.md\`)에 어디까지 배포할지
- 설계서 자체의 형식과 위치

## Out of scope

- 새 프로젝트 부트스트랩(템플릿으로 새 저장소 만들기): 배포 메커니즘이 정해지면 "빈 소비 프로젝트에 처음 배포하는 경우"로 다룬다.
- Pulsarlube-nfc_tag, Pulsarlube-Docs, T_N2-kit-p5-os-log-baseline 클론
- 빌드·배포 도구(\`release.py\`), CI 정의(\`host-gates.yml\`), \`CODING_STANDARDS.md\`
- 외부에서 받은 범용 스킬(\`~/.agents/skills\`의 Matt Pocock 스킬 등)
- 실제 전환 구현
EOF

echo
echo "맵: $U/$MAP"
for t in $T01 $T02 $T03 $T04 $T05 $T06 $T07 $T08 $T09 $T10; do echo "  #$t $(gh issue view "$t" --repo "$REPO" --json title,state --jq '.title + " [" + .state + "]"')"; done
