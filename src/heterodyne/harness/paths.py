"""Where the managed install store may be (ADR 0001 §7 Managed installs). `config check` prints each
problem; a launch refuses on any (`STORE_UNSAFE`, Task 8). Each problem is one of the constants below, so
no message carries a path."""

import hashlib
import os
from collections.abc import Callable, Iterable
from importlib import resources
from pathlib import Path

STORE_IN_LOGIN_DIR = "STORE_IN_LOGIN_DIR"
LOGIN_DIR_IN_STORE = "LOGIN_DIR_IN_STORE"
STORE_IN_WORKTREE = "STORE_IN_WORKTREE"
STORE_IN_STATE_DIR = "STORE_IN_STATE_DIR"
STORE_PATH_WRITABLE = "STORE_PATH_WRITABLE"
HELPER_DRIFT = "HELPER_DRIFT"
PROBLEMS = (STORE_IN_LOGIN_DIR, LOGIN_DIR_IN_STORE, STORE_IN_WORKTREE, STORE_IN_STATE_DIR,
            STORE_PATH_WRITABLE, HELPER_DRIFT)


def _owner_and_mode(path: Path) -> tuple[int, int]:
    st = os.lstat(path)
    return st.st_uid, st.st_mode


def store_problems(store: Path, *, login_dirs: Iterable[Path], worktrees: Iterable[Path], state_dir: Path,
                   service_uid: int, owner_uid: int | None, fallback: bool,
                   lookup: Callable[[Path], tuple[int, int]] = _owner_and_mode) -> list[str]:
    """The store's problems, in `PROBLEMS` order, each at most once.

    Paths are compared resolved (symlinks followed, `strict=False`), so a link can't hide a store inside a
    login directory. Each existing component of the resolved store, from `/` down, must be owned by root
    or the store owner (`owner_uid`, None when that user doesn't exist), and not be writable by group or
    others. Under the fallback the service user may own them too, and the store itself must be the
    service user's and 0700. A component that doesn't exist yet ends the walk: the store creates it. One
    that can't be looked up is a problem."""
    root = store.resolve(strict=False)
    found: set[str] = set()
    for login in (d.resolve(strict=False) for d in login_dirs):
        if _within(root, login):
            found.add(STORE_IN_LOGIN_DIR)
        if _within(login, root):
            found.add(LOGIN_DIR_IN_STORE)
    if any(_within(root, w.resolve(strict=False)) for w in worktrees):
        found.add(STORE_IN_WORKTREE)
    if _within(root, state_dir.resolve(strict=False)):
        found.add(STORE_IN_STATE_DIR)
    if not _components_safe(root, service_uid, owner_uid, fallback, lookup):
        found.add(STORE_PATH_WRITABLE)
    return [p for p in PROBLEMS if p in found]


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _components_safe(root: Path, service_uid: int, owner_uid: int | None, fallback: bool,
                     lookup: Callable[[Path], tuple[int, int]]) -> bool:
    owners = {0, *([owner_uid] if owner_uid is not None else []), *([service_uid] if fallback else [])}
    uid, mode = -1, 0
    for component in [*reversed(root.parents), root]:
        try:
            uid, mode = lookup(component)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if uid not in owners or mode & 0o022:
            return False
    return not fallback or (uid == service_uid and mode & 0o7777 == 0o700)   # the loop ended on the store


def packaged_helper() -> Path:
    """The helper as this checkout ships it (Task 2); root installs a copy at `[harnesses] helper_copy`."""
    return Path(str(resources.files("heterodyne.harness"))) / "resources" / "store_core.py"


def helper_problems(installed: Path, packaged: Path) -> list[str]:
    """`HELPER_DRIFT` unless the installed copy's sha256 equals the packaged file's. A copy that can't be
    read (or a missing packaged file) drifts too: nothing proves the helper is the one this checkout runs."""
    try:
        same = _sha256(installed) == _sha256(packaged)
    except OSError:
        same = False
    return [] if same else [HELPER_DRIFT]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
