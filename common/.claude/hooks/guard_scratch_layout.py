"""`<root>/<feature>/issues/` 에는 티켓(`NN-<slug>.md`)만, 기능 루트에는 spec·map·ledger 만 새로 만들 수 있다.

PreToolUse(Write) hook. 사용: guard_scratch_layout.py [root]  (root 기본 `.scratch`)
새 파일을 만드는 호출만 막는다. 이미 있는 파일 갱신은 통과시킨다(하위 폴더에 남은 기존 티켓 때문이다).
Bash 리다이렉션으로 만드는 파일은 못 본다.
"""
import json
import os
import re
import sys

TICKET_RE = re.compile(r"^\d{2}[a-z]?-[^/]+\.md$")
FEATURE_DOCS = ("spec.md", "map.md", "ledger.md")
DOC = "docs/agents/scratch-layout.md"


def verdict(payload, root=".scratch"):
    if payload.get("tool_name") != "Write":
        return None
    path = str(payload.get("tool_input", {}).get("file_path", "")).replace("\\", "/")
    if os.path.exists(path):
        return None
    base = r"(?:^|/)%s/[^/]+/" % re.escape(root.strip("/"))
    guide = (f"측정=measure/, 리뷰·변이 검토 JSON=review/, 관찰표·근거=evidence/, 분류·진단=classified/, "
             f"일회성 스크립트=scripts/, 탐색 노트=notes/ (모두 {root}/<feature>/ 아래). "
             f"프로젝트의 \"산출물 위치\" 표({DOC}) 참조.")
    m = re.search(base + r"issues/(.+)$", path)
    if m:
        if TICKET_RE.match(m.group(1)):
            return None
        return f"`issues/` 에는 티켓(NN-<slug>.md)만 새로 만들 수 있다: {m.group(1)}\n" + guide
    m = re.search(base + r"([^/]+)$", path)
    if m and m.group(1) not in FEATURE_DOCS and not m.group(1).startswith("."):
        return f"기능 루트에는 {'·'.join(FEATURE_DOCS)} 만 새로 만들 수 있다: {m.group(1)}\n" + guide
    return None


def main(argv):
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    message = verdict(payload, argv[1] if len(argv) > 1 else ".scratch")
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
