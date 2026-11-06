"""에이전트가 `main` 을 직접 바꾸지 못하게 한다. `main` 은 PR 로만 바뀐다.

PreToolUse(Bash·PowerShell) hook. 사용: guard_main_branch.py
Windows 세션은 git 을 PowerShell 도구로도 부른다 — Bash 만 보면 그 커밋이 main 에 그대로 들어간다(hot-path-inline 01).
`main` 에 있는 동안의 merge·commit·rebase·reset·cherry-pick·push 와, main 을 가리키는 push·`branch -f`·`update-ref` 를 막는다.
`gh pr merge` 와 `git pull` 은 통과시킨다. 절차: docs/agents/pull-request.md
"""
import json
import re
import subprocess
import sys

DOC = "docs/agents/pull-request.md"
MAIN = r"(?:refs/heads/|origin/)?main"
WRITES_BRANCH = re.compile(r"\bgit\s+(?:-C\s+(\S+)\s+)?(merge|commit|rebase|reset|cherry-pick|revert)\b")
PUSH = re.compile(r"\bgit\s+(?:-C\s+(\S+)\s+)?push\b([^;&|\n]*)")
CHANGE_DIR = re.compile(r"\b(?:cd|Set-Location|pushd)\s+(?:-LiteralPath\s+)?([^\s;&|\"']+)")
MOVES_MAIN = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(?:branch\s+(?:-\w*[fFmM]\w*|--force)\s+%s\b|update-ref\s+(?:-\S+\s+)*refs/heads/main\b)" % MAIN)
COMMAND_TOOLS = ("Bash", "PowerShell")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2(?=\s|$)", re.S)
# PowerShell here-string(`@'…'@`·`@"…"@`). 닫는 표시는 줄 첫 칸에 온다.
HERE_STRING = re.compile(r"@(['\"])[ \t]*\r?\n(.*?)\r?\n\1@", re.S)        # PowerShell here-string: 닫는 표지는 줄 맨 앞
QUOTED = re.compile(r"\"([^\"]*)\"|'([^']*)'")
SWITCHES_TO_MAIN = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|switch)\s+(?:-\S+\s+)*main\b")


def current_branch():
    out = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
    return out.stdout.strip()


def branch_in(path):
    out = subprocess.run(["git", "-C", path, "branch", "--show-current"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def target_branch(cmd, match, session_branch, branch_of):
    """명령이 실제로 바꾸는 저장소의 브랜치. `git -C <경로>` 가 우선이고, 없으면 앞선 `cd <경로>` 다.
    경로를 읽지 못하거나(따옴표 안 경로) 그 경로의 브랜치를 못 알아내면 세션 저장소의 브랜치로 막는 쪽에 둔다."""
    path = match.group(1)
    if path is None:
        cds = CHANGE_DIR.findall(cmd[:match.start()])
        path = cds[-1] if cds else None
    if path is None or path == '""':
        return session_branch
    return branch_of(path.strip("\"'")) or session_branch


SUBEXPRESSION = re.compile(r"\$\(([^()]*)\)")


def here_string_code(m):
    """`@'…'@` 본문은 글자 그대로라 통째로 뺀다. `@"…"@` 는 `$(…)` 안이 실행되므로 그 부분만 남긴다."""
    return "\n".join(SUBEXPRESSION.findall(m.group(2))) if m.group(1) == '"' else ""


def code_of(cmd):
    """heredoc·here-string 본문과 따옴표 안 문자열(커밋 메시지·문서 내용)은 명령이 아니므로 뺀다.
    공백 없는 한 단어(`"main"`·`'HEAD:main'`)는 ref 인자일 수 있어 따옴표만 벗긴다."""
    def unquote(m):
        text = m.group(1) if m.group(1) is not None else m.group(2)
        return text if text and not re.search(r"\s", text) else '""'
    return QUOTED.sub(unquote, HERE_STRING.sub(here_string_code, HEREDOC.sub("", cmd)))


def pushes_main(args, branch):
    refs = [a for a in args.split() if not a.startswith("-")][1:]       # 첫 항목은 remote
    if not refs:
        return branch == "main"
    return any(re.fullmatch(r"(?:\+?[^:]*:)?\+?%s" % MAIN, r) for r in refs)


def verdict(payload, branch, branch_of=branch_in):
    if payload.get("tool_name") not in COMMAND_TOOLS:
        return None
    cmd = code_of(str(payload.get("tool_input", {}).get("command", "")))
    switches = bool(SWITCHES_TO_MAIN.search(cmd))
    why = None
    m = WRITES_BRANCH.search(cmd)
    p = PUSH.search(cmd)
    if m and (switches or target_branch(cmd, m, branch, branch_of) == "main"):
        why = f"`main` 위에서 `git {m.group(2)}` 를 한다"
    elif MOVES_MAIN.search(cmd):
        why = "`main` 브랜치 포인터를 직접 옮긴다"
    elif p:
        pushed_from = "main" if switches else target_branch(cmd, p, branch, branch_of)
        if pushes_main(p.group(2), pushed_from):
            why = "`main` 으로 push 한다"
    if why:
        return f"{why}. main 은 PR 로만 바꾼다: 기능 브랜치에서 작업하고 `gh pr create`·`gh pr merge` 를 쓴다 ({DOC})."
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    message = verdict(payload, current_branch())
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
