"""Plan 4c T5: the store's location and path-component checks (ADR 0001 §7 Managed installs)."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from heterodyne.harness import paths

ME = os.getuid()
Lookup = Callable[[Path], tuple[int, int]]


def above_tmp_is_root(tmp_path: Path) -> Lookup:
    """The injected owner lookup: the real owner and mode inside `tmp_path`, and root's 0755 above it, so
    each case is decided by the tree it builds (the host's /tmp is world-writable)."""
    def lookup(p: Path) -> tuple[int, int]:
        if p == tmp_path or tmp_path in p.parents:
            st = os.lstat(p)
            return st.st_uid, st.st_mode
        return 0, 0o40755
    return lookup


def mkdir(p: Path, mode: int = 0o700) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    p.chmod(mode)
    return p


def problems(tmp_path: Path, store: Path, lookup: Lookup | None = None, **overrides: Any) -> list[str]:
    args: dict[str, Any] = {"login_dirs": [tmp_path / "home" / ".codex"], "worktrees": [tmp_path / "repo"],
                            "state_dir": tmp_path / "state", "service_uid": ME, "owner_uid": ME,
                            "fallback": False}
    args.update(overrides)
    return paths.store_problems(store, lookup=lookup or above_tmp_is_root(tmp_path), **args)


def in_login(t: Path) -> list[str]:
    return problems(t, mkdir(t / "home" / ".codex" / "store"))


def login_inside(t: Path) -> list[str]:
    return problems(t, mkdir(t / "store"), login_dirs=[t / "store" / "logins" / ".claude"])


def via_symlink(t: Path) -> list[str]:
    mkdir(t / "home" / ".codex" / "store")
    (t / "link").symlink_to(t / "home" / ".codex")
    return problems(t, t / "link" / "store")


def in_worktree(t: Path) -> list[str]:
    return problems(t, mkdir(t / "repo" / "store"))


def in_state(t: Path) -> list[str]:
    return problems(t, mkdir(t / "state" / "harnesses"))


def group_writable(t: Path) -> list[str]:
    mkdir(t / "shared", 0o770)
    return problems(t, mkdir(t / "shared" / "store"))


def other_writable(t: Path) -> list[str]:
    mkdir(t / "open", 0o707)
    return problems(t, mkdir(t / "open" / "store"))


def owned_by_service_user(t: Path) -> list[str]:
    # Not the fallback: a component the service user owns is one it can rename, so the store is unsafe.
    return problems(t, mkdir(t / "store", 0o755), owner_uid=ME + 1)


def unreadable_component(t: Path) -> list[str]:
    def lookup(p: Path) -> tuple[int, int]:
        if p == t:
            raise PermissionError(13, "denied")
        return above_tmp_is_root(t)(p)
    return problems(t, mkdir(t / "store"), lookup=lookup)


def fallback_not_0700(t: Path) -> list[str]:
    return problems(t, mkdir(t / "store", 0o750), fallback=True, owner_uid=None)


def fallback_not_service_users(t: Path) -> list[str]:
    real = above_tmp_is_root(t)

    def lookup(p: Path) -> tuple[int, int]:
        uid, mode = real(p)
        return (ME + 1, mode) if p == t / "store" else (uid, mode)
    return problems(t, mkdir(t / "store"), lookup=lookup, fallback=True, owner_uid=ME + 1)


def helper_differs(t: Path) -> list[str]:
    (t / "packaged.py").write_text("print(1)\n")
    (t / "installed.py").write_text("print(2)\n")
    return paths.helper_problems(t / "installed.py", t / "packaged.py")


def helper_missing(t: Path) -> list[str]:
    (t / "packaged.py").write_text("print(1)\n")
    return paths.helper_problems(t / "installed.py", t / "packaged.py")


CASES: list[tuple[str, Callable[[Path], list[str]]]] = [
    ("STORE_IN_LOGIN_DIR", in_login), ("STORE_IN_LOGIN_DIR", via_symlink),
    ("LOGIN_DIR_IN_STORE", login_inside),
    ("STORE_IN_WORKTREE", in_worktree),
    ("STORE_IN_STATE_DIR", in_state),
    ("STORE_PATH_WRITABLE", group_writable), ("STORE_PATH_WRITABLE", other_writable),
    ("STORE_PATH_WRITABLE", owned_by_service_user), ("STORE_PATH_WRITABLE", unreadable_component),
    ("STORE_PATH_WRITABLE", fallback_not_0700), ("STORE_PATH_WRITABLE", fallback_not_service_users),
    ("HELPER_DRIFT", helper_differs), ("HELPER_DRIFT", helper_missing),
]


@pytest.mark.parametrize("constant, case", CASES, ids=[f"{c}-{f.__name__}" for c, f in CASES])
def test_store_problems(tmp_path: Path, constant: str, case: Callable[[Path], list[str]]) -> None:
    assert getattr(paths, constant) == constant
    assert case(tmp_path) == [constant]


def test_every_constant_has_a_case() -> None:
    assert {c for c, _ in CASES} == set(paths.PROBLEMS)


def test_a_clean_store_has_no_problems(tmp_path: Path) -> None:
    """The negative controls: each case's tree, minus the one thing that causes it."""
    assert problems(tmp_path, mkdir(tmp_path / "store", 0o755)) == []
    assert problems(tmp_path, tmp_path / "missing" / "store") == []      # created later, by the store
    assert problems(tmp_path, mkdir(tmp_path / "fb"), fallback=True, owner_uid=None) == []
    assert problems(tmp_path, mkdir(tmp_path / "sib"), login_dirs=[tmp_path / "sibling"],
                    worktrees=[tmp_path / "s"], state_dir=tmp_path / "st") == []
    (tmp_path / "packaged.py").write_text("print(1)\n")
    (tmp_path / "installed.py").write_text("print(1)\n")
    assert paths.helper_problems(tmp_path / "installed.py", tmp_path / "packaged.py") == []
