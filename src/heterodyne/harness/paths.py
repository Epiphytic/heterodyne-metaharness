"""Where the managed install store may be (ADR 0001 §7 Managed installs). `config check` prints each
problem; a launch refuses on any (`STORE_UNSAFE`, Task 8). Each problem is one of the constants below, so
no message carries a path."""

import hashlib
import os
import stat
from collections.abc import Callable, Iterable
from importlib import resources
from pathlib import Path

STORE_IN_LOGIN_DIR = "STORE_IN_LOGIN_DIR"
LOGIN_DIR_IN_STORE = "LOGIN_DIR_IN_STORE"
STORE_IN_WORKTREE = "STORE_IN_WORKTREE"
WORKTREE_IN_STORE = "WORKTREE_IN_STORE"
STORE_IN_STATE_DIR = "STORE_IN_STATE_DIR"
STATE_DIR_IN_STORE = "STATE_DIR_IN_STORE"
STORE_PATH_WRITABLE = "STORE_PATH_WRITABLE"
HELPER_DRIFT = "HELPER_DRIFT"
PROBLEMS = (STORE_IN_LOGIN_DIR, LOGIN_DIR_IN_STORE, STORE_IN_WORKTREE, WORKTREE_IN_STORE, STORE_IN_STATE_DIR,
            STATE_DIR_IN_STORE, STORE_PATH_WRITABLE, HELPER_DRIFT)
MAX_LINKS = 40                       # symbolic links followed in one walk, as the kernel's own bound


class _Unsafe(Exception):
    """The walk can't show that only root and the store owner can change the store's path."""


def _owner_and_mode(path: Path) -> tuple[int, int]:
    st = os.lstat(path)
    return st.st_uid, st.st_mode


def store_problems(store: Path, *, login_dirs: Iterable[Path], worktrees: Iterable[Path], state_dir: Path,
                   service_uid: int, owner_uid: int | None, fallback: bool,
                   lookup: Callable[[Path], tuple[int, int]] = _owner_and_mode) -> list[str]:
    """The store's problems, in `PROBLEMS` order, each at most once.

    The store's path is walked component by component from `/`, following each symbolic link where it
    appears (at most `MAX_LINKS`). Every directory the walk passes through, the link's own directory and
    the target's included, must be owned by root or the store owner (`owner_uid`, None when that user
    doesn't exist) and not be writable by group or others. Outside the fallback the store owner must not
    be the service user. Under the fallback the service user may own them too, and the store itself must
    be the service user's and 0700. A component that doesn't exist yet ends the walk (the store creates
    it); one that can't be looked up, or a link loop, is `STORE_PATH_WRITABLE`.

    The store, found by that walk, must not be in or contain a login directory, a worktree or the state
    directory (compared resolved, so a link can't hide one inside the other). A path whose resolution can't
    be established (a component that can't be looked up, a link loop) is both of its pair's problems. When
    the store's own can't be, `STORE_PATH_WRITABLE` (already found) is the only problem: it refuses alone."""
    found: set[str] = set()
    root: Path | None
    try:
        root = _walk(store, service_uid, owner_uid, fallback, lookup)
    except _Unsafe:
        found.add(STORE_PATH_WRITABLE)
        root = _real(store)
        if root is None:
            return [STORE_PATH_WRITABLE]
    pairs = [*((login, STORE_IN_LOGIN_DIR, LOGIN_DIR_IN_STORE) for login in login_dirs),
             *((worktree, STORE_IN_WORKTREE, WORKTREE_IN_STORE) for worktree in worktrees),
             (state_dir, STORE_IN_STATE_DIR, STATE_DIR_IN_STORE)]
    for path, store_in, in_store in pairs:
        other = _real(path)
        if other is None or _within(root, other):
            found.add(store_in)
        if other is None or _within(other, root):
            found.add(in_store)
    return [p for p in PROBLEMS if p in found]


def _real(path: Path, links: int = 0) -> Path | None:
    """`path` with every symbolic link followed, dangling ones included; the part that doesn't exist yet
    is kept as written. None when that can't be established: a component that can't be looked up, a link
    loop or more than `MAX_LINKS` links."""
    path = path if path.is_absolute() else Path.cwd() / path
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        pass
    except (OSError, RuntimeError):
        return None
    if path.parent == path:
        return None
    base = _real(path.parent, links)
    if base is None:
        return None
    if path.name == "..":
        return base.parent
    candidate = base / path.name
    try:
        mode = os.lstat(candidate).st_mode
    except FileNotFoundError:
        return candidate
    except OSError:
        return None
    if not stat.S_ISLNK(mode):
        return candidate
    if links >= MAX_LINKS:
        return None
    try:
        target = candidate.readlink()
    except OSError:
        return None
    return _real(base / target, links + 1)        # an absolute target replaces `base`


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _walk(store: Path, service_uid: int, owner_uid: int | None, fallback: bool,
          lookup: Callable[[Path], tuple[int, int]]) -> Path:
    """The store's real path, after checking every component on the way to it. Raises `_Unsafe`."""
    if not fallback and owner_uid == service_uid:
        raise _Unsafe
    owners = {0, *([owner_uid] if owner_uid is not None else []), *([service_uid] if fallback else [])}
    pending = list((store if store.is_absolute() else Path.cwd() / store).parts[1:])   # `..` kept
    current, links = Path("/"), 0
    uid, mode = _look(current, lookup)
    while True:
        if uid not in owners or mode & 0o022:
            raise _Unsafe
        if not pending:
            break
        name = pending.pop(0)
        if name == "..":
            current = current.parent
            uid, mode = _look(current, lookup)
            continue
        try:
            uid, mode = _look(current / name, lookup)
        except FileNotFoundError:
            if ".." in pending:     # it would climb back among directories (and links) the walk never saw
                raise _Unsafe from None
            return current.joinpath(name, *pending)    # the rest is made later, by the store
        if stat.S_ISLNK(mode):
            links += 1
            if links > MAX_LINKS:
                raise _Unsafe
            target = _readlink(current / name)
            pending[:0] = list(target.parts[1:] if target.is_absolute() else target.parts)
            if target.is_absolute():
                current = Path("/")
            uid, mode = _look(current, lookup)
            continue
        current = current / name
    if fallback and (uid != service_uid or mode & 0o7777 != 0o700):
        raise _Unsafe
    return current


def _look(path: Path, lookup: Callable[[Path], tuple[int, int]]) -> tuple[int, int]:
    try:
        return lookup(path)
    except FileNotFoundError:
        raise
    except OSError:
        raise _Unsafe from None


def _readlink(path: Path) -> Path:
    try:
        return path.readlink()
    except OSError:
        raise _Unsafe from None


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
