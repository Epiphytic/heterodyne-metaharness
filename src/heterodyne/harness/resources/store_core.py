"""The managed install store's core (ADR r16 §7 Managed installs; plan 4c E1, E8): the manifest and its
canonical encoding and digest, `statx`, the fd walk, the record and the fast verification. Standard library
only, and nothing from `heterodyne`: root installs a copy of this one file that the service user can't
write, runs it with `python3 -I` as the store helper, and `wsd` imports the same file for the fallback and
for verification, so there is one manifest implementation.

The walk never follows a link and never leaves the tree's mount. Every directory is opened relative to its
parent's fd (`O_NOFOLLOW`), every fact comes from `statx` on that fd, and every fd opened here is closed
in a `finally`. Refusals carry their constant as `.constant`, and their message is the constant alone:
nothing here names a path, a digest or a file's content in an error.
"""

import contextlib
import ctypes
import dataclasses
import errno
import hashlib
import json
import os
import stat
from collections.abc import Callable, Generator
from typing import Literal, cast

MAX_ENTRIES = 200_000           # entries in one install, the root uncounted; at the limit is accepted
MAX_DEPTH = 64                  # components in an entry's path; at the limit is accepted
MAX_LINK_HOPS = 40              # links followed resolving one link, the link itself included
FORMAT = 1
CHUNK = 1 << 20

TOO_MANY_ENTRIES = "the install has more entries than the store accepts"
TOO_DEEP = "the install is nested deeper than the store accepts"
BAD_TYPE = "the install holds an entry that is not a directory, a regular file or a symbolic link"
BAD_NAME = "the install holds an entry whose name is not UTF-8"
HARD_LINK = "the install holds a hard link"
BAD_LINK = "the install holds a link that is absolute, dangles, loops or leaves the install"
NO_MOUNT_ID = "statx returned no mount ID"
CROSSES_MOUNT = "the install crosses a mount"
ENTRY_CHANGED = "an install entry changed"
ENTRY_ADDED = "the install has an entry its manifest does not"
ENTRY_MISSING = "the install lacks an entry its manifest has"
CONTENT_CHANGED = "an install file's content changed"
MANIFEST_MALFORMED = "the manifest is not in its canonical form"

Kind = Literal["dir", "file", "link"]
KINDS: tuple[Kind, ...] = ("dir", "file", "link")
SourceKind = Literal["release", "npm", "git"]
SOURCE_KINDS: tuple[SourceKind, ...] = ("release", "npm", "git")


class Refused(Exception):
    """A refusal with a fixed constant, which is also its whole message."""

    def __init__(self, constant: str) -> None:
        super().__init__(constant)
        self.constant = constant


class WalkRefused(Refused):
    """The tree can't be walked as an install: its shape, a link, a mount, or a change during the walk."""


class Mismatch(Refused):
    """The tree, or a manifest's bytes, is not what the manifest and the record say."""


@dataclasses.dataclass(frozen=True)
class Entry:
    path: str                   # relative, `/`-separated, never empty: the root isn't an entry
    kind: Kind
    mode: int
    size: int
    nlink: int
    sha256: str | None          # a file's content, hex; None for a directory or a link
    target: str | None          # a link's target; None for a directory or a file


@dataclasses.dataclass(frozen=True)
class Source:
    part: str
    kind: SourceKind
    name: str
    ref: str
    checksum: str
    origin: str
    signature_available: bool
    signature_verified: bool    # always False in v1 (plan 4c E5): `parse` refuses True


@dataclasses.dataclass(frozen=True)
class Manifest:
    adapter: str
    version: str
    target: str
    entry_point: str
    sources: tuple[Source, ...]
    entries: tuple[Entry, ...]
    format: int = FORMAT


# --- the canonical encoding and the digest ---

def _path_key(path: str) -> bytes:
    return path.encode("utf-8")


def canonical(m: Manifest) -> bytes:
    """UTF-8 JSON with sorted keys and no whitespace, non-ASCII written raw, and the entries sorted by
    their path's UTF-8 bytes. The sources keep their order."""
    doc = dataclasses.asdict(m)
    doc["entries"] = sorted(doc["entries"], key=lambda e: _path_key(e["path"]))
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(m: Manifest) -> str:
    return "sha256:" + hashlib.sha256(canonical(m)).hexdigest()


ENTRY_FIELDS = frozenset(f.name for f in dataclasses.fields(Entry))
SOURCE_FIELDS = frozenset(f.name for f in dataclasses.fields(Source))
MANIFEST_FIELDS = frozenset(f.name for f in dataclasses.fields(Manifest))
HEX = frozenset("0123456789abcdef")


