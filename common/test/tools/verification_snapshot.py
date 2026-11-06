#!/usr/bin/env python3
"""검증 사본 준비: 리비전의 격리 사본을 만들고, 그 사본의 러너·테스트 구성을 골라 한 번에 넘긴다.

호출자는 생성·구성 선정·정리의 순서를 알 필요 없이 정책을 골라 `prepare` 를 요청한다. 사본의 수명(종료 훅,
소유자 파일, 등록 잠금, stale 정리)의 상태는 이 module 하나가 가진다. 이 module 은 전수 변이 도구를 가져오지
않는다 — 전수 변이 도구·red 증명·영향 범위 분석·완료 게이트가 이 module 을 직접 쓴다.

세 정책을 구현한다: 부모 원본(PRISTINE_PARENT: 리비전의 제품·테스트·러너를 그대로), 현재 변경분 전체
(CURRENT_CHANGES: 리비전 사본에 tracked 변경 전체와 test/ 의 untracked 파일을 반영), 부모 제품+현재 테스트
(PARENT_PRODUCT_CURRENT_TESTS: 제품은 리비전 그대로, test/ 의 변경·삭제·untracked 만 반영 — red 증명).
현재 작업 트리의 구성은 사본 없이 `current_configs` 로 읽는다.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
import atexit
import glob
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]

PRISTINE_PARENT = "pristine-parent"
CURRENT_CHANGES = "current-changes"
PARENT_PRODUCT_CURRENT_TESTS = "parent-product-current-tests"
POLICIES = (PRISTINE_PARENT, CURRENT_CHANGES, PARENT_PRODUCT_CURRENT_TESTS)


class ToolError(Exception):
    """도구·환경 오류. kill 로 세지 않고 실행 전체를 중단한다."""


def git(*args, cwd=None):
    cwd = ROOT if cwd is None else cwd
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


# 격리 worktree 정리 (이슈 .scratch/characterization-to-specification/issues/06).
# finally 는 TerminateProcess 같은 강제 종료에서 돌지 않는다. 그래서 세 겹으로 지운다:
#   1) 정상 종료·예외: isolated_worktree 의 finally
#   2) SIGINT·SIGTERM·SIGBREAK·atexit: 아래 종료 훅이 살아 있는 worktree 를 지운다
#   3) 강제 종료: 정리할 수 없으므로 다음 실행의 시작에서 sweep_stale_worktrees 가 지운다.
#      각 임시 폴더의 owner.pid 가 가리키는 프로세스가 죽은 것만 지운다.
# pid 재사용으로 죽은 소유자가 살아 있는 것으로 보이면 그 폴더는 남는다(안전한 쪽의 오류).
WORKTREE_PREFIX = "plr-worktree-"
OWNER_FILE = "owner.pid"
# 초. owner.pid 가 없는 폴더는 만든 지 이만큼 지난 것만 지운다. 이 정리가 없는 체크아웃의 도구가
# 돌고 있는 폴더(수십 분 걸린다)와 생성 직후의 경합을 지우지 않으려고 하루로 잡았다.
ORPHAN_GRACE = 24 * 60 * 60.0

_add_lock = threading.Lock()
_live = set()   # (root, 임시 폴더) — 이 프로세스가 만들어 아직 지우지 않은 것
_state = {"swept": False, "hooked": False}
stopping = threading.Event()   # 종료 신호를 받으면 켠다. 작업자는 뮤턴트 사이에 보고 멈춘다


def pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5   # ERROR_ACCESS_DENIED: 있으나 열 수 없다
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                        ) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)   # Windows 에서는 TerminateProcess 이므로 위 분기에서 처리한다
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _force_writable(function, path, _excinfo):
    os.chmod(path, 0o700)   # git 객체 파일은 읽기 전용이라 Windows 에서 삭제가 막힌다
    function(path)


def remove_worktree(root, temp):
    """임시 폴더 temp 안의 worktree 를 git 에서 등록 해제하고 폴더째 지운다. 이미 없어도 된다."""
    temp = Path(temp)
    if (temp / "worktree").exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(temp / "worktree")],
                       cwd=root, capture_output=True)
    shutil.rmtree(temp, onerror=_force_writable)
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)


def _owner_pid(temp):
    try:
        return int((Path(temp) / OWNER_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def sweep_stale_worktrees(root=None, temp_root=None, grace=ORPHAN_GRACE):
    """소유 프로세스가 죽은 plr-worktree-* 를 지우고 지운 폴더를 돌려준다.

    owner.pid 가 없는 폴더(이 정리 도입 전의 잔여, 또는 생성 직후)는 grace 초가 지난 것만 지운다.
    git 에 등록되지 않은 사본도 같은 정책으로 폴더째 지운다."""
    root = ROOT if root is None else root
    temp_root = Path(tempfile.gettempdir() if temp_root is None else temp_root)
    removed = []
    for temp in sorted(temp_root.glob(WORKTREE_PREFIX + "*")):
        if not temp.is_dir():
            continue
        owner = _owner_pid(temp)
        if owner is None:
            if time.time() - temp.stat().st_mtime < grace:
                continue
        elif pid_alive(owner):
            continue
        try:
            remove_worktree(root, temp)
        except OSError:
            continue   # 다른 프로세스가 쥐고 있다. 시작을 막지 말고 다음 실행에 맡긴다
        removed.append(temp)
    return removed


def _remove_live():
    for entry in list(_live):
        _live.discard(entry)
        try:
            remove_worktree(*entry)
        except OSError:
            pass   # 다른 프로세스가 쥐고 있으면 다음 실행의 sweep 이 지운다


def _exit_on_signal(signum, _frame):
    stopping.set()   # 작업자 스레드가 끝나야 풀의 __exit__ 이 돌아오므로 현재 뮤턴트 뒤에 멈추게 한다
    raise SystemExit(128 + signum)   # 호출 스택의 finally 가 돌고 atexit 이 남은 것을 지운다


def _install_exit_hooks():
    if _state["hooked"]:
        return
    _state["hooked"] = True
    atexit.register(_remove_live)
    if threading.current_thread() is not threading.main_thread():
        return
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                signal.signal(number, _exit_on_signal)
            except (ValueError, OSError):
                pass


@contextmanager
def isolated_worktree(revision, root=None, on_teardown=None):
    """revision 의 detached worktree 를 임시 디렉터리에 만들어 넘기고, 끝나면 지운다.

    on_teardown 은 삭제에 든 초를 받는 호출자의 계측 훅이다."""
    root = ROOT if root is None else root
    _install_exit_hooks()
    if not _state["swept"]:
        _state["swept"] = True
        sweep_stale_worktrees(root)
    temp = Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX))
    entry = (root, temp)
    _live.add(entry)
    try:
        (temp / OWNER_FILE).write_text(str(os.getpid()), encoding="ascii")
        worktree = temp / "worktree"
        with _add_lock:   # 같은 이름("worktree")의 등록이 동시에 같은 관리 폴더를 고르지 않게 한다
            git("worktree", "add", "--detach", str(worktree), revision, cwd=root)
        yield worktree
    finally:
        try:
            began = time.monotonic()
            try:
                remove_worktree(root, temp)
            finally:
                if on_teardown is not None:
                    on_teardown(time.monotonic() - began)
            _live.discard(entry)
        except OSError:
            pass   # 원래 예외를 덮지 않는다. _live 에 남겨 atexit 이 다시 시도하고, 그래도 안 되면 sweep 이 지운다


def normalized(path):
    return os.path.normcase(os.path.realpath(path))


def load_runner(worktree):
    spec = importlib.util.spec_from_file_location("em_runner",
                                                  Path(worktree) / "test" / "run_tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def select_configs(worktree, runner, target):
    """대상 파일을 포함하는 (테스트 TU, variant) 구성. 포함 여부는 grep 이 아니라 러너와 같은
    플래그로 돌린 `gcc -MM` 의존성 목록으로 잰다."""
    wanted = normalized(worktree / target)
    configs = []
    for variant in runner.VARIANTS:
        for test in sorted(glob.glob(str(worktree / "test" / "test_*.c"))):
            base = [runner.CC] + runner.CFLAGS + variant["extra"] + [test]
            deps = subprocess.run(base + ["-MM"], cwd=worktree, capture_output=True,
                                  text=True)
            if deps.returncode:
                raise ToolError("의존성 측정 실패: {}\n{}".format(test, deps.stderr[-2000:]))
            words = deps.stdout.replace("\\\r\n", " ").replace("\\\n", " ").split()
            if wanted in {normalized(word) for word in words[1:]}:
                stem = Path(test).stem
                configs.append({"name": "{}[{}]".format(stem, variant["name"]),
                                "base": base,
                                "exe": "{}-{}.exe".format(stem, variant["name"]),
                                "dependencies": words[1:]})
    if not configs:
        raise ToolError("대상 파일을 포함하는 테스트 구성이 없다: {}".format(target))
    return configs


def current_configs(root, target):
    """현재 작업 트리(사본 없음)의 러너와 target 의 테스트 구성. 구성이 없거나 읽지 못하면 ToolError 다."""
    root = Path(root)
    runner = load_runner(root)
    return runner, select_configs(root, runner, target)


def overlay(root, worktree, revision="HEAD"):
    """revision 대비 root 작업 트리의 tracked 변경 전체와 test/ 의 untracked 파일을 사본에 덮고, 작업 트리에서
    지운 파일은 사본에서도 지운다. 반영한 경로 기록을 돌려준다. root 는 읽기만 한다."""
    root = Path(root)
    changed = git("diff", revision, "--no-renames", "--name-only", cwd=root).splitlines()   # 이름 변경은 옛 이름도 지워야 한다
    untracked = [p for p in git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines()
                 if p.startswith("test/")]
    copied = []
    for relative in sorted(set(changed + untracked)):
        source = root / relative
        if source.is_file():
            destination = worktree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            copied.append(relative)
        elif (worktree / relative).is_file():
            # 작업 트리에서 지운 파일(삭제한 테스트 TU 등)이 사본에 남으면 이미 없는 심볼을 참조해 빌드가 실패한다
            (worktree / relative).unlink()
            copied.append(relative + " (삭제)")
    return copied


def paths_z(root, command, *args):
    """-z 로 받은 경로 목록. 기본 core.quotepath 는 비ASCII 경로를 따옴표로 감싸 파일을 못 찾게 한다.
    -z 는 `--` 뒤에 가면 pathspec 이 되므로 하위 명령 바로 뒤에 둔다."""
    return [path for path in git(command, "-z", *args, cwd=root).split("\0") if path]


def changed_test_paths(root, revision):
    """revision 대비 바뀐(삭제 포함) test/ 경로와 untracked test/ 경로."""
    tracked = paths_z(root, "diff", "--name-only", "--no-renames", revision, "--", "test")
    untracked = paths_z(root, "ls-files", "--others", "--exclude-standard", "--", "test")
    return sorted(set(tracked + untracked)), untracked


def overlay_tests(root, worktree, revision):
    """사본의 test/ 를 root 작업 트리의 test/ 와 같게 만든다(제품은 건드리지 않는다). 반영한 경로(삭제 포함)와
    그중 untracked 경로를 돌려준다. root 는 읽기만 한다."""
    root = Path(root)
    paths, untracked = changed_test_paths(root, revision)
    for path in paths:
        source, destination = root / path, worktree / path
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        elif destination.is_file():
            destination.unlink()
    return paths, untracked


@dataclass
class Snapshot:
    """준비된 사본. path 는 사본 root, copied 는 사본에 반영한 경로 기록(부모 원본은 비어 있다)이다.
    untracked 는 copied 중 현재 작업 트리의 untracked 경로(부모 제품+현재 테스트만 채운다)다.
    target 없이 준비하면 runner 는 None, configs 는 비어 있다."""
    path: Path
    runner: object
    configs: list
    copied: list = field(default_factory=list)
    untracked: list = field(default_factory=list)


@contextmanager
def prepare(root, revision, policy, target=None, on_teardown=None):
    """revision 의 사본을 policy 대로 준비해 넘기고, 블록이 끝나거나 준비·사용 중 실패하면 지운다.

    생성 → 정책의 반영 → (target 이 있으면) 러너 로드 → target 의 구성 선정이 한 요청이다. 러너와 구성의
    절대 경로는 이 사본을 가리킨다. 준비가 실패하면 본문은 돌지 않고 사본은 이미 지워져 있다.
    CURRENT_CHANGES·PARENT_PRODUCT_CURRENT_TESTS 는 요청마다 root 의 변경분을 다시 읽는다(한 번 캡처해
    재사용하지 않는다)."""
    if policy not in POLICIES:
        raise ValueError("알 수 없는 사본 정책: {!r}".format(policy))
    with isolated_worktree(revision, root, on_teardown) as worktree:
        copied, untracked = [], []
        if policy == CURRENT_CHANGES:
            copied = overlay(root, worktree, revision)
        elif policy == PARENT_PRODUCT_CURRENT_TESTS:
            copied, untracked = overlay_tests(root, worktree, revision)
        runner, configs = None, []
        if target is not None:
            runner = load_runner(worktree)
            configs = select_configs(worktree, runner, target)
        yield Snapshot(worktree, runner, configs, copied, untracked)
