"""tdd·implement 스킬이 시작될 때 CODING_STANDARDS.md 의 구현 관련 절을 모델 문맥에 주입한다.

- PreToolUse(Skill): 모델이 Skill 도구로 tdd/implement/implement-spec 을 호출할 때.
- UserPromptSubmit: 사용자가 /tdd, /implement, /implement-spec 을 직접 입력할 때
  (직접 입력은 Skill 도구 호출이 아니라서 PreToolUse 에 잡히지 않는다).

절은 번호가 아니라 제목 키워드로 고른다 — 절 순서가 바뀌어도 이 파일을 고칠 필요가 없다.
리뷰 전용 절(커밋 종류, 변이 증거)은 주입하지 않는다.
"""
import json
import os
import re
import sys

SKILLS = ("tdd", "implement", "implement-spec")
SECTION_KEYWORDS = ("ISR", "타깃", "생성 코드", "테스트 품질", "C 주석")
SKILL_RE = re.compile(r"(?:^|:)(?:%s)$" % "|".join(SKILLS))
SLASH_RE = re.compile(r"^\s*/(?:[\w-]+:)?(?:%s)(?:\s|$)" % "|".join(SKILLS))


def triggered(payload):
    event = payload.get("hook_event_name")
    if event == "PreToolUse":
        if payload.get("tool_name") != "Skill":
            return False
        return bool(SKILL_RE.search(str(payload.get("tool_input", {}).get("skill", ""))))
    if event == "UserPromptSubmit":
        return bool(SLASH_RE.match(str(payload.get("prompt", ""))))
    return False


def pick_sections(text):
    parts = re.split(r"(?m)^(?=## )", text)
    return [p.rstrip() for p in parts[1:]
            if any(k in p.splitlines()[0] for k in SECTION_KEYWORDS)]


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    if not triggered(payload):
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    path = os.path.join(root, "CODING_STANDARDS.md")
    with open(path, encoding="utf-8") as f:
        sections = pick_sections(f.read())
    if not sections:
        return
    context = ("구현 전에 아래 코딩 표준(CODING_STANDARDS.md 발췌)을 적용한다. "
               "전체는 리뷰 기준 파일이다.\n\n" + "\n\n".join(sections))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": payload["hook_event_name"],
        "additionalContext": context}}))


if __name__ == "__main__":
    main()
