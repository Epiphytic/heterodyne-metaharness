"""The home's exec directories (plan 4c Amendment 1, E25): home-relative directories the CLI runs what it
writes into, which the sandbox presents empty and read-only. Before a launch each is made an empty directory
on the host side too: created if missing, cleared if it has content, refused if it is anything else.

The clear is fail-closed and never crosses a mount. Every directory is opened from its parent by fd without
following a link, and every entry's mount ID and device must be the home root's. A pre-scan deletes
nothing; the delete pass checks each directory again and refuses an entry that appeared or vanished. It
runs only after the previous generation has ended, so no CLI from the home is running."""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from heterodyne.admind.redact import redact
from heterodyne.agents.base import AdapterError
from heterodyne.harness.resources.store_core import Refused, Stat, statx_at

EXEC_DIR_NOT_A_DIRECTORY = "the home's {path} isn't a directory"
EXEC_DIR_CROSSES_MOUNT = "the home's {path} crosses a mount"
EXEC_DIR_TOO_LARGE = "the home's {path} holds more than the launcher clears"
EXEC_DIR_NOT_CLEARED = "the home's {path} could not be cleared"

MAX_ENTRIES = 10_000            # entries below one exec directory; at the limit is accepted
MAX_DEPTH = 32                  # an entry's depth below it (a child is 1); at the limit is accepted
MAX_NAMES = 16                  # names kept in a summary
MAX_NAME_CHARS = 128            # each, after escaping

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class Cleared:
    """What one clear removed: counts and the first names, never contents. Task 8 journals it."""
    path: PurePosixPath
    files: int
    links: int
    dirs: int
    bytes: int
    names: tuple[str, ...]


class _Stop(Exception):
    def __init__(self, template: str) -> None:
        super().__init__(template)
        self.template = template


def shown_name(name: str) -> str:
    """A workload's name as a summary keeps it: redacted (admind's one redaction, Codex T8 r2), escaped, then
    cut to `MAX_NAME_CHARS`, in that order, so the cut never splits a credential out of the redactor's
    reach. Idempotent, so wsd applies it again to whatever a runtime reports."""
    text = "".join(c if c.isprintable() else f"\\x{ord(c):02x}" if ord(c) < 0x100 else f"\\u{ord(c):04x}"
                   for c in redact(name))
    return text[:MAX_NAME_CHARS]


def _same(st: Stat, ref: Stat) -> bool:
    return (st.mnt_id, st.dev) == (ref.mnt_id, ref.dev)


def _stat(dirfd: int, name: str, stop: str) -> Stat:
    try:
        return statx_at(dirfd, name)
    except (OSError, Refused):
        raise _Stop(stop) from None


def _open_dir(dirfd: int, name: str, ref: Stat, stop: str, crossed: str) -> int:
    """`name` in `dirfd` as a directory on the reference mount; `crossed` if it is on another."""
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=dirfd)
    except OSError:
        raise _Stop(stop) from None
    try:
        if not _same(_stat(fd, "", stop), ref):
            raise _Stop(crossed)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _names(fd: int) -> list[str]:
    return sorted(os.listdir(fd))


@dataclass
class _Scan:
    files: int = 0
    links: int = 0
    dirs: int = 0
    bytes: int = 0
    seen: int = 0
    names: list[str] = field(default_factory=list[str])
    children: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])  # name -> kind


def _scan(fd: int, rel: str, depth: int, ref: Stat, scan: _Scan) -> None:
    try:
        names = _names(fd)
    except OSError:
        raise _Stop(EXEC_DIR_NOT_CLEARED) from None
    kinds = scan.children[rel] = {}
    for name in names:
        scan.seen += 1
        if scan.seen > MAX_ENTRIES or depth > MAX_DEPTH:
            raise _Stop(EXEC_DIR_TOO_LARGE)
        path = f"{rel}/{name}" if rel else name
        if len(scan.names) < MAX_NAMES:
            scan.names.append(shown_name(path))
        st = _stat(fd, name, EXEC_DIR_NOT_CLEARED)
        if not _same(st, ref):                       # a mount on a directory, or a file bound over a file
            raise _Stop(EXEC_DIR_CROSSES_MOUNT)
        kinds[name] = st.kind
        if st.kind == "dir":
            scan.dirs += 1
            inner = _open_dir(fd, name, ref, EXEC_DIR_NOT_CLEARED, EXEC_DIR_CROSSES_MOUNT)
            try:
                _scan(inner, path, depth + 1, ref, scan)
            finally:
                os.close(inner)
        elif st.kind == "link":
            scan.links += 1
        else:
            scan.files += 1
            scan.bytes += st.size