def valid_path(path: object) -> bool:
    """A relative, `/`-separated path with no empty, `.` or `..` component and no NUL."""
    if not isinstance(path, str) or not path or "\0" in path:
        return False
    return all(c not in ("", ".", "..") for c in path.split("/"))


def _text(v: object) -> str:
    if not isinstance(v, str) or not v:
        raise Mismatch(MANIFEST_MALFORMED)
    return v


def _count(v: object) -> int:
    if type(v) is not int or v < 0:
        raise Mismatch(MANIFEST_MALFORMED)
    return v


def _flag(v: object) -> bool:
    if type(v) is not bool:
        raise Mismatch(MANIFEST_MALFORMED)
    return v


def _object(v: object, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(v, dict):
        raise Mismatch(MANIFEST_MALFORMED)
    d = cast(dict[str, object], v)                      # JSON object keys are always strings
    if frozenset(d) != fields:
        raise Mismatch(MANIFEST_MALFORMED)
    return d


def _list(v: object) -> list[object]:
    if not isinstance(v, list):
        raise Mismatch(MANIFEST_MALFORMED)
    return cast(list[object], v)


def _entry(v: object) -> Entry:
    d = _object(v, ENTRY_FIELDS)
    path, kind, sha, target = d["path"], d["kind"], d["sha256"], d["target"]
    if not valid_path(path) or kind not in KINDS:
        raise Mismatch(MANIFEST_MALFORMED)
    mode = _count(d["mode"])
    if mode > 0o7777:
        raise Mismatch(MANIFEST_MALFORMED)
    if kind == "file":
        if not isinstance(sha, str) or len(sha) != 64 or not set(sha) <= HEX or target is not None:
            raise Mismatch(MANIFEST_MALFORMED)
    elif kind == "link":
        if sha is not None or not isinstance(target, str) or not target:
            raise Mismatch(MANIFEST_MALFORMED)
    elif sha is not None or target is not None:
        raise Mismatch(MANIFEST_MALFORMED)
    return Entry(str(path), kind, mode, _count(d["size"]), _count(d["nlink"]),
                 sha if isinstance(sha, str) else None, target if isinstance(target, str) else None)


def _source(v: object) -> Source:
    d = _object(v, SOURCE_FIELDS)
    kind = d["kind"]
    if kind not in SOURCE_KINDS or _flag(d["signature_verified"]):
        raise Mismatch(MANIFEST_MALFORMED)
    return Source(_text(d["part"]), kind, _text(d["name"]), _text(d["ref"]),
                  _text(d["checksum"]), _text(d["origin"]), _flag(d["signature_available"]), False)


def parse(data: bytes) -> Manifest:
    """The manifest `data` encodes, refusing (`MANIFEST_MALFORMED`) anything `canonical` would not have
    produced byte for byte: another layout, an unknown or missing field, a value of the wrong type, an
    unknown format, a duplicate path, or a verified signature, which v1 never claims."""
    try:
        doc = _object(json.loads(data.decode("utf-8")), MANIFEST_FIELDS)
        if doc["format"] != FORMAT or type(doc["format"]) is not int:
            raise Mismatch(MANIFEST_MALFORMED)
        entry_point = doc["entry_point"]
        if not valid_path(entry_point):
            raise Mismatch(MANIFEST_MALFORMED)
        m = Manifest(_text(doc["adapter"]), _text(doc["version"]), _text(doc["target"]), str(entry_point),
                     tuple(_source(s) for s in _list(doc["sources"])),
                     tuple(_entry(e) for e in _list(doc["entries"])))
        if len({e.path for e in m.entries}) != len(m.entries) or canonical(m) != data:
            raise Mismatch(MANIFEST_MALFORMED)
    except (ValueError, RecursionError):     # not UTF-8, not JSON, too deep, or a value JSON can't encode
        raise Mismatch(MANIFEST_MALFORMED) from None
    return m


# --- statx(2) through libc: the mount ID and the change time in nanoseconds, which os.stat lacks (E8) ---

AT_SYMLINK_NOFOLLOW = 0x100
AT_EMPTY_PATH = 0x1000
STATX_BASIC_STATS = 0x7FF
STATX_MNT_ID = 0x1000


class _Ts(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("_r", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    _fields_ = [("mask", ctypes.c_uint32), ("blksize", ctypes.c_uint32), ("attributes", ctypes.c_uint64),
                ("nlink", ctypes.c_uint32), ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32),
                ("mode", ctypes.c_uint16), ("_s0", ctypes.c_uint16), ("ino", ctypes.c_uint64),
                ("size", ctypes.c_uint64), ("blocks", ctypes.c_uint64), ("attributes_mask", ctypes.c_uint64),
                ("atime", _Ts), ("btime", _Ts), ("ctime", _Ts), ("mtime", _Ts),
                ("rdev_major", ctypes.c_uint32), ("rdev_minor", ctypes.c_uint32),
                ("dev_major", ctypes.c_uint32), ("dev_minor", ctypes.c_uint32),
                ("mnt_id", ctypes.c_uint64), ("_pad", ctypes.c_uint64 * 13)]


_libc = ctypes.CDLL(None, use_errno=True)
_statx = getattr(_libc, "statx", None)
if _statx is not None:
    _statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
    _statx.restype = ctypes.c_int


@dataclasses.dataclass(frozen=True)
class Stat:
    kind: str                   # "dir", "file" or "link", else what the walk refuses ("fifo", "char", ...)
    mode: int
    size: int
    nlink: int
    dev: int
    ino: int
    ctime_ns: int
    mnt_id: int

    @property
    def triple(self) -> tuple[int, int, int]:
        return (self.dev, self.ino, self.ctime_ns)


def _kind(mode: int) -> str:
    for test, name in ((stat.S_ISDIR, "dir"), (stat.S_ISREG, "file"), (stat.S_ISLNK, "link"),
                       (stat.S_ISFIFO, "fifo"), (stat.S_ISSOCK, "socket"), (stat.S_ISCHR, "char"),
                       (stat.S_ISBLK, "block")):
        if test(mode):
            return name
    return "unknown"


def statx_at(dirfd: int, name: str) -> Stat:
    """`name` in the directory `dirfd`, never following a link; `""` is `dirfd` itself. Refuses
    (`NO_MOUNT_ID`) where the kernel or libc can't give the mount ID."""
    if _statx is None:
        raise WalkRefused(NO_MOUNT_ID)
    buf = _Statx()
    flags = AT_SYMLINK_NOFOLLOW | (AT_EMPTY_PATH if name == "" else 0)
    if _statx(dirfd, os.fsencode(name), flags, STATX_BASIC_STATS | STATX_MNT_ID, ctypes.byref(buf)) != 0:
        raise OSError(ctypes.get_errno(), "statx")
    if not buf.mask & STATX_MNT_ID:
        raise WalkRefused(NO_MOUNT_ID)
    return Stat(_kind(buf.mode), stat.S_IMODE(buf.mode), buf.size, buf.nlink,
                os.makedev(buf.dev_major, buf.dev_minor), buf.ino,
                buf.ctime.tv_sec * 1_000_000_000 + buf.ctime.tv_nsec, buf.mnt_id)


# --- the walk ---

GONE = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)      # an entry that vanished or changed type mid-walk
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC     # a FIFO never blocks the open


def _open(name: str, flags: int, dirfd: int) -> int:
    """`os.open` relative to `dirfd`, with an error that names no path."""
    try:
        return os.open(name, flags, dir_fd=dirfd)
    except OSError as exc:
        raise OSError(exc.errno, "open") from None


def _names(dirfd: int) -> list[str]:
    """The directory's entry names, sorted by their bytes. Refuses a name that isn't UTF-8, and stops
    listing once one directory alone holds more than MAX_ENTRIES."""
    names: list[str] = []
    try:
        with os.scandir(dirfd) as it:
            for e in it:
                names.append(e.name)
                if len(names) > MAX_ENTRIES:
                    raise WalkRefused(TOO_MANY_ENTRIES)
    except OSError as exc:
        raise OSError(exc.errno, "scandir") from None
    for name in names:
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            raise WalkRefused(BAD_NAME) from None
    return sorted(names, key=os.fsencode)


def _same(a: Stat, b: Stat) -> bool:
    return (a.kind, a.dev, a.ino, a.mnt_id) == (b.kind, b.dev, b.ino, b.mnt_id)


def walk(root_fd: int) -> Generator[tuple[str, Stat, str | None]]:
    """Every entry under the directory `root_fd`, depth first with each directory's names sorted by their
    bytes, as `(path, stat, link target)`. Follows no link and opens nothing but directories. Refuses
    (`WalkRefused`) an entry that is not a directory, a file or a link, a hard link, a name that isn't
    UTF-8, more than MAX_ENTRIES entries, a path deeper than MAX_DEPTH, an entry on another mount than the
    root, a directory that changed while it was walked, and, once the walk is done, a link that doesn't
    resolve inside the tree."""
    root = statx_at(root_fd, "")
    if root.kind != "dir":
        raise WalkRefused(BAD_TYPE)
    kinds: dict[str, str] = {}
    targets: dict[str, str] = {}
    count = 0
    # Each frame: the directory's fd (the root's is the caller's), its path, its statx, its sorted names
    # and the index of the next one.
    stack: list[tuple[int, str, Stat, list[str], int]] = [(root_fd, "", root, _names(root_fd), 0)]
    try:
        while stack:
            dirfd, prefix, before, names, i = stack[-1]
            if i == len(names):
                stack.pop()
                try:
                    after = statx_at(dirfd, "")
                finally:
                    if dirfd != root_fd:
                        os.close(dirfd)
                if after != before:             # it changed while it was walked: an entry may be unseen
                    raise WalkRefused(ENTRY_CHANGED)
                continue
            stack[-1] = (dirfd, prefix, before, names, i + 1)
            name = names[i]
            path = f"{prefix}/{name}" if prefix else name
            count += 1
            if count > MAX_ENTRIES:
                raise WalkRefused(TOO_MANY_ENTRIES)
            if path.count("/") + 1 > MAX_DEPTH:
                raise WalkRefused(TOO_DEEP)
            st = _statx_entry(dirfd, name)
            if st.mnt_id != root.mnt_id:
                raise WalkRefused(CROSSES_MOUNT)
            if st.kind not in KINDS:
                raise WalkRefused(BAD_TYPE)
            if st.kind != "dir" and st.nlink != 1:
                raise WalkRefused(HARD_LINK)
            kinds[path] = st.kind
            target = None
            if st.kind == "link":
                target = _readlink(dirfd, name)
                targets[path] = target
            elif st.kind == "dir":
                child = _open_dir(dirfd, name, st)
                try:
                    names_below = _names(child)
                except BaseException:
                    os.close(child)
                    raise
                stack.append((child, path, st, names_below, 0))
            yield path, st, target
        _check_links(kinds, targets)
    finally:
        for dirfd, *_ in stack:
            if dirfd != root_fd:
                os.close(dirfd)


def _statx_entry(dirfd: int, name: str) -> Stat:
    try:
        return statx_at(dirfd, name)
    except OSError as exc:
        if exc.errno in GONE:
            raise WalkRefused(ENTRY_CHANGED) from None
        raise


def _readlink(dirfd: int, name: str) -> str:
    try:
        target = os.readlink(name, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (*GONE, errno.EINVAL):
            raise WalkRefused(ENTRY_CHANGED) from None
        raise OSError(exc.errno, "readlink") from None
    try:
        target.encode("utf-8")
    except UnicodeEncodeError:
        raise WalkRefused(BAD_LINK) from None
    return target


def _open_dir(dirfd: int, name: str, st: Stat) -> int:
    """The directory `name`, opened without following a link and confirmed to be the one `st` describes."""
    try:
        fd = _open(name, DIR_FLAGS, dirfd)
    except OSError as exc:
        if exc.errno in GONE:
            raise WalkRefused(ENTRY_CHANGED) from None
        raise
    try:
        if not _same(statx_at(fd, ""), st):
            raise WalkRefused(ENTRY_CHANGED)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _check_links(kinds: dict[str, str], targets: dict[str, str]) -> None:
    """Every link resolves, component by component against the walked tree (no filesystem access), to an
    entry inside it or to its root, following at most MAX_LINK_HOPS links."""
    for path, target in targets.items():
        _resolve(kinds, targets, path.split("/")[:-1], target)


def _resolve(kinds: dict[str, str], targets: dict[str, str], parent: list[str], target: str) -> None:
    hops = 1
    here = list(parent)
    if not target or target.startswith("/"):
        raise WalkRefused(BAD_LINK)
    pending = target.split("/")
    pending.reverse()                  # a stack: the next component is last
    while pending:
        comp = pending.pop()
        if comp in ("", "."):
            continue
        if comp == "..":
            if not here:
                raise WalkRefused(BAD_LINK)
            here.pop()
            continue
        here.append(comp)
        kind = kinds.get("/".join(here))
        if kind is None:
            raise WalkRefused(BAD_LINK)
        if kind == "link":
            hops += 1
            if hops > MAX_LINK_HOPS:
                raise WalkRefused(BAD_LINK)
            inner = targets["/".join(here)]
            if not inner or inner.startswith("/"):
                raise WalkRefused(BAD_LINK)
            here.pop()
            pending.extend(reversed(inner.split("/")))
        elif kind == "file" and pending:
            raise WalkRefused(BAD_LINK)


# --- the record and the verification ---

Record = dict[str, tuple[int, int, int]]


def record(root_fd: int) -> Record:
    """Every entry's `(dev, ino, ctime_ns)` and the root's own under `""`, from one walk."""
    rec: Record = {"": statx_at(root_fd, "").triple}
    with contextlib.closing(walk(root_fd)) as entries:
        for path, st, _ in entries:
            rec[path] = st.triple
    return rec


def entries_of(root_fd: int) -> tuple[Entry, ...]:
    """The manifest entries of the tree at `root_fd`, from one walk, each file hashed by fd. A file that
    isn't the one the walk saw refuses (`ENTRY_CHANGED`)."""
    out: list[Entry] = []
    with contextlib.closing(walk(root_fd)) as entries:
        for path, st, target in entries:
            sha = _hash_file(root_fd, path, os.read, st) if st.kind == "file" else None
            out.append(Entry(path, cast(Kind, st.kind), st.mode, st.size, st.nlink, sha, target))
    return tuple(sorted(out, key=lambda e: _path_key(e.path)))


def check_fast(root_fd: int, m: Manifest, rec: Record) -> None:
    """The fast verification (r16 §7): the root is a directory with mode 0555 whose `(dev, ino, ctime_ns)`
    is the record's; the walk finds exactly the manifest's entries, each with its type, mode, size, link
    count and link target, and exactly the record's entries with their `(dev, ino, ctime_ns)`; and nothing
    crosses the root's mount. The root is checked again at the end. The whole tree is compared before
    anything is raised, so the most specific constant wins: an added entry, then a missing one, then a
    changed one (`Mismatch`). A refused walk is a `Mismatch` with the walk's constant."""
    first = statx_at(root_fd, "")
    expected = {e.path: e for e in m.entries}
    changed = first.kind != "dir" or first.mode != 0o555 or first.triple != rec.get("")
    added = False
    seen: set[str] = set()
    try:
        with contextlib.closing(walk(root_fd)) as entries:
            for path, st, target in entries:
                seen.add(path)
                e = expected.get(path)
                if e is None:
                    added = True
                elif ((e.kind, e.mode, e.size, e.nlink, e.target) != (st.kind, st.mode, st.size, st.nlink,
                                                                       target)
                      or rec.get(path) != st.triple):
                    changed = True
    except WalkRefused as exc:
        raise Mismatch(exc.constant) from None
    missing = bool(expected.keys() - seen)
    changed = changed or set(rec) != {"", *seen} or statx_at(root_fd, "") != first
    for flag, constant in ((added, ENTRY_ADDED), (missing, ENTRY_MISSING), (changed, ENTRY_CHANGED)):
        if flag:
            raise Mismatch(constant)


Reader = Callable[[int, int], bytes]


def rehash(root_fd: int, m: Manifest, *, read: Reader = os.read) -> None:
    """Every regular file's sha256 is its manifest entry's, read by fd through `read` (`CONTENT_CHANGED`).
    A file that is gone or no longer a regular file refuses (`ENTRY_MISSING`, `ENTRY_CHANGED`)."""
    for e in sorted(m.entries, key=lambda e: _path_key(e.path)):
        if e.kind == "file" and _hash_file(root_fd, e.path, read, None) != e.sha256:
            raise Mismatch(CONTENT_CHANGED)


def _hash_file(root_fd: int, path: str, read: Reader, st: Stat | None) -> str:
    """The sha256 of the regular file at `path` under `root_fd`, opened component by component without
    following a link. With `st`, the file must be the one it describes (`WalkRefused(ENTRY_CHANGED)`);
    without, a file that is gone or not regular is a `Mismatch`."""
    refuse: type[Refused] = WalkRefused if st is not None else Mismatch
    *dirs, name = path.split("/")
    fd = -1
    dirfd = root_fd
    try:
        for comp in dirs:
            try:
                nxt = _open(comp, DIR_FLAGS, dirfd)
            except OSError as exc:
                if exc.errno in GONE:
                    raise refuse(ENTRY_MISSING if exc.errno == errno.ENOENT else ENTRY_CHANGED) from None
                raise
            if dirfd != root_fd:
                os.close(dirfd)
            dirfd = nxt
        try:
            fd = _open(name, FILE_FLAGS, dirfd)
        except OSError as exc:
            if exc.errno in GONE:
                raise refuse(ENTRY_MISSING if exc.errno == errno.ENOENT else ENTRY_CHANGED) from None
            raise
        now = statx_at(fd, "")
        if now.kind != "file" or (st is not None and not _same(now, st)):
            raise refuse(ENTRY_CHANGED)
        h = hashlib.sha256()
        while chunk := read(fd, CHUNK):
            h.update(chunk)
        return h.hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)
        if dirfd != root_fd:
            os.close(dirfd)
