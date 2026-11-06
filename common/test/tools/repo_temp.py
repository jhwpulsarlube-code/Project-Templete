"""단위 테스트 픽스처의 임시 폴더를 저장소의 `.repo-temp/unittest/` 아래에 만든다(docs/agents/temp-hygiene.md).

`tempfile.TemporaryDirectory()` 의 대체다. `tempfile.tempdir` 를 전역으로 바꾸지 않는다 — 전역으로 바꾸면 같은
프로세스 안의 격리 사본 도구(red_proof·exhaustive_mutation 등)가 저장소 안에 사본을 만들어 "저장소 밖" 계약이 깨진다.
`with` 를 벗어나거나 `cleanup()` 이 불리면 지워지고, 프로세스가 죽어 남은 폴더는 `.repo-temp/` 가 무시되므로
눈에 띄지 않는다. 오래 남은 것은 손으로 지운다.
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".repo-temp" / "unittest"


def TemporaryDirectory(prefix=None, **kwargs):  # noqa: N802 — tempfile 의 이름을 그대로 쓴다
    BASE.mkdir(parents=True, exist_ok=True)
    # 픽스처가 저장소 안에 있으므로 `.git` 을 지운 픽스처를 git 이 위로 올라가 이 저장소로 찾지 않게 막는다.
    # 값이 이미 있으면 존재하는 정책을 덮지 않는다. 자식 프로세스가 환경을 물려받는다.
    os.environ.setdefault("GIT_CEILING_DIRECTORIES", str(BASE))
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(BASE), **kwargs)