def _delete(fd: int, rel: str, ref: Stat, scan: _Scan) -> None:
    """Empty `fd`, which must hold exactly what the scan saw there. Nothing is opened by path."""
    try:
        names = _names(fd)
    except OSError:
        raise _Stop(EXEC_DIR_NOT_CLEARED) from None
    kinds = scan.children.get(rel, {})
    if names != list(kinds):
        raise _Stop(EXEC_DIR_NOT_CLEARED)
    for name in names:
        path = f"{rel}/{name}" if rel else name
        st = _stat(fd, name, EXEC_DIR_NOT_CLEARED)
        if not _same(st, ref) or st.kind != kinds[name]:     # a mount, or a directory swapped for a link
            raise _Stop(EXEC_DIR_NOT_CLEARED)
        try:
            if st.kind == "dir":
                inner = _open_dir(fd, name, ref, EXEC_DIR_NOT_CLEARED, EXEC_DIR_NOT_CLEARED)
                try:
                    _delete(inner, path, ref, scan)
                finally:
                    os.close(inner)
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)          # a link is removed, its target untouched
        except OSError:
            raise _Stop(EXEC_DIR_NOT_CLEARED) from None


def _parent(home_fd: int, parts: Sequence[str], ref: Stat, path: PurePosixPath) -> int:
    """The exec directory's parent, each component created 0700 if missing and on the home's mount."""
    fd = os.dup(home_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            except OSError:
                raise AdapterError("the synthetic home holds a path the host won't follow") from None
            try:
                inner = os.open(part, DIR_FLAGS, dir_fd=fd)
            except OSError:
                raise AdapterError("the synthetic home holds a path the host won't follow") from None
            os.close(fd)
            fd = inner
            if not _same(_stat(fd, "", EXEC_DIR_NOT_CLEARED), ref):
                raise _Stop(EXEC_DIR_CROSSES_MOUNT)
    except _Stop as stop:
        os.close(fd)
        raise AdapterError(stop.template.format(path=path)) from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _one(home_fd: int, path: PurePosixPath, ref: Stat, between: Callable[[], None] | None) -> Cleared | None:
    *parents, leaf = path.parts
    parent = _parent(home_fd, parents, ref, path)
    try:
        try:
            st = statx_at(parent, leaf)
        except FileNotFoundError:
            try:
                os.mkdir(leaf, 0o700, dir_fd=parent)
            except OSError:
                raise _Stop(EXEC_DIR_NOT_CLEARED) from None
            st = _stat(parent, leaf, EXEC_DIR_NOT_CLEARED)
        except (OSError, Refused):
            raise _Stop(EXEC_DIR_NOT_CLEARED) from None
        if st.kind != "dir":
            raise _Stop(EXEC_DIR_NOT_A_DIRECTORY)
        fd = _open_dir(parent, leaf, ref, EXEC_DIR_NOT_A_DIRECTORY, EXEC_DIR_CROSSES_MOUNT)
        try:
            scan = _Scan()
            _scan(fd, "", 1, ref, scan)
            if scan.seen == 0:
                return None
            if between is not None:
                between()
            _delete(fd, "", ref, scan)
        finally:
            os.close(fd)
        final = _open_dir(parent, leaf, ref, EXEC_DIR_NOT_CLEARED, EXEC_DIR_NOT_CLEARED)
        try:
            if os.listdir(final):  # noqa: PTH208 - by descriptor, never by path
                raise _Stop(EXEC_DIR_NOT_CLEARED)
        except OSError:
            raise _Stop(EXEC_DIR_NOT_CLEARED) from None
        finally:
            os.close(final)
        return Cleared(path, scan.files, scan.links, scan.dirs, scan.bytes, tuple(scan.names))
    except _Stop as stop:
        raise AdapterError(stop.template.format(path=path)) from None
    finally:
        os.close(parent)


def prepare(home: Path, paths: Sequence[PurePosixPath], *,
            between: Callable[[], None] | None = None) -> tuple[Cleared, ...]:
    """Make each of `paths` (relative to `home`) an empty directory, in order, and return what was cleared.
    `between` runs between a clear's scan and its delete pass (tests only). Raises AdapterError."""
    try:
        home_fd = os.open(home, DIR_FLAGS)
    except OSError:
        raise AdapterError("the synthetic home can't be opened") from None
    try:
        try:
            ref = statx_at(home_fd, "")
        except (OSError, Refused):
            raise AdapterError("the synthetic home can't be opened") from None
        cleared = [c for p in paths if (c := _one(home_fd, p, ref, between)) is not None]
    finally:
        os.close(home_fd)
    return tuple(cleared)
