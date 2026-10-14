"""The managed install store's core (ADR r16 §7 Managed installs; plan 4c E1, E2, E6, E8, E20, E21): the
manifest and its canonical encoding and digest, `statx`, the fd walk, the record and the fast verification,
the checksums and git trees of sources, and `stage`. Standard library
only, and nothing from `heterodyne`: root installs a copy of this one file that the service user can't
write, runs it with `python3 -I` as the store helper, and `wsd` imports the same file for the fallback and
for verification, so there is one manifest implementation.

The walk never follows a link and never leaves the tree's mount. Every directory is opened relative to its
parent's fd (`O_NOFOLLOW`), every fact comes from `statx` on that fd, and every fd opened here is closed
in a `finally`. Refusals carry their constant as `.constant`, and their message is the constant alone:
nothing here names a path, a digest or a file's content in an error.
"""

import argparse
import base64
import binascii
import contextlib
import ctypes
import dataclasses
import errno
import fcntl
import gzip
import hashlib
import json
import os
import re
import secrets
import select
import socket
import stat
import struct
import tarfile
import time
import zlib
from collections.abc import Callable, Generator, Iterator, Sequence
from pathlib import Path
from typing import IO, Any, Literal, cast, get_args, get_origin

MAX_ENTRIES = 200_000           # entries in one install, the root uncounted; at the limit is accepted
MAX_DEPTH = 64                  # components in an entry's path; at the limit is accepted
MAX_LINK_HOPS = 40              # links followed resolving one link, the link itself included
MAX_ARTIFACT_BYTES = 1 << 30    # one artifact handed to `stage`; at the limit is accepted
MAX_FILE_BYTES = 1 << 30        # one file unpacked; at the limit is accepted
MAX_INSTALL_BYTES = 4 << 30     # all the files of one install; at the limit is accepted
MAX_TAR_EXTENSION = 64 << 10    # one pax or GNU long-name header's payload; at the limit is accepted
MAX_TAR_EXTENSIONS = 64 << 20   # one tar's extension payloads in all (tarfile keeps them); at the limit too
# the uncompressed bytes of one tar: its files, its extensions, and four blocks a member for the headers and
# padding; at the limit is accepted
MAX_TAR_BYTES = (MAX_INSTALL_BYTES + MAX_TAR_EXTENSIONS
                 + (MAX_ENTRIES + MAX_DEPTH + 1) * 4 * tarfile.BLOCKSIZE)
FORMAT = 1
CHUNK = 1 << 20

TOO_MANY_ENTRIES = "the install has more entries than the store accepts"
TOO_DEEP = "the install is nested deeper than the store accepts"
BAD_TYPE = "the install holds an entry that is not a directory, a regular file or a symbolic link"
BAD_NAME = "the install holds an entry whose name is empty, `.` or `..`, holds a NUL or is not UTF-8"
HARD_LINK = "the install holds a hard link"
BAD_LINK = "the install holds a link that is absolute, dangles, loops or leaves the install"
NO_MOUNT_ID = "statx returned no mount ID"
CROSSES_MOUNT = "the install crosses a mount"
ENTRY_CHANGED = "an install entry changed"
ENTRY_ADDED = "the install has an entry its manifest does not"
ENTRY_MISSING = "the install lacks an entry its manifest has"
CONTENT_CHANGED = "an install file's content changed"
MANIFEST_MALFORMED = "the manifest is not in its canonical form"
LOGIN_NAME = "the install holds an entry named as a login file"
CHECKSUM_MISMATCH = "an artifact's bytes are not the ones its checksum names"
GIT_MISMATCH = "a git archive or commit object is not the one its commit ID names"
GIT_SUBMODULE = "a git source holds a submodule"
BAD_MEMBER = ("an archive holds a member that is not a directory, a regular file or a symbolic link, or one "
              "that replaces or passes through another")
NO_VERSION = "the CLI's metadata gives no plain version, or its entry point is not an executable file"
BAD_PART = "a part, its source, its artifact or its layout is not one the store accepts"
TARGET_MISMATCH = "the CLI's metadata names another target than the recipe's"
TOO_MANY_BYTES = "an artifact, a file or the install is larger than the store accepts"

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
        entry_point, target = doc["entry_point"], doc["target"]
        if (not valid_path(entry_point) or not isinstance(target, str)
                or (target and not PLAIN.fullmatch(target))):
            raise Mismatch(MANIFEST_MALFORMED)
        m = Manifest(_text(doc["adapter"]), _text(doc["version"]), target, str(entry_point),
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
    root, a directory that changed while it was walked, and, once the walk is done, a tree that a second
    full walk doesn't find identical (`ENTRY_CHANGED`) or a link that doesn't resolve inside the tree.

    The second walk is what makes the result a snapshot. Each pass observes every entry once, at some
    instant in it; an entry both passes observe identically, `(dev, ino, ctime_ns)` included, was
    unchanged from its first observation to its second, since any change to it moves its ctime. Every
    such interval spans the instant between the passes, so the walk describes the tree as it was then,
    including the subtrees the first pass had already finished when something changed them."""
    first: list[tuple[str, Stat, str | None]] = []
    with contextlib.closing(_traverse(root_fd)) as entries:
        for item in entries:
            first.append(item)
            yield item
    with contextlib.closing(_traverse(root_fd)) as entries:
        if list(entries) != first:
            raise WalkRefused(ENTRY_CHANGED)
    _check_links({path: st.kind for path, st, _ in first},
                 {path: target for path, _, target in first if target is not None})


def _traverse(root_fd: int) -> Generator[tuple[str, Stat, str | None]]:
    """One pass of `walk`, without the second pass or the link check."""
    root = statx_at(root_fd, "")
    if root.kind != "dir":
        raise WalkRefused(BAD_TYPE)
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
            target = None
            if st.kind == "link":
                target = _readlink(dirfd, name)
            elif st.kind == "dir":
                child = _open_dir(dirfd, name, st)
                try:
                    names_below = _names(child)
                except BaseException:
                    os.close(child)
                    raise
                stack.append((child, path, st, names_below, 0))
            yield path, st, target
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


Reader = Callable[[int, int], bytes]


def record(root_fd: int) -> Record:
    """Every entry's `(dev, ino, ctime_ns)` and the root's own under `""`, from one walk."""
    rec: Record = {"": statx_at(root_fd, "").triple}
    with contextlib.closing(walk(root_fd)) as entries:
        for path, st, _ in entries:
            rec[path] = st.triple
    return rec


def entries_of(root_fd: int, *, read: Reader = os.read) -> tuple[Entry, ...]:
    """The manifest entries of the tree at `root_fd`, from one walk, each file hashed by fd through `read`.
    A file whose statx, before or after it was read, isn't exactly the walk's, or whose length isn't its
    size, refuses (`WalkRefused(ENTRY_CHANGED)`)."""
    out: list[Entry] = []
    with contextlib.closing(walk(root_fd)) as entries:
        for path, st, target in entries:
            sha = _hash_file(root_fd, path, read, st.__eq__, WalkRefused) if st.kind == "file" else None
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


def rehash(root_fd: int, m: Manifest, *, read: Reader = os.read) -> None:
    """Every regular file's sha256 is its manifest entry's, read by fd through `read` (`CONTENT_CHANGED`).
    A file that is gone refuses (`ENTRY_MISSING`); one that is no longer a regular file with the entry's
    mode, size and link count, before or after it was read, or whose length isn't its size, refuses
    (`ENTRY_CHANGED`)."""
    for e in sorted(m.entries, key=lambda e: _path_key(e.path)):
        if e.kind != "file":
            continue
        if _hash_file(root_fd, e.path, read, _fits(e), Mismatch) != e.sha256:
            raise Mismatch(CONTENT_CHANGED)


def _fits(e: Entry) -> Callable[[Stat], bool]:
    return lambda now: (now.mode, now.size, now.nlink) == (e.mode, e.size, e.nlink)


def _hash_file(root_fd: int, path: str, read: Reader, fits: Callable[[Stat], bool],
               refuse: type[Refused]) -> str:
    """The sha256 of the regular file at `path` under `root_fd`, opened component by component without
    following a link. The opened file's statx must be a regular file's that `fits`, and must be the same
    after the read, which must return exactly its size (`refuse(ENTRY_CHANGED)`); a file that is gone is
    `refuse(ENTRY_MISSING)`."""
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
        if now.kind != "file" or not fits(now):
            raise refuse(ENTRY_CHANGED)
        h = hashlib.sha256()
        length = 0
        while chunk := read(fd, CHUNK):
            h.update(chunk)
            length += len(chunk)
        if length != now.size or statx_at(fd, "") != now:   # it changed while it was read
            raise refuse(ENTRY_CHANGED)
        return h.hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)
        if dirfd != root_fd:
            os.close(dirfd)


# --- checksums (plan 4c E5) ---

SHA256_PREFIX = "sha256:"
SRI_PREFIX = "sha512-"


def _is_hex(s: str, n: int) -> bool:
    return len(s) == n and set(s) <= HEX


def _chunks(fd: int) -> Iterator[bytes]:
    """`fd`'s bytes from offset 0 by `pread`, whatever its file offset."""
    offset = 0
    while chunk := os.pread(fd, CHUNK, offset):
        offset += len(chunk)
        yield chunk


def _read_bounded(fd: int, limit: int) -> bytes | None:
    """`fd`'s bytes from offset 0, or None if there are more than `limit`."""
    out = bytearray()
    for chunk in _chunks(fd):
        out += chunk
        if len(out) > limit:
            return None
    return bytes(out)


def verify_checksum(fd: int, checksum: str) -> None:
    """`fd`'s bytes are the ones `checksum` names: `sha256:<64 lowercase hex>`, or an SRI `sha512-<base64>`.
    Any other form, or other bytes, refuses (`CHECKSUM_MISMATCH`)."""
    if checksum.startswith(SHA256_PREFIX) and _is_hex(checksum[len(SHA256_PREFIX):], 64):
        algo, want = "sha256", bytes.fromhex(checksum[len(SHA256_PREFIX):])
    elif checksum.startswith(SRI_PREFIX):
        try:
            algo, want = "sha512", base64.b64decode(checksum[len(SRI_PREFIX):], validate=True)
        except binascii.Error:
            raise Refused(CHECKSUM_MISMATCH) from None
        if len(want) != 64:
            raise Refused(CHECKSUM_MISMATCH)
    else:
        raise Refused(CHECKSUM_MISMATCH)
    h = hashlib.new(algo)
    for chunk in _chunks(fd):
        h.update(chunk)
    if h.digest() != want:
        raise Refused(CHECKSUM_MISMATCH)


# --- git sources, checked without git (plan 4c E6) ---

GitAlgo = Literal["sha1", "sha256"]
GitMember = tuple[str, bytes, bytes]           # path, mode, raw object ID
COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MAX_COMMIT_OBJECT = 1 << 20
GIT_FILE, GIT_EXECUTABLE, GIT_LINK, GIT_TREE = b"100644", b"100755", b"120000", b"40000"
# the modes `git archive` writes for a blob, under `tar.umask` 022 (the fetcher's) or 002 (git's default)
GIT_TAR_MODES = {0o644: GIT_FILE, 0o664: GIT_FILE, 0o755: GIT_EXECUTABLE, 0o775: GIT_EXECUTABLE}
_TAR_ERRORS = (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile)


def _utf8(s: str) -> bool:
    """False for a name that came from a tar header undecoded: it holds surrogates."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _plain_path(path: str) -> bool:
    return valid_path(path) and _utf8(path)


class _Capped:
    """A reader that refuses (`TOO_MANY_BYTES`) to go past `MAX_TAR_BYTES` of `inner`, before it reads or
    seeks there: a gzip is decompressed only as far as the cap."""

    def __init__(self, inner: IO[bytes] | gzip.GzipFile) -> None:
        self.inner = inner

    def tell(self) -> int:
        return self.inner.tell()

    def read(self, n: int = -1) -> bytes:
        pos = self.inner.tell()
        if pos >= MAX_TAR_BYTES and n != 0:
            if self.inner.read(1):
                raise Refused(TOO_MANY_BYTES)
            return b""
        room = MAX_TAR_BYTES - pos
        chunk = self.inner.read(room + 1 if n < 0 or n > room else n)
        if len(chunk) > room:
            raise Refused(TOO_MANY_BYTES)
        return chunk

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = 0 if whence == os.SEEK_SET else self.inner.tell() if whence == os.SEEK_CUR else None
        target = -1 if base is None else base + offset
        if target < 0 or target > MAX_TAR_BYTES:
            raise Refused(TOO_MANY_BYTES)
        return self.inner.seek(target)


EXTENSIONS = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE, tarfile.GNUTYPE_LONGNAME,
              tarfile.GNUTYPE_LONGLINK)


class _BoundedTar(tarfile.TarFile):
    extension_bytes = 0


class _BoundedInfo(tarfile.TarInfo):
    """Refuses an extension header's payload over `MAX_TAR_EXTENSION`, or one that brings the tar's past
    `MAX_TAR_EXTENSIONS` (`TOO_MANY_BYTES`), before tarfile reads it; and a sparse member (`BAD_MEMBER`)
    before tarfile reads its map."""

    # `_proc_member` is the hook `TarInfo.fromtarfile` calls once it has the header block and before it reads
    # any payload (CPython 3.12's tarfile); the bounds tests fail if it stops being called
    def _proc_member(self, tf: tarfile.TarFile) -> tarfile.TarInfo:
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise Refused(BAD_MEMBER)
        if self.type in EXTENSIONS:
            total = getattr(tf, "extension_bytes", 0) + self.size
            if self.size > MAX_TAR_EXTENSION or total > MAX_TAR_EXTENSIONS:
                raise Refused(TOO_MANY_BYTES)
            if isinstance(tf, _BoundedTar):
                tf.extension_bytes = total
        base: Any = super()
        return base._proc_member(tf)

    def _proc_gnusparse_00(self, *args: object) -> None:
        raise Refused(BAD_MEMBER)

    _proc_gnusparse_01 = _proc_gnusparse_10 = _proc_gnusparse_00


@contextlib.contextmanager
def _tar(fd: int, *, gz: bool) -> Generator[tarfile.TarFile]:
    """A tar reader on `fd` from offset 0, names decoded as UTF-8 with surrogates, and its uncompressed bytes
    and extension headers bounded; `fd` stays open."""
    os.lseek(fd, 0, os.SEEK_SET)
    with contextlib.ExitStack() as stack:
        raw: IO[bytes] | gzip.GzipFile = stack.enter_context(open(fd, "rb", closefd=False))
        if gz:
            raw = stack.enter_context(gzip.GzipFile(fileobj=raw, mode="rb"))
        t = _BoundedTar(fileobj=cast(IO[bytes], _Capped(raw)), mode="r", tarinfo=_BoundedInfo,
                        encoding="utf-8", errors="surrogateescape")
        with t:
            yield t


def _git_hash(algo: GitAlgo, kind: bytes, body: bytes) -> bytes:
    return hashlib.new(algo, b"%s %d\0" % (kind, len(body)) + body).digest()


def git_members(archive_fd: int, algo: GitAlgo) -> list[GitMember]:
    """The blobs of the `git archive --format=tar` at `archive_fd`, as `(path, mode, object ID)`: a file is
    100755 if its member's mode is 0755 or 0775, 100644 if it is 0644 or 0664, and any other mode is
    `GIT_MISMATCH`; a link is 120000 with its target as its content. An empty directory is a submodule,
    which `git archive` writes as one (`GIT_SUBMODULE`); any other member type, a duplicate or bad name, or a
    malformed archive is `GIT_MISMATCH`."""
    out: list[GitMember] = []
    seen: set[str] = set()
    dirs: set[str] = set()
    try:
        with _tar(archive_fd, gz=False) as t:
            for members, info in enumerate(t, 1):
                if members > MAX_ENTRIES + MAX_DEPTH:
                    raise Refused(TOO_MANY_ENTRIES)
                name = info.name
                if not _plain_path(name) or name in seen:
                    raise Refused(GIT_MISMATCH)
                if name.count("/") >= MAX_DEPTH:
                    raise Refused(TOO_DEEP)
                seen.add(name)
                if info.type == tarfile.DIRTYPE:
                    dirs.add(name)
                elif info.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    f = t.extractfile(info)
                    if f is None:
                        raise Refused(GIT_MISMATCH)
                    h = hashlib.new(algo, b"blob %d\0" % info.size)
                    length = 0
                    while chunk := f.read(CHUNK):
                        h.update(chunk)
                        length += len(chunk)
                    if length != info.size:
                        raise Refused(GIT_MISMATCH)
                    mode = GIT_TAR_MODES.get(info.mode)
                    if mode is None:            # a mode git can't have written: the tree can't vouch for it
                        raise Refused(GIT_MISMATCH)
                    out.append((name, mode, h.digest()))
                elif info.type == tarfile.SYMTYPE:
                    target = info.linkname.encode("utf-8", "surrogateescape")
                    out.append((name, GIT_LINK, _git_hash(algo, b"blob", target)))
                else:
                    raise Refused(GIT_MISMATCH)
    except _TAR_ERRORS:
        raise Refused(GIT_MISMATCH) from None
    parents = {p.rsplit("/", 1)[0] for p in seen if "/" in p}
    ancestors = {"/".join(p.split("/")[:i]) for p in parents for i in range(1, p.count("/") + 2)}
    if dirs - ancestors:
        raise Refused(GIT_SUBMODULE)
    return out


@dataclasses.dataclass
class _Tree:
    children: dict[str, "_Tree | tuple[bytes, bytes]"] = dataclasses.field(
        default_factory=dict[str, "_Tree | tuple[bytes, bytes]"])


def git_tree_id(members: Sequence[GitMember], *, algo: GitAlgo = "sha1") -> str:
    """The ID of the git tree that holds exactly `members`, with the directories their paths imply, in git's
    own order and encoding. A path that is both a blob and a directory, or twice a blob, is `GIT_MISMATCH`."""
    root = _Tree()
    for path, mode, oid in members:
        *dirs, name = path.split("/")
        node = root
        for d in dirs:
            child = node.children.setdefault(d, _Tree())
            if not isinstance(child, _Tree):
                raise Refused(GIT_MISMATCH)
            node = child
        if name in node.children:
            raise Refused(GIT_MISMATCH)
        node.children[name] = (mode, oid)
    return _tree_id(root, algo).hex()


def _tree_id(tree: _Tree, algo: GitAlgo) -> bytes:
    rows: list[tuple[bytes, bytes]] = []
    for name, child in tree.children.items():
        raw = name.encode("utf-8")
        if isinstance(child, _Tree):           # git sorts a tree as if its name ended in `/`
            rows.append((raw + b"/", GIT_TREE + b" " + raw + b"\0" + _tree_id(child, algo)))
        else:
            rows.append((raw, child[0] + b" " + raw + b"\0" + child[1]))
    return _git_hash(algo, b"tree", b"".join(row for _, row in sorted(rows)))


def check_git(archive_fd: int, commit_fd: int, commit_id: str) -> None:
    """The commit object at `commit_fd` hashes to `commit_id` (lowercase hex: 40 digits for sha1, 64 for
    sha256), and the tree it names is the one the archive at `archive_fd` holds. Anything else is
    `GIT_MISMATCH` (or `GIT_SUBMODULE`)."""
    if not COMMIT_ID.fullmatch(commit_id):
        raise Refused(GIT_MISMATCH)
    n = len(commit_id)
    algo: GitAlgo = "sha1" if n == 40 else "sha256"
    body = _read_bounded(commit_fd, MAX_COMMIT_OBJECT)
    if body is None or _git_hash(algo, b"commit", body).hex() != commit_id:
        raise Refused(GIT_MISMATCH)
    head = body[:n + 6]
    tree = head[5:n + 5].decode("ascii", "replace")
    if head[:5] != b"tree " or head[n + 5:] != b"\n" or not _is_hex(tree, n):
        raise Refused(GIT_MISMATCH)
    if git_tree_id(git_members(archive_fd, algo), algo=algo) != tree:
        raise Refused(GIT_MISMATCH)


# --- fs-verity (plan 4c E20) ---

FS_IOC_ENABLE_VERITY = 0x40806685
# struct fsverity_enable_arg: version 1, sha256, 4096-byte blocks, no salt and no signature
VERITY_ARG = struct.pack("=IIIIQIIQ88x", 1, 1, 4096, 0, 0, 0, 0, 0)


def _ioctl(fd: int, request: int, arg: bytes) -> bytes:
    return fcntl.ioctl(fd, request, arg)


def enable_verity(fd: int) -> bool:
    """fs-verity on the file at `fd`, which must be open read-only and still writable by its owner. False
    where the filesystem has no verity (`EOPNOTSUPP`, `ENOTTY`); any other error raises."""
    try:
        _ioctl(fd, FS_IOC_ENABLE_VERITY, VERITY_ARG)
    except OSError as exc:
        if exc.errno in (errno.EOPNOTSUPP, errno.ENOTTY):
            return False
        raise OSError(exc.errno, "verity") from None
    return True


# --- stage (plan 4c E2, E4, E7, E20, E21) ---

PartFormat = Literal["tar.gz", "tgz", "tar", "file"]
PART_FORMATS: tuple[PartFormat, ...] = ("tar.gz", "tgz", "tar", "file")
STAGING, STAGED, LOCK = ".staging", ".staged", ".lock"
MAX_METADATA = 1 << 20
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")      # a version, a target or an adapter
WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclasses.dataclass(frozen=True)
class Layout:
    """Where a CLI's version, target and entry point are read, all under `cli/` (plan 4c E7). Exactly one of
    `entry_field` (a field of the metadata file) and `entry_point` (a fixed path) is set; with no
    `target_field` the target is ""."""
    version_file: str
    version_field: str
    target_field: str | None
    entry_field: str | None
    entry_point: str | None


@dataclasses.dataclass(frozen=True)
class Part:
    part: str                   # `cli`, `defaults` or `content/<kind>/<name>`
    format: PartFormat
    strip: str                  # "" or a prefix every member has, ending in `/`; a `file` part's file name
    checksum: str               # `sha256:<hex>`, an SRI `sha512-<base64>`, or a git part's `git:<commit>`
    origin: str
    signature_available: bool
    source: Source
    git_commit: str | None      # a git part's commit; it takes two fds, the archive and the commit object


@dataclasses.dataclass(frozen=True)
class Staged:
    id: str
    digest: str
    entries: int
    size: int
    verity: bool                # every file has fs-verity on


def _bad_component(c: str) -> bool:
    return not _plain_path(c) or "/" in c


def _layout_ok(layout: Layout) -> bool:
    return (valid_path(layout.version_file) and bool(layout.version_field)
            and (layout.entry_field is None) != (layout.entry_point is None)
            and (layout.entry_point is None or valid_path(layout.entry_point)))


def _check_parts(adapter: str, layout: Layout, parts: Sequence[Part], fds: Sequence[int],
                 login_names: frozenset[str]) -> list[tuple[Part, list[int]]]:
    """Each part with its fds, once the parts are ones the store accepts (`BAD_PART`, `LOGIN_NAME`)."""
    names = [p.part for p in parts]
    if (not PLAIN.fullmatch(adapter) or not _layout_ok(layout) or "cli" not in names
            or len(set(names)) != len(names)):
        raise Refused(BAD_PART)
    out: list[tuple[Part, list[int]]] = []
    for p in parts:
        comps = p.part.split("/")
        if p.part not in ("cli", "defaults") and (len(comps) != 3 or comps[0] != "content"
                                                  or any(_bad_component(c) for c in comps)):
            raise Refused(BAD_PART)
        if any(c in login_names for c in comps):
            raise Refused(LOGIN_NAME)
        s = p.source
        git = s.kind == "git"
        if ((s.part, s.checksum, s.origin, s.signature_available, s.signature_verified)
                != (p.part, p.checksum, p.origin, p.signature_available, False)
                or s.kind not in SOURCE_KINDS or p.format not in PART_FORMATS
                or git != (p.git_commit is not None)
                or (git and (p.format != "tar" or p.strip or p.checksum != f"git:{p.git_commit}"))):
            raise Refused(BAD_PART)
        if p.format == "file":
            if _bad_component(p.strip):
                raise Refused(BAD_PART)
            if p.strip in login_names:
                raise Refused(LOGIN_NAME)
        elif p.strip and not (p.strip.endswith("/") and _plain_path(p.strip[:-1])):
            raise Refused(BAD_PART)
        taken = sum(len(f) for _, f in out)
        mine = list(fds[taken:taken + (2 if git else 1)])
        if len(mine) != (2 if git else 1):
            raise Refused(BAD_PART)
        out.append((p, mine))
    if sum(len(f) for _, f in out) != len(fds):
        raise Refused(BAD_PART)
    return out


def _open_path(root_fd: int, path: str, flags: int) -> int:
    """`path` under `root_fd`, opened component by component without following a link."""
    *dirs, name = path.split("/")
    dirfd = root_fd
    try:
        for comp in dirs:
            nxt = _open(comp, DIR_FLAGS, dirfd)
            if dirfd != root_fd:
                os.close(dirfd)
            dirfd = nxt
        return _open(name, flags, dirfd)
    finally:
        if dirfd != root_fd:
            os.close(dirfd)


@contextlib.contextmanager
def _parent(root_fd: int, path: str) -> Generator[tuple[int, str]]:
    """The fd of `path`'s parent under `root_fd`, and `path`'s last component."""
    head, _, name = path.rpartition("/")
    if not head:
        yield root_fd, name
        return
    fd = _open_path(root_fd, head, DIR_FLAGS)
    try:
        yield fd, name
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class _Unpacker:
    """Writes parts into the staging root by fd: every name checked component by component before anything
    is written, every directory made 0700, every file made by `O_EXCL | O_NOFOLLOW` at 0600 and a new inode,
    and nothing replaced or passed through. `tarfile.extract` is never used."""

    def __init__(self, root_fd: int, login_names: frozenset[str]) -> None:
        self.root_fd = root_fd
        self.login_names = login_names
        self.kinds: dict[str, Kind] = {}
        self.modes: dict[str, int] = {}             # each file's final mode (E20)
        self.size = 0                               # bytes written to files so far

    def _claim(self, path: str, kind: Kind) -> bool:
        """`path` recorded as `kind`; False if it is a directory already. Anything else there is
        `BAD_MEMBER`."""
        held = self.kinds.get(path)
        if held is not None:
            if held == kind == "dir":
                return False
            raise Refused(BAD_MEMBER)
        if len(self.kinds) >= MAX_ENTRIES:
            raise Refused(TOO_MANY_ENTRIES)
        self.kinds[path] = kind
        return True

    def _mkdir(self, path: str) -> None:
        if self._claim(path, "dir"):
            with _parent(self.root_fd, path) as (fd, name):
                os.mkdir(name, 0o700, dir_fd=fd)

    def _prepare(self, path: str) -> None:
        """The checks on `path`'s components, then its parents, made where they are missing."""
        comps = path.split("/")
        if any(c in self.login_names for c in comps):
            raise Refused(LOGIN_NAME)
        if len(comps) > MAX_DEPTH:
            raise Refused(TOO_DEEP)
        for i in range(1, len(comps)):
            self._mkdir("/".join(comps[:i]))

    def directory(self, path: str) -> None:
        self._prepare(path)
        self._mkdir(path)

    def file(self, path: str, src: IO[bytes], size: int, executable: bool) -> None:
        """A file of `size` bytes from `src`; the byte bounds are checked before the first write and against
        every chunk (`TOO_MANY_BYTES`), and a length that isn't `size` is `BAD_PART`."""
        self._prepare(path)
        if size > MAX_FILE_BYTES or self.size + size > MAX_INSTALL_BYTES:
            raise Refused(TOO_MANY_BYTES)
        self._claim(path, "file")
        with _parent(self.root_fd, path) as (dirfd, name):
            fd = os.open(name, WRITE_FLAGS, 0o600, dir_fd=dirfd)
        try:
            written = 0
            while chunk := src.read(CHUNK):
                written += len(chunk)
                if written > size:
                    raise Refused(BAD_PART)
                _write_all(fd, chunk)
            if written != size:
                raise Refused(BAD_PART)
        finally:
            os.close(fd)
        self.size += written
        self.modes[path] = 0o555 if executable else 0o444

    def link(self, path: str, target: str) -> None:
        if not target or "\0" in target or not _utf8(target):
            raise Refused(BAD_LINK)             # the walk refuses one that is absolute or leaves the install
        self._prepare(path)
        self._claim(path, "link")
        with _parent(self.root_fd, path) as (fd, name):
            os.symlink(target, name, dir_fd=fd)

    def part(self, p: Part, fd: int) -> None:
        self.directory(p.part)
        if p.format == "file":
            os.lseek(fd, 0, os.SEEK_SET)
            with open(fd, "rb", closefd=False) as f:
                self.file(f"{p.part}/{p.strip}", f, os.fstat(fd).st_size, False)
            return
        try:
            with _tar(fd, gz=p.format != "tar") as t:
                for members, info in enumerate(t, 1):
                    if members > MAX_ENTRIES + MAX_DEPTH:      # repeated directories create no entry
                        raise Refused(TOO_MANY_ENTRIES)
                    self._member(p, t, info)
        except _TAR_ERRORS:
            raise Refused(BAD_PART) from None

    def _member(self, p: Part, t: tarfile.TarFile, info: tarfile.TarInfo) -> None:
        name = info.name
        if not _plain_path(name):
            raise Refused(BAD_NAME)
        if p.strip:
            if info.type == tarfile.DIRTYPE and p.strip.startswith(name + "/"):
                return                              # the strip prefix's own directories
            if not name.startswith(p.strip):
                raise Refused(BAD_PART)
            name = name[len(p.strip):]
        path = f"{p.part}/{name}"
        if info.type == tarfile.DIRTYPE:
            self.directory(path)
        elif info.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
            src = t.extractfile(info)
            if src is None:
                raise Refused(BAD_MEMBER)
            # a git part's member is executable as git classified it when `check_git` authenticated it
            executable = info.mode & (0o100 if p.git_commit is not None else 0o111)
            self.file(path, src, info.size, bool(executable))
        elif info.type == tarfile.SYMTYPE:
            self.link(path, info.linkname)
        else:
            raise Refused(BAD_MEMBER)


def _copy(fd: int, dirfd: int, name: str) -> int:
    """A staging-private copy of the regular file at `fd`, from offset 0, open read-write at `name`. More
    than `MAX_ARTIFACT_BYTES`, at the start or while it is copied (a file that grows), is `TOO_MANY_BYTES`."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise Refused(BAD_PART)
    if st.st_size > MAX_ARTIFACT_BYTES:
        raise Refused(TOO_MANY_BYTES)
    out = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                  dir_fd=dirfd)
    try:
        offset = 0
        try:
            while sent := os.sendfile(out, fd, offset, min(CHUNK, MAX_ARTIFACT_BYTES + 1 - offset)):
                offset += sent
                if offset > MAX_ARTIFACT_BYTES:
                    raise Refused(TOO_MANY_BYTES)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOSYS) or offset:
                raise
            while chunk := os.pread(fd, min(CHUNK, MAX_ARTIFACT_BYTES + 1 - offset), offset):
                offset += len(chunk)
                if offset > MAX_ARTIFACT_BYTES:
                    raise Refused(TOO_MANY_BYTES) from None
                _write_all(out, chunk)
        return out
    except BaseException:
        os.close(out)
        raise


def _unpack(staging_fd: int, src_name: str, planned: list[tuple[Part, list[int]]],
            unpacker: _Unpacker) -> None:
    """Each part copied into `src_name` first, its checksum (or its git tree) checked on that copy, then
    unpacked from it: the bytes checked are the bytes unpacked."""
    src_fd = _open(src_name, DIR_FLAGS, staging_fd)
    try:
        for n, (p, fds) in enumerate(planned):
            copies: list[int] = []
            try:
                for k, fd in enumerate(fds):
                    copies.append(_copy(fd, src_fd, f"{n}.{k}"))
                if p.git_commit is None:
                    verify_checksum(copies[0], p.checksum)
                else:
                    check_git(copies[0], copies[1], p.git_commit)
                unpacker.part(p, copies[0])
            finally:
                for c in copies:
                    os.close(c)
    finally:
        os.close(src_fd)


def _version(root_fd: int, layout: Layout, unpacker: _Unpacker, expected_target: str) -> tuple[str, str]:
    """The version, target and entry point from the CLI's metadata (E7): a regular file, not a link, of
    bounded size, holding a JSON object whose version (and target) is a plain name, and an entry point that
    is an executable regular file under `cli/`. Anything else is `NO_VERSION`; a target the metadata names
    that isn't `expected_target` is `TARGET_MISMATCH`."""
    meta = f"cli/{layout.version_file}"
    if unpacker.kinds.get(meta) != "file":
        raise Refused(NO_VERSION)
    fd = _open_path(root_fd, meta, FILE_FLAGS)
    try:
        data = _read_bounded(fd, MAX_METADATA)
    finally:
        os.close(fd)
    try:
        doc: object = json.loads(data) if data is not None else None
    except (ValueError, RecursionError):
        raise Refused(NO_VERSION) from None
    if not isinstance(doc, dict):
        raise Refused(NO_VERSION)
    fields = cast(dict[str, object], doc)
    version = fields.get(layout.version_field)
    target = fields.get(layout.target_field, "") if layout.target_field is not None else ""
    entry = layout.entry_point if layout.entry_field is None else fields.get(layout.entry_field)
    if (not isinstance(version, str) or not PLAIN.fullmatch(version) or not isinstance(target, str)
            or (layout.target_field is not None and not PLAIN.fullmatch(target))
            or not isinstance(entry, str) or not valid_path(entry)):
        raise Refused(NO_VERSION)
    entry = f"cli/{entry}"
    if unpacker.kinds.get(entry) != "file" or unpacker.modes.get(entry) != 0o555:
        raise Refused(NO_VERSION)
    if layout.target_field is not None and target != expected_target:
        raise Refused(TARGET_MISMATCH)
    return version, entry


def _seal(root_fd: int, unpacker: _Unpacker, verity: bool) -> bool:
    """The final modes and the tree made durable (E20, E21): every file gets fs-verity (if asked, while
    the filesystem has it), its mode and an `fsync`; then every directory, deepest first, 0555 and an
    `fsync`; then the root. True if every file has verity."""
    on = verity
    for path, kind in unpacker.kinds.items():
        if kind != "file":
            continue
        fd = _open_path(root_fd, path, FILE_FLAGS)
        try:
            if on:
                on = enable_verity(fd)
            os.fchmod(fd, unpacker.modes[path])
            os.fsync(fd)
        finally:
            os.close(fd)
    dirs = sorted((p for p, k in unpacker.kinds.items() if k == "dir"), key=lambda p: -p.count("/"))
    for path in dirs:
        fd = _open_path(root_fd, path, DIR_FLAGS)
        try:
            os.fchmod(fd, 0o555)
            os.fsync(fd)
        finally:
            os.close(fd)
    os.fchmod(root_fd, 0o555)
    os.fsync(root_fd)
    return on


LOCK_SECONDS = 30.0         # the longest a request waits for the store lock
LOCK_POLL = 0.01
STORE_BUSY = "the store lock was not free before the deadline"


@contextlib.contextmanager
def _lock(store_fd: int, *, shared: bool = False, deadline: float = LOCK_SECONDS) -> Generator[None]:
    """The store's lock on `<store>/.lock` (E21) for the block, exclusive unless `shared`. Waiting longer
    than `deadline` seconds refuses (`STORE_BUSY`). A flock dies with its process."""
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=store_fd)
    try:
        end = time.monotonic() + deadline
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise Refused(STORE_BUSY) from None
                time.sleep(LOCK_POLL)
        yield
    finally:
        os.close(fd)


def _open_store(store: Path) -> int:
    try:
        return os.open(store, DIR_FLAGS)
    except OSError as exc:
        raise OSError(exc.errno, "open") from None


@contextlib.contextmanager
def store_lock(store: Path, *, shared: bool = False, deadline: float = LOCK_SECONDS) -> Generator[int]:
    """The store lock (E21) on `store` for the block, which gets the store's fd."""
    store_fd = _open_store(store)
    try:
        with _lock(store_fd, shared=shared, deadline=deadline):
            yield store_fd
    finally:
        os.close(store_fd)


def _make_dir(parent_fd: int, name: str, mode: int) -> int:
    """`name` under `parent_fd`, opened; if it is new, made with `mode` and its parent `fsync`ed."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        new = True
    except FileExistsError:
        new = False
    fd = _open(name, DIR_FLAGS, parent_fd)
    try:
        if new:
            os.fchmod(fd, mode)
            os.fsync(parent_fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _remove(dirfd: int, name: str) -> None:
    """`name` under `dirfd` and everything below it, by fd, never following a link; each directory is made
    writable by its owner first. Gone already is fine."""
    try:
        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=dirfd)
        return
    fd = _open(name, DIR_FLAGS, dirfd)
    try:
        os.fchmod(fd, 0o700)
        with os.scandir(fd) as it:
            children = [e.name for e in it]
        for child in children:
            _remove(fd, child)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dirfd)


def _commit(store_fd: int, staging_id: str, data: bytes, deadline: float) -> None:
    """Under the store lock (E21): `.staged/<id>.json` written by `O_EXCL`, made 0444 and `fsync`ed, then
    `.staged` `fsync`ed."""
    with _lock(store_fd, deadline=deadline):
        staged_fd = _make_dir(store_fd, STAGED, 0o755)
        try:
            fd = os.open(f"{staging_id}.json", WRITE_FLAGS, 0o444, dir_fd=staged_fd)
            try:
                _write_all(fd, data)
                os.fchmod(fd, 0o444)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(staged_fd)
        finally:
            os.close(staged_fd)


def _discard(store_fd: int, staging_fd: int, staging_id: str) -> None:
    for name in (staging_id, f"{staging_id}.src", f"{staging_id}.lock"):
        _remove(staging_fd, name)
    try:
        staged_fd = _open(STAGED, DIR_FLAGS, store_fd)
    except FileNotFoundError:
        return
    try:
        _remove(staged_fd, f"{staging_id}.json")
    finally:
        os.close(staged_fd)


def stage(store: Path, adapter: str, layout: Layout, parts: Sequence[Part], fds: Sequence[int],
          login_names: frozenset[str], *, verity: bool, target: str,
          deadline: float = LOCK_SECONDS) -> Staged:
    """Unpack `parts` from `fds` (one per part, two for a git part: the archive and the commit object) into
    `<store>/.staging/<id>/<part>/`, and write the staged manifest to `<store>/.staged/<id>.json`.

    Each artifact is copied first (at most `MAX_ARTIFACT_BYTES`; the files unpacked at most `MAX_FILE_BYTES`
    each and `MAX_INSTALL_BYTES` in all), and its checksum (or git tree) is checked on the copy it is unpacked
    from. Before the tree is walked and hashed it has its final modes (E20: directories and the root 0555, a
    file 0555 if its member had any execute bit, else 0444) and is durable (E21), so the staged manifest
    holds the modes the install will have. `target` is the recipe's: the manifest records it, and a target
    the CLI's metadata names must be it. The unpack and the hash hold only `.staging/<id>.lock`; the
    manifest is written under the store lock, each section waiting at most `deadline` seconds for it
    (`STORE_BUSY`). Nothing in an artifact is run. Any exception removes
    `.staging/<id>` and `.staged/<id>.json` before it propagates."""
    if not PLAIN.fullmatch(target):
        raise Refused(BAD_PART)
    planned = _check_parts(adapter, layout, parts, fds, login_names)
    staging_id = secrets.token_hex(16)
    store_fd = _open_store(store)
    staging_fd = root_fd = lock_fd = -1
    try:
        try:
            with _lock(store_fd, deadline=deadline):
                staging_fd = _make_dir(store_fd, STAGING, 0o700)
                os.mkdir(staging_id, 0o700, dir_fd=staging_fd)
                os.mkdir(f"{staging_id}.src", 0o700, dir_fd=staging_fd)
                lock_fd = os.open(f"{staging_id}.lock", WRITE_FLAGS, 0o600, dir_fd=staging_fd)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                os.fsync(staging_fd)
            root_fd = _open(staging_id, DIR_FLAGS, staging_fd)
            unpacker = _Unpacker(root_fd, login_names)
            _unpack(staging_fd, f"{staging_id}.src", planned, unpacker)
            _remove(staging_fd, f"{staging_id}.src")
            version, entry_point = _version(root_fd, layout, unpacker, target)
            on = _seal(root_fd, unpacker, verity)
            os.fsync(staging_fd)
            entries = entries_of(root_fd)
            sources = tuple(sorted((p.source for p in parts), key=lambda s: _path_key(s.part)))
            m = Manifest(adapter, version, target, entry_point, sources, entries)
            data = canonical(m)
            parse(data)                         # the staged manifest is one the store will read back
            _commit(store_fd, staging_id, data, deadline)
            os.unlink(f"{staging_id}.lock", dir_fd=staging_fd)
        except BaseException:
            if staging_fd >= 0:
                with contextlib.suppress(OSError):
                    _discard(store_fd, staging_fd, staging_id)
            raise
    finally:
        for fd in (root_fd, lock_fd, staging_fd, store_fd):
            if fd >= 0:
                os.close(fd)
    size = sum(e.size for e in entries if e.kind == "file")
    return Staged(staging_id, digest(m), len(entries), size, on)


# --- publish, recovery, verify, remove and the sweep (plan 4c E18, E20, E21) ---

DIGEST_MISMATCH = "the manifest's digest is not the one asked for"
CHANGED_DURING_PUBLISH = "the tree changed while it was published"
CHANGED_DURING_VERIFY = "the install changed while it was verified"
NAME_CONFLICT = "the install name is taken, or held by what an unfinished removal left"
NO_SUCH_STAGING = "the store has no staging, receipt or failure for that staging ID"
NO_SUCH_INSTALL = "the store has no install of that name"
INSTALL_BROKEN = "the install is not in its published state"
RECOVERY_FAILED = "an interrupted publish could not be recovered"
RECORD_MALFORMED = "a record, an intent, a receipt or a failure is not in the store's form"

PUBLISHING, PUBLISHED, FAILED, META = ".publishing", ".published", ".failed", ".meta"
STORE_DIR_MODE = 0o755
STAGING_ID = re.compile(r"[0-9a-f]{32}")
INSTALL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*-[0-9a-f]{12}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MAX_STORE_FILE = 1 << 30        # a manifest or a record of an install at the store's entry bound
RENAME_NOREPLACE = 1
Hook = Callable[[str], None]
StatAt = Callable[[int, str], Stat]
State = Literal["published", "publishing", "broken"]

_renameat2 = getattr(_libc, "renameat2", None)
if _renameat2 is not None:
    _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameat2.restype = ctypes.c_int


def _nothing(point: str) -> None:
    """No hook: tests pass one that acts at a named point of publish, recovery, verify or remove."""


def _rename_noreplace(src_fd: int, src: str, dst_fd: int, dst: str) -> None:
    if _renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2")
    if _renameat2(src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), RENAME_NOREPLACE) != 0:
        raise OSError(ctypes.get_errno(), "renameat2")


def install_name(version: str, manifest_digest: str) -> str:
    """`<version>-<the digest's first 12 hex>` (plan 4c E2)."""
    if not PLAIN.fullmatch(version) or not DIGEST.fullmatch(manifest_digest):
        raise Mismatch(MANIFEST_MALFORMED)
    return f"{version}-{manifest_digest.removeprefix(SHA256_PREFIX)[:12]}"


@dataclasses.dataclass(frozen=True)
class Intent:
    """A publish intent (`.publishing/<id>.json`), and, written at P5, its receipt
    (`.published/<id>.json`)."""
    id: str
    adapter: str
    install: str
    digest: str
    root: tuple[int, int]       # the staging root's (dev, ino), which the rename keeps


def _dump(doc: dict[str, object]) -> bytes:
    return json.dumps({"format": FORMAT, **doc}, sort_keys=True, separators=(",", ":")).encode()


def _load(data: bytes, fields: frozenset[str]) -> dict[str, object]:
    try:
        doc: object = json.loads(data)
    except (ValueError, RecursionError):
        raise Refused(RECORD_MALFORMED) from None
    if not isinstance(doc, dict):
        raise Refused(RECORD_MALFORMED)
    d = cast(dict[str, object], doc)
    if frozenset(d) != fields | {"format"} or d["format"] != FORMAT or type(d["format"]) is not int:
        raise Refused(RECORD_MALFORMED)
    return d


def _ints(v: object, n: int) -> tuple[int, ...]:
    if not isinstance(v, list):
        raise Refused(RECORD_MALFORMED)
    items = cast(list[object], v)
    if len(items) != n or any(type(i) is not int or i < 0 for i in items):
        raise Refused(RECORD_MALFORMED)
    return tuple(cast(list[int], items))


def _record_bytes(rec: Record) -> bytes:
    return _dump({"record": {k: list(v) for k, v in rec.items()}})


def _record_of(data: bytes) -> Record:
    d = _load(data, frozenset({"record"}))
    entries = d["record"]
    if not isinstance(entries, dict):
        raise Refused(RECORD_MALFORMED)
    out: Record = {}
    for k, v in cast(dict[str, object], entries).items():
        if k and not valid_path(k):
            raise Refused(RECORD_MALFORMED)
        dev, ino, ctime = _ints(v, 3)
        out[k] = (dev, ino, ctime)
    if "" not in out:
        raise Refused(RECORD_MALFORMED)
    return out


INTENT_FIELDS = frozenset({"id", "adapter", "install", "digest", "root"})


def _intent_bytes(i: Intent) -> bytes:
    return _dump({"id": i.id, "adapter": i.adapter, "install": i.install, "digest": i.digest,
                  "root": list(i.root)})


def _intent_of(data: bytes, staging_id: str) -> Intent:
    d = _load(data, INTENT_FIELDS)
    sid, adapter, install, dig = d["id"], d["adapter"], d["install"], d["digest"]
    if (sid != staging_id or not isinstance(adapter, str) or not PLAIN.fullmatch(adapter)
            or not isinstance(install, str) or not INSTALL.fullmatch(install)
            or not isinstance(dig, str) or not DIGEST.fullmatch(dig)):
        raise Refused(RECORD_MALFORMED)
    dev, ino = _ints(d["root"], 2)
    return Intent(staging_id, adapter, install, dig, (dev, ino))


def _failure_of(data: bytes, staging_id: str) -> str:
    d = _load(data, frozenset({"id", "refused"}))
    constant = d["refused"]
    if d["id"] != staging_id or not isinstance(constant, str) or not constant:
        raise Refused(RECORD_MALFORMED)
    return constant


def _opt_dir(dirfd: int, name: str) -> int | None:
    """The directory `name` under `dirfd`, opened, or None if it doesn't exist."""
    try:
        return _open(name, DIR_FLAGS, dirfd)
    except FileNotFoundError:
        return None


def _read_file(dirfd: int, name: str, limit: int = MAX_METADATA) -> bytes | None:
    """The regular file `name` under `dirfd`, or None if it doesn't exist. Anything else that can't be read
    whole refuses (`RECORD_MALFORMED`) or raises: missing is the only absence."""
    try:
        fd = _open(name, FILE_FLAGS, dirfd)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(RECORD_MALFORMED)
        data = _read_bounded(fd, limit)
    finally:
        os.close(fd)
    if data is None:
        raise Refused(RECORD_MALFORMED)
    return data


def _read_at(store_fd: int, directory: str, name: str, limit: int = MAX_METADATA) -> bytes | None:
    fd = _opt_dir(store_fd, directory)
    if fd is None:
        return None
    try:
        return _read_file(fd, name, limit)
    finally:
        os.close(fd)


def _write_durable(dirfd: int, name: str, data: bytes) -> None:
    """`name` under `dirfd` replaced atomically by `data` (0444): a temp file written and `fsync`ed, renamed
    over it, then the directory `fsync`ed."""
    tmp = f"{name}.tmp"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp, dir_fd=dirfd)
    fd = os.open(tmp, WRITE_FLAGS, 0o444, dir_fd=dirfd)
    try:
        _write_all(fd, data)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    os.fsync(dirfd)


def _unlink_durable(dirfd: int, name: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dirfd)
    os.fsync(dirfd)


def _write_at(store_fd: int, directory: str, name: str, data: bytes) -> None:
    fd = _make_dir(store_fd, directory, STORE_DIR_MODE)
    try:
        _write_durable(fd, name, data)
    finally:
        os.close(fd)


def _ids(store_fd: int, directory: str) -> list[str]:
    """The staging IDs that `directory`'s `<id>.json` files name, sorted; a stale temp file is skipped."""
    fd = _opt_dir(store_fd, directory)
    if fd is None:
        return []
    try:
        names = _names(fd)
    finally:
        os.close(fd)
    return sorted(n.removesuffix(".json") for n in names
                  if n.endswith(".json") and STAGING_ID.fullmatch(n.removesuffix(".json")))


def _receipts(store_fd: int) -> list[Intent]:
    out: list[Intent] = []
    for sid in _ids(store_fd, PUBLISHED):
        data = _read_at(store_fd, PUBLISHED, f"{sid}.json")
        if data is not None:
            out.append(_intent_of(data, sid))
    return out


def _intents(store_fd: int) -> list[Intent]:
    out: list[Intent] = []
    for sid in _ids(store_fd, PUBLISHING):
        data = _read_at(store_fd, PUBLISHING, f"{sid}.json")
        if data is not None:
            out.append(_intent_of(data, sid))
    return out


def _naming(intents: list[Intent], adapter: str, install: str) -> list[Intent]:
    return [i for i in intents if (i.adapter, i.install) == (adapter, install)]


def _meta(store_fd: int, adapter: str, install: str, what: str, limit: int = MAX_STORE_FILE) -> bytes | None:
    fd = _opt_dir(store_fd, adapter)
    if fd is None:
        return None
    try:
        return _read_at(fd, META, f"{install}.{what}.json", limit)
    finally:
        os.close(fd)


def _exists(dirfd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _held(store_fd: int, adapter: str, install: str) -> bool:
    """Anything in the store holds the name: the install path, either meta file, a receipt or an intent."""
    fd = _opt_dir(store_fd, adapter)
    if fd is not None:
        try:
            if _exists(fd, install):
                return True
            meta_fd = _opt_dir(fd, META)
            if meta_fd is not None:
                try:
                    if any(_exists(meta_fd, f"{install}.{w}.json") for w in ("manifest", "record")):
                        return True
                finally:
                    os.close(meta_fd)
        finally:
            os.close(fd)
    return bool(_naming(_receipts(store_fd), adapter, install)
                or _naming(_intents(store_fd), adapter, install))


def _structure(root_fd: int, m: Manifest) -> None:
    """The structural part of `check_fast` (E20 step 2): the manifest's entries, types, modes, sizes, link
    counts and targets, against a fresh record, so the record checks stay with the comparisons of R1, R2."""
    check_fast(root_fd, m, record(root_fd))


def _remove_tree(store_fd: int, parent: str, name: str, root: tuple[int, int]) -> None:
    """`<parent>/<name>` removed only if it is a directory whose `(dev, ino)` is `root` (E21)."""
    fd = _opt_dir(store_fd, parent)
    if fd is None:
        return
    try:
        try:
            st = statx_at(fd, name)
        except FileNotFoundError:
            return
        if st.kind == "dir" and (st.dev, st.ino) == root:
            _remove(fd, name)
            os.fsync(fd)
    finally:
        os.close(fd)


def _discard_staging(store_fd: int, staging_id: str, *, tree: bool) -> None:
    """`.staged/<id>.json`, then `.staging/<id>.src` and `<id>.lock` (and, with `tree`, `.staging/<id>`),
    each directory `fsync`ed after."""
    fd = _opt_dir(store_fd, STAGED)
    if fd is not None:
        try:
            _unlink_durable(fd, f"{staging_id}.json")
        finally:
            os.close(fd)
    fd = _opt_dir(store_fd, STAGING)
    if fd is not None:
        try:
            for name in ((staging_id,) if tree else ()) + (f"{staging_id}.src", f"{staging_id}.lock"):
                _remove(fd, name)
            os.fsync(fd)
        finally:
            os.close(fd)


def _cleanup(store_fd: int, staging_id: str, hook: Hook) -> None:
    """P6: the staged manifest, the staging's leftovers, then the intent's record and the intent, each
    followed by its directory's `fsync`."""
    fd = _opt_dir(store_fd, STAGED)
    if fd is not None:
        try:
            _unlink_durable(fd, f"{staging_id}.json")
        finally:
            os.close(fd)
    hook("P6.staged")
    fd = _opt_dir(store_fd, STAGING)
    if fd is not None:
        try:
            for name in (f"{staging_id}.src", f"{staging_id}.lock"):
                _remove(fd, name)
            os.fsync(fd)
        finally:
            os.close(fd)
    hook("P6.staging")
    fd = _opt_dir(store_fd, PUBLISHING)
    if fd is not None:
        try:
            _unlink_durable(fd, f"{staging_id}.record.json")
            hook("P6.record")
            _unlink_durable(fd, f"{staging_id}.json")
        finally:
            os.close(fd)
    hook("P6.intent")


def _fail(store_fd: int, staging_id: str, constant: str) -> None:
    """A publish refused before its intent: `.failed/<id>.json` first, then the staging is discarded."""
    _write_at(store_fd, FAILED, f"{staging_id}.json", _dump({"id": staging_id, "refused": constant}))
    _discard_staging(store_fd, staging_id, tree=True)


def _abandon(store_fd: int, intent: Intent, constant: str) -> None:
    """A publish that can't finish (E21, "anything else"): `.failed/<id>.json` first, so the intent is never
    the only evidence removed; then the tree, wherever it is, only if its root is the intent's; this
    install's meta files only if no receipt names it; then the staging's leftovers and the intent."""
    _write_at(store_fd, FAILED, f"{intent.id}.json", _dump({"id": intent.id, "refused": constant}))
    _remove_tree(store_fd, STAGING, intent.id, intent.root)
    _remove_tree(store_fd, intent.adapter, intent.install, intent.root)
    if not _naming(_receipts(store_fd), intent.adapter, intent.install):
        fd = _opt_dir(store_fd, intent.adapter)
        meta_fd = _opt_dir(fd, META) if fd is not None else None
        try:
            if meta_fd is not None:
                for what in ("record", "manifest"):
                    _unlink_durable(meta_fd, f"{intent.install}.{what}.json")
        finally:
            for f in (meta_fd, fd):
                if f is not None:
                    os.close(f)
    _cleanup(store_fd, intent.id, _nothing)


def _complete(store_fd: int, intent: Intent, data: bytes, rec: Record, hook: Hook) -> None:
    """P3 to P6: the meta manifest, the meta record, the receipt, then the cleanup."""
    adapter_fd = _open(intent.adapter, DIR_FLAGS, store_fd)
    try:
        meta_fd = _make_dir(adapter_fd, META, STORE_DIR_MODE)
        try:
            _write_durable(meta_fd, f"{intent.install}.manifest.json", data)
            hook("P3")
            _write_durable(meta_fd, f"{intent.install}.record.json", _record_bytes(rec))
            hook("P4")
        finally:
            os.close(meta_fd)
    finally:
        os.close(adapter_fd)
    _write_at(store_fd, PUBLISHED, f"{intent.id}.json", _intent_bytes(intent))
    hook("P5")
    _cleanup(store_fd, intent.id, hook)


def _publish(store_fd: int, staging_id: str, approved: str, hook: Hook, read: Reader, statx: StatAt) -> str:
    receipt = _read_at(store_fd, PUBLISHED, f"{staging_id}.json")
    if receipt is not None:
        done = _intent_of(receipt, staging_id)
        if done.digest != approved:
            raise Refused(DIGEST_MISMATCH)
        if _state(store_fd, done.adapter, done.install) != "published":
            raise Refused(INSTALL_BROKEN)
        return done.install
    failed = _read_at(store_fd, FAILED, f"{staging_id}.json")
    if failed is not None:
        raise Refused(_failure_of(failed, staging_id))
    data = _read_at(store_fd, STAGED, f"{staging_id}.json", MAX_STORE_FILE)
    if data is None:
        raise Refused(NO_SUCH_STAGING)
    m = parse(data)
    if digest(m) != approved:
        raise Refused(DIGEST_MISMATCH)
    if not PLAIN.fullmatch(m.adapter):
        raise Mismatch(MANIFEST_MALFORMED)
    name = install_name(m.version, approved)
    staging_fd = _open(STAGING, DIR_FLAGS, store_fd)
    try:
        try:
            root_fd = _open(staging_id, DIR_FLAGS, staging_fd)
        except FileNotFoundError:
            raise Refused(NO_SUCH_STAGING) from None
        try:
            return _publish_tree(store_fd, staging_fd, root_fd, staging_id, m, data, name, hook, read, statx)
        finally:
            os.close(root_fd)
    finally:
        os.close(staging_fd)


def _publish_tree(store_fd: int, staging_fd: int, root_fd: int, staging_id: str, m: Manifest, data: bytes,
                  name: str, hook: Hook, read: Reader, statx: StatAt) -> str:
    """E20's steps inside E21's phases. A refusal before the intent fails the staging; one after it
    abandons the publish, removing only what the intent identifies."""
    try:
        if _held(store_fd, m.adapter, name):
            raise Refused(NAME_CONFLICT)
        if statx_at(root_fd, "").mode != 0o555:     # a crash between the rename's two chmods left it 0755
            os.fchmod(root_fd, 0o555)
        r1 = record(root_fd)
        hook("R1")
        rehash(root_fd, m, read=read)
        _structure(root_fd, m)
        hook("REHASHED")
        r2 = record(root_fd)
        if r2 != r1:
            raise Refused(CHANGED_DURING_PUBLISH)
        hook("R2")
    except Refused as exc:
        _fail(store_fd, staging_id, exc.constant)
        raise
    intent = Intent(staging_id, m.adapter, name, digest(m), (r2[""][0], r2[""][1]))
    publishing_fd = _make_dir(store_fd, PUBLISHING, STORE_DIR_MODE)
    try:
        _write_durable(publishing_fd, f"{staging_id}.record.json", _record_bytes(r2))
        hook("P1.record")
        _write_durable(publishing_fd, f"{staging_id}.json", _intent_bytes(intent))
        hook("P1.intent")
    finally:
        os.close(publishing_fd)
    try:
        adapter_fd = _make_dir(store_fd, m.adapter, STORE_DIR_MODE)
        try:
            # Moving a directory to another parent rewrites its `..`, which needs write permission on it: the
            # root alone is made writable for the rename. Its change time is the one E20 lets move.
            os.fchmod(root_fd, 0o755)
            try:
                _rename_noreplace(staging_fd, staging_id, adapter_fd, name)
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise Refused(NAME_CONFLICT) from None
                raise
            finally:
                os.fchmod(root_fd, 0o555)
            os.fsync(adapter_fd)
            os.fsync(staging_fd)
            os.fsync(root_fd)
            hook("P2")
            st = statx(adapter_fd, name)
            if (st.dev, st.ino) != intent.root:
                raise Refused(CHANGED_DURING_PUBLISH)
            rec = {**r2, "": st.triple}
            try:
                check_fast(root_fd, m, rec)
            except Mismatch:
                raise Refused(CHANGED_DURING_PUBLISH) from None
        finally:
            os.close(adapter_fd)
        _complete(store_fd, intent, data, rec, hook)
    except Refused as exc:
        _abandon(store_fd, intent, exc.constant)
        raise
    return name


def _reverify(store_fd: int, intent: Intent, m: Manifest, r2: Record, hook: Hook, read: Reader) -> Record:
    """Recovery's check of a renamed tree (E21), with no new baseline: R3 is R2 but for the root's change
    time, the rehash and the structure pass, and R4 is R3. The record to write: R2 with R3's root."""
    adapter_fd = _open(intent.adapter, DIR_FLAGS, store_fd)
    try:
        root_fd = _open(intent.install, DIR_FLAGS | os.O_NOFOLLOW, adapter_fd)
    finally:
        os.close(adapter_fd)
    try:
        if statx_at(root_fd, "").mode != 0o555:     # a crash between the rename's two chmods
            os.fchmod(root_fd, 0o555)
        r3 = record(root_fd)
        if ({k: v for k, v in r3.items() if k} != {k: v for k, v in r2.items() if k}
                or r3[""][:2] != r2[""][:2]):
            raise Refused(RECOVERY_FAILED)
        try:
            rehash(root_fd, m, read=read)
            _structure(root_fd, m)
        except Mismatch:
            raise Refused(RECOVERY_FAILED) from None
        hook("RECOVER.REHASHED")
        if record(root_fd) != r3:
            raise Refused(RECOVERY_FAILED)
        return {**r2, "": r3[""]}
    finally:
        os.close(root_fd)


def _recover_one(store_fd: int, staging_id: str, hook: Hook, read: Reader) -> str:
    raw = _read_at(store_fd, PUBLISHING, f"{staging_id}.json")
    if raw is None:                 # P1 stopped between its two writes: the tree never left .staging
        fd = _open(PUBLISHING, DIR_FLAGS, store_fd)
        try:
            _unlink_durable(fd, f"{staging_id}.record.json")
        finally:
            os.close(fd)
        return "restarted"
    try:
        intent = _intent_of(raw, staging_id)
    except Refused:                 # nothing it names can be trusted: no tree is touched
        failure = _dump({"id": staging_id, "refused": RECOVERY_FAILED})
        _write_at(store_fd, FAILED, f"{staging_id}.json", failure)
        _discard_staging(store_fd, staging_id, tree=False)
        _cleanup(store_fd, staging_id, _nothing)
        return f"failed:{RECOVERY_FAILED}"
    receipt = _read_at(store_fd, PUBLISHED, f"{staging_id}.json")
    if receipt is not None:         # P6 was interrupted
        agrees = _intent_of(receipt, staging_id) == intent
        _cleanup(store_fd, staging_id, _nothing)
        return "published" if agrees else f"failed:{RECOVERY_FAILED}"
    failed = _read_at(store_fd, FAILED, f"{staging_id}.json")
    if failed is not None:          # an abandonment was interrupted: finish it
        constant = _failure_of(failed, staging_id)
        _abandon(store_fd, intent, constant)
        return f"failed:{constant}"
    try:
        rec_raw = _read_at(store_fd, PUBLISHING, f"{staging_id}.record.json", MAX_STORE_FILE)
        data = _read_at(store_fd, STAGED, f"{staging_id}.json", MAX_STORE_FILE)
        if data is None:
            data = _meta(store_fd, intent.adapter, intent.install, "manifest")
        if rec_raw is None or data is None:
            raise Refused(RECOVERY_FAILED)
        r2 = _record_of(rec_raw)
        m = parse(data)
        if digest(m) != intent.digest or r2[""][:2] != intent.root:
            raise Refused(RECOVERY_FAILED)
        staging_fd = _open(STAGING, DIR_FLAGS, store_fd)
        try:
            try:
                st = statx_at(staging_fd, staging_id)
            except FileNotFoundError:
                st = None
        finally:
            os.close(staging_fd)
        if st is not None and st.kind == "dir" and (st.dev, st.ino) == intent.root:
            fd = _open(PUBLISHING, DIR_FLAGS, store_fd)
            try:
                _unlink_durable(fd, f"{staging_id}.record.json")
                _unlink_durable(fd, f"{staging_id}.json")
            finally:
                os.close(fd)
            return "restarted"
        adapter_fd = _opt_dir(store_fd, intent.adapter)
        if adapter_fd is None:
            raise Refused(RECOVERY_FAILED)
        try:
            try:
                at = statx_at(adapter_fd, intent.install)
            except FileNotFoundError:
                raise Refused(RECOVERY_FAILED) from None
        finally:
            os.close(adapter_fd)
        if at.kind != "dir" or (at.dev, at.ino) != intent.root:
            raise Refused(RECOVERY_FAILED)
        rec = _reverify(store_fd, intent, m, r2, hook, read)
    except Refused as exc:
        _abandon(store_fd, intent, exc.constant)
        return f"failed:{exc.constant}"
    _complete(store_fd, intent, data, rec, _nothing)
    return "published"


def _recover(store_fd: int, hook: Hook, read: Reader) -> list[tuple[str, str]]:
    """Every intent recovered (E21). A temp file a crash left mid-write is removed first: the phase it
    belonged to never happened."""
    fd = _opt_dir(store_fd, PUBLISHING)
    if fd is None:
        return []
    try:
        names = _names(fd)
        stale = [n for n in names if n.endswith(".tmp")]
        for n in stale:
            os.unlink(n, dir_fd=fd)
        if stale:
            os.fsync(fd)
        names = [n for n in names if not n.endswith(".tmp")]
    finally:
        os.close(fd)
    ids = sorted({n.split(".", 1)[0] for n in names if STAGING_ID.fullmatch(n.split(".", 1)[0])})
    return [(sid, _recover_one(store_fd, sid, hook, read)) for sid in ids]


def _state(store_fd: int, adapter: str, install: str) -> State:
    """E21's published: one receipt names it, no intent does, both meta files exist and the meta manifest's
    digest is the receipt's, the root's `(dev, ino)` is the receipt's, and `check_fast` passes. Anything
    unreadable is not published."""
    if not PLAIN.fullmatch(adapter) or not INSTALL.fullmatch(install):
        raise Refused(NO_SUCH_INSTALL)
    try:
        receipts = _naming(_receipts(store_fd), adapter, install)
        if _naming(_intents(store_fd), adapter, install):
            return "publishing"
    except Refused:
        return "broken"
    if not receipts and not _held(store_fd, adapter, install):
        raise Refused(NO_SUCH_INSTALL)
    if len(receipts) != 1:
        return "broken"
    try:
        data = _meta(store_fd, adapter, install, "manifest")
        rec_raw = _meta(store_fd, adapter, install, "record")
        if data is None or rec_raw is None:
            return "broken"
        m = parse(data)
        rec = _record_of(rec_raw)
        if digest(m) != receipts[0].digest:
            return "broken"
        adapter_fd = _open(adapter, DIR_FLAGS, store_fd)
        try:
            root_fd = _open(install, DIR_FLAGS, adapter_fd)
        finally:
            os.close(adapter_fd)
        try:
            root = statx_at(root_fd, "")
            if (root.dev, root.ino) != receipts[0].root:
                return "broken"
            check_fast(root_fd, m, rec)
        finally:
            os.close(root_fd)
    except (Refused, OSError):
        return "broken"
    return "published"


def _outcome(store_fd: int, staging_id: str) -> str | None:
    if not STAGING_ID.fullmatch(staging_id):
        return None
    receipt = _read_at(store_fd, PUBLISHED, f"{staging_id}.json")
    if receipt is not None:
        done = _intent_of(receipt, staging_id)
        if _state(store_fd, done.adapter, done.install) == "published":
            return f"published:{done.install}"
        return f"failed:{INSTALL_BROKEN}"
    failed = _read_at(store_fd, FAILED, f"{staging_id}.json")
    if failed is not None:
        return f"failed:{_failure_of(failed, staging_id)}"
    fd = _opt_dir(store_fd, STAGED)
    if fd is None:
        return None
    try:
        return "staged" if _exists(fd, f"{staging_id}.json") else None
    finally:
        os.close(fd)


def _verify(store_fd: int, adapter: str, install: str, hook: Hook, read: Reader) -> None:
    """E18: V1, the rehash and the structure against the meta manifest, V2 (`CHANGED_DURING_VERIFY` unless it
    is V1), then V2 written as the record, and the receipt's root updated to V2's."""
    if not PLAIN.fullmatch(adapter) or not INSTALL.fullmatch(install):
        raise Refused(NO_SUCH_INSTALL)
    data = _meta(store_fd, adapter, install, "manifest")
    if data is None:
        raise Refused(NO_SUCH_INSTALL)
    m = parse(data)
    receipts = _naming(_receipts(store_fd), adapter, install)
    if len(receipts) != 1:
        raise Refused(INSTALL_BROKEN)
    if receipts[0].digest != digest(m):
        raise Refused(DIGEST_MISMATCH)
    adapter_fd = _open(adapter, DIR_FLAGS, store_fd)
    try:
        try:
            root_fd = _open(install, DIR_FLAGS, adapter_fd)
        except FileNotFoundError:
            raise Mismatch(ENTRY_MISSING) from None
        try:
            v1 = record(root_fd)
            rehash(root_fd, m, read=read)
            _structure(root_fd, m)
            hook("VERIFY.REHASHED")
            v2 = record(root_fd)
            if v2 != v1:
                raise Refused(CHANGED_DURING_VERIFY)
        finally:
            os.close(root_fd)
        meta_fd = _open(META, DIR_FLAGS, adapter_fd)
        try:
            _write_durable(meta_fd, f"{install}.record.json", _record_bytes(v2))
        finally:
            os.close(meta_fd)
    finally:
        os.close(adapter_fd)
    root = (v2[""][0], v2[""][1])
    if receipts[0].root != root:
        moved = dataclasses.replace(receipts[0], root=root)
        _write_at(store_fd, PUBLISHED, f"{moved.id}.json", _intent_bytes(moved))


def _remove_install(store_fd: int, adapter: str, install: str, hook: Hook) -> None:
    """The meta record, the meta manifest, the tree bottom-up, then the receipt (E21); a rerun finishes a
    partial removal."""
    if not PLAIN.fullmatch(adapter) or not INSTALL.fullmatch(install):
        raise Refused(NO_SUCH_INSTALL)
    receipts = _naming(_receipts(store_fd), adapter, install)
    if not receipts and not _held(store_fd, adapter, install):
        raise Refused(NO_SUCH_INSTALL)
    adapter_fd = _opt_dir(store_fd, adapter)
    if adapter_fd is not None:
        try:
            meta_fd = _opt_dir(adapter_fd, META)
            if meta_fd is not None:
                try:
                    _unlink_durable(meta_fd, f"{install}.record.json")
                    hook("REMOVE.record")
                    _unlink_durable(meta_fd, f"{install}.manifest.json")
                    hook("REMOVE.manifest")
                finally:
                    os.close(meta_fd)
            _remove(adapter_fd, install)
            os.fsync(adapter_fd)
            hook("REMOVE.tree")
        finally:
            os.close(adapter_fd)
    fd = _opt_dir(store_fd, PUBLISHED)
    if fd is not None:
        try:
            for r in receipts:
                _unlink_durable(fd, f"{r.id}.json")
            hook("REMOVE.receipt")
        finally:
            os.close(fd)


def _age_ok(dirfd: int, name: str, now: float, hours: float) -> bool:
    """True if `name` under `dirfd` changed last more than `hours` before `now`."""
    try:
        st = statx_at(dirfd, name)
    except FileNotFoundError:
        return False
    return now - st.ctime_ns / 1e9 > hours * 3600


def _sweep(store_fd: int, now: float, hours: float) -> int:
    """Removes stagings no intent names (E21): a partial one (no staged manifest, its builder's lock free or
    gone) at any age, and a complete one, staged manifest first, once older than `hours`. A staging whose
    lock is held is still being built and is skipped. The count is of stagings removed."""
    staging_fd = _opt_dir(store_fd, STAGING)
    if staging_fd is None:
        return 0
    publishing = set(_ids(store_fd, PUBLISHING))
    staged_fd = _make_dir(store_fd, STAGED, STORE_DIR_MODE)
    removed = 0
    try:
        groups: dict[str, set[str]] = {}
        for n in _names(staging_fd):
            sid, dot, rest = n.partition(".")
            if STAGING_ID.fullmatch(sid) and rest in (("",) if not dot else ("src", "lock")):
                groups.setdefault(sid, set()).add(n)
        for sid in sorted(groups):
            if sid in publishing:
                continue
            complete = _exists(staged_fd, f"{sid}.json")
            if f"{sid}.lock" in groups[sid]:
                lock_fd = _open(f"{sid}.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, staging_fd)
                try:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue            # still being built
                    if not complete:
                        for name in sorted(groups[sid]):
                            _remove(staging_fd, name)
                        os.fsync(staging_fd)
                        removed += 1
                        continue
                    os.unlink(f"{sid}.lock", dir_fd=staging_fd)     # its builder died after the commit
                finally:
                    os.close(lock_fd)
            if not complete:
                for name in sorted(groups[sid]):
                    _remove(staging_fd, name)
                os.fsync(staging_fd)
                removed += 1
            elif _age_ok(staging_fd, sid, now, hours):
                _unlink_durable(staged_fd, f"{sid}.json")
                for name in (sid, f"{sid}.src"):
                    _remove(staging_fd, name)
                os.fsync(staging_fd)
                removed += 1
    finally:
        os.close(staged_fd)
        os.close(staging_fd)
    return removed


def _installs(store_fd: int) -> list[dict[str, object]]:
    names: set[tuple[str, str]] = set()
    for n in _names(store_fd):
        if not PLAIN.fullmatch(n):
            continue
        fd = _opt_dir(store_fd, n)
        if fd is None:
            continue
        try:
            meta_fd = _opt_dir(fd, META)
            if meta_fd is not None:
                try:
                    for f in _names(meta_fd):
                        install = f.removesuffix(".manifest.json")
                        if f.endswith(".manifest.json") and INSTALL.fullmatch(install):
                            names.add((n, install))
                finally:
                    os.close(meta_fd)
        finally:
            os.close(fd)
    names |= {(i.adapter, i.install) for i in _receipts(store_fd) + _intents(store_fd)}
    out: list[dict[str, object]] = []
    for adapter, install in sorted(names):
        info: dict[str, object] = {"name": install, "adapter": adapter, "version": "", "digest": "",
                                   "entries": 0, "size": 0, "state": _state(store_fd, adapter, install)}
        data = _meta(store_fd, adapter, install, "manifest")
        try:
            m = parse(data) if data is not None else None
        except Refused:
            m = None
        if m is not None:
            info |= {"version": m.version, "digest": digest(m), "entries": len(m.entries),
                     "size": sum(e.size for e in m.entries if e.kind == "file")}
        out.append(info)
    return out


def publish(store: Path, staging_id: str, approved_digest: str, *, deadline: float = LOCK_SECONDS,
            hook: Hook = _nothing, read: Reader = os.read, statx: StatAt = statx_at) -> str:
    """Publish the staging `staging_id` as `<store>/<adapter>/<version>-<12 hex>` (E20, E21), if its staged
    manifest's digest is `approved_digest`, and return the install's name. Idempotent: a staging already
    published returns its install. Under the store lock, after recovery."""
    if not STAGING_ID.fullmatch(staging_id):
        raise Refused(NO_SUCH_STAGING)
    with store_lock(store, deadline=deadline) as store_fd:
        _recover(store_fd, _nothing, os.read)
        return _publish(store_fd, staging_id, approved_digest, hook, read, statx)


def recover(store: Path, *, deadline: float = LOCK_SECONDS, hook: Hook = _nothing,
            read: Reader = os.read) -> list[tuple[str, str]]:
    """E21's recovery of every intent, as (staging ID, `published`, `restarted` or `failed:<constant>`)."""
    with store_lock(store, deadline=deadline) as store_fd:
        return _recover(store_fd, hook, read)


def outcome(store: Path, staging_id: str, *, deadline: float = LOCK_SECONDS) -> str | None:
    """`published:<install>`, `staged`, `failed:<constant>` or None, from the store's own evidence."""
    with store_lock(store, shared=True, deadline=deadline) as store_fd:
        return _outcome(store_fd, staging_id)


def state(store: Path, adapter: str, install: str, *, deadline: float = LOCK_SECONDS) -> State:
    with store_lock(store, shared=True, deadline=deadline) as store_fd:
        return _state(store_fd, adapter, install)


def verify(store: Path, adapter: str, install: str, *, deadline: float = LOCK_SECONDS, hook: Hook = _nothing,
           read: Reader = os.read) -> None:
    with store_lock(store, deadline=deadline) as store_fd:
        _recover(store_fd, _nothing, os.read)
        _verify(store_fd, adapter, install, hook, read)


def remove(store: Path, adapter: str, install: str, *, deadline: float = LOCK_SECONDS,
           hook: Hook = _nothing) -> None:
    with store_lock(store, deadline=deadline) as store_fd:
        _recover(store_fd, _nothing, os.read)
        _remove_install(store_fd, adapter, install, hook)


def sweep(store: Path, now: float, hours: float, *, deadline: float = LOCK_SECONDS) -> int:
    with store_lock(store, deadline=deadline) as store_fd:
        return _sweep(store_fd, now, hours)


def list_installs(store: Path, *, deadline: float = LOCK_SECONDS) -> list[dict[str, object]]:
    """Every install the store's meta files, receipts and intents name, with its state."""
    with store_lock(store, shared=True, deadline=deadline) as store_fd:
        return _installs(store_fd)


def read_staged(store: Path, staging_id: str) -> Manifest:
    """The staged manifest of `staging_id`, read without the store lock (the service user can't take a lock
    root made): a staging's manifest is written once and never changed."""
    if not STAGING_ID.fullmatch(staging_id):
        raise Refused(NO_SUCH_STAGING)
    store_fd = _open_store(store)
    try:
        data = _read_at(store_fd, STAGED, f"{staging_id}.json", MAX_STORE_FILE)
    finally:
        os.close(store_fd)
    if data is None:
        raise Refused(NO_SUCH_STAGING)
    return parse(data)


# --- the store helper (plan 4c E1, E3): one request a connection, as JSON, its artifacts as fds ---

PEER_REFUSED = "the peer is not the service user"
HELPER_FAILED = "the store helper failed or did not answer"
REQUEST_TOO_LARGE = "the request is larger than the helper accepts"
TOO_MANY_FDS = "the request carries more fds than the helper accepts"
BAD_REQUEST = "the request is not one the helper accepts"
WRONG_STORE = "the request names another store than the helper's"
BAD_CONFIG = "the helper's config is not in its form"
MAX_REQUEST = 1 << 20           # one request's bytes, its newline included; at the limit is accepted
MAX_FDS = 16                    # one request's fds; at the limit is accepted
CONFIG_EXIT = 2
HANG_UP_SECONDS = 1.0
CONFIG = Path("/etc/heterodyne/store-helper.json")
STAGING_HOURS = 24.0
CONFIG_FIELDS = frozenset({"service_uid", "store"})
CONFIG_OPTIONAL = frozenset({"staging_hours"})


@dataclasses.dataclass(frozen=True)
class HelperConfig:
    service_uid: int
    store: Path
    staging_hours: float = STAGING_HOURS
    lock_seconds: float = LOCK_SECONDS


def load_config(path: Path) -> HelperConfig:
    """The helper's config: `service_uid`, an absolute `store` and optionally `staging_hours`; anything else,
    or a config that can't be read, refuses (`BAD_CONFIG`)."""
    try:
        fd = os.open(path, FILE_FLAGS)
    except OSError:
        raise Refused(BAD_CONFIG) from None
    try:
        data = _read_bounded(fd, MAX_METADATA)
    finally:
        os.close(fd)
    try:
        doc: object = json.loads(data) if data is not None else None
    except (ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict):
        raise Refused(BAD_CONFIG)
    d = cast(dict[str, object], doc)
    if not CONFIG_FIELDS <= frozenset(d) <= CONFIG_FIELDS | CONFIG_OPTIONAL:
        raise Refused(BAD_CONFIG)
    uid, store, hours = d["service_uid"], d["store"], d.get("staging_hours", STAGING_HOURS)
    if (type(uid) is not int or uid < 0 or not isinstance(store, str) or not Path(store).is_absolute()
            or type(hours) not in (int, float) or not 0 < cast(float, hours) < 1e6):
        raise Refused(BAD_CONFIG)
    return HelperConfig(uid, Path(store), float(cast(float, hours)))


def encode_request(op: str, store: Path, args: dict[str, object]) -> bytes:
    return json.dumps({"op": op, "store": str(store), "args": args}, sort_keys=True).encode() + b"\n"


def stage_args(adapter: str, layout: Layout, parts: Sequence[Part], login_names: frozenset[str], *,
               verity: bool, target: str) -> dict[str, object]:
    return {"adapter": adapter, "layout": dataclasses.asdict(layout),
            "parts": [dataclasses.asdict(p) for p in parts], "login_names": sorted(login_names),
            "verity": verity, "target": target}


def _bad() -> Refused:
    return Refused(BAD_REQUEST)


def _value(t: object, v: object) -> object:
    """`v` as the field type `t`: a str, bool, int, an optional str, a Literal or a dataclass."""
    if t is str or t is bool or t is int:
        if type(v) is not t:
            raise _bad()
        return v
    if t == str | None:
        if v is not None and type(v) is not str:
            raise _bad()
        return v
    if get_origin(t) is Literal:
        if type(v) is not str or v not in get_args(t):
            raise _bad()
        return v
    if isinstance(t, type) and dataclasses.is_dataclass(t):
        return _decode(t, v)
    raise _bad()


def _decode[T](cls: type[T], v: object) -> T:
    fields = dataclasses.fields(cast(Any, cls))
    if not isinstance(v, dict) or frozenset(cast(dict[str, object], v)) != frozenset(f.name for f in fields):
        raise _bad()
    d = cast(dict[str, object], v)
    return cls(**{f.name: _value(f.type, d[f.name]) for f in fields})


def _args(args: dict[str, object], fields: dict[str, type]) -> list[object]:
    if frozenset(args) != frozenset(fields):
        raise _bad()
    return [_value(t, args[k]) for k, t in fields.items()]


@dataclasses.dataclass(frozen=True)
class Request:
    op: str
    store: str
    args: dict[str, object]


def decode_request(data: bytes) -> Request:
    """One request line (BAD_REQUEST unless it is `{"op", "store", "args"}` with a known op)."""
    try:
        doc: object = json.loads(data)
    except (ValueError, RecursionError):
        raise _bad() from None
    if not isinstance(doc, dict):
        raise _bad()
    d = cast(dict[str, object], doc)
    op, store, args = d.get("op"), d.get("store"), d.get("args")
    if (frozenset(d) != REQUEST_FIELDS or not isinstance(op, str) or op not in OPS
            or not isinstance(store, str) or not isinstance(args, dict)):
        raise _bad()
    return Request(op, store, cast(dict[str, object], args))


REQUEST_FIELDS = frozenset({"op", "store", "args"})
STAGE_FIELDS = frozenset({"adapter", "layout", "parts", "login_names", "verity", "target"})
INSTALL_ARGS: dict[str, type] = {"adapter": str, "install": str}
OPS = frozenset({"stage", "publish", "outcome", "state", "verify", "remove", "recover", "list"})


def handle(store: Path, request: Request, fds: Sequence[int], *, now: float, hours: float,
           deadline: float) -> object:
    """One request against `store`, its result as JSON values. Every request first recovers and sweeps
    under the store lock, so crash leftovers never wait for a restart; `stage` then runs with its own
    locks."""
    op, args = request.op, request.args
    if op != "stage" and fds:
        raise _bad()
    run_stage = _stage_request(store, args, fds, deadline) if op == "stage" else None
    with store_lock(store, deadline=deadline) as store_fd:
        recovered = _recover(store_fd, _nothing, os.read)
        _sweep(store_fd, now, hours)
        match op:
            case "publish":
                sid, dig = _args(args, {"id": str, "digest": str})
                if not STAGING_ID.fullmatch(cast(str, sid)):
                    raise Refused(NO_SUCH_STAGING)
                return _publish(store_fd, cast(str, sid), cast(str, dig), _nothing, os.read, statx_at)
            case "outcome":
                [sid] = _args(args, {"id": str})
                return _outcome(store_fd, cast(str, sid))
            case "state":
                adapter_, install = _args(args, INSTALL_ARGS)
                return _state(store_fd, cast(str, adapter_), cast(str, install))
            case "verify":
                adapter_, install = _args(args, INSTALL_ARGS)
                return _verify(store_fd, cast(str, adapter_), cast(str, install), _nothing, os.read)
            case "remove":
                adapter_, install = _args(args, INSTALL_ARGS)
                return _remove_install(store_fd, cast(str, adapter_), cast(str, install), _nothing)
            case "recover":
                _args(args, {})
                return recovered
            case "list":
                _args(args, {})
                return _installs(store_fd)
            case _:
                pass
    if run_stage is None:               # every other op returned above
        raise _bad()
    return dataclasses.asdict(run_stage())


def _stage_request(store: Path, args: dict[str, object], fds: Sequence[int],
                   deadline: float) -> Callable[[], Staged]:
    """A `stage` request's arguments, decoded before anything runs; the stage itself, to run after the
    recovery and the sweep."""
    if frozenset(args) != STAGE_FIELDS:
        raise _bad()
    parts, names = args["parts"], args["login_names"]
    if not isinstance(parts, list) or not isinstance(names, list):
        raise _bad()
    adapter, target = cast(str, _value(str, args["adapter"])), cast(str, _value(str, args["target"]))
    verity = cast(bool, _value(bool, args["verity"]))
    decoded = [_decode(Part, p) for p in cast(list[object], parts)]
    login = frozenset(cast(str, _value(str, n)) for n in cast(list[object], names))
    layout = _decode(Layout, args["layout"])
    return lambda: stage(store, adapter, layout, decoded, fds, login, verity=verity, target=target,
                         deadline=deadline)


def _peer_uid(conn: socket.socket) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def _receive(conn: socket.socket, fds: list[int]) -> bytes:
    """One request line and its fds, which are appended to `fds` as they arrive so the caller closes them."""
    data = b""
    while not data.endswith(b"\n"):
        chunk, got, flags, _ = socket.recv_fds(conn, 1 << 16, MAX_FDS, socket.MSG_CMSG_CLOEXEC)
        fds.extend(got)
        if flags & socket.MSG_CTRUNC or len(fds) > MAX_FDS:
            raise Refused(TOO_MANY_FDS)
        if not chunk:
            raise _bad()
        data += chunk
        if len(data) > MAX_REQUEST:
            raise Refused(REQUEST_TOO_LARGE)
    if data.count(b"\n") != 1:
        raise _bad()
    return data


def serve(conn: socket.socket, config: HelperConfig, clock: Callable[[], float], *,
          peer_uid: Callable[[socket.socket], int] = _peer_uid) -> bool:
    """Answer one request on `conn`: the peer must be the service user (nothing is read otherwise), the
    request must name this helper's store, and the reply is one JSON line, `{"ok": true, "result": ...}` or
    `{"ok": false, "refused": <constant>}`. Every fd received is closed; `conn` is the caller's. True if the
    peer check passed, so the request was read."""
    fds: list[int] = []
    trusted = False
    try:
        try:
            if peer_uid(conn) != config.service_uid:
                raise Refused(PEER_REFUSED)
            trusted = True
            request = decode_request(_receive(conn, fds))
            if request.store != str(config.store):
                raise Refused(WRONG_STORE)
            result = handle(config.store, request, fds, now=clock(), hours=config.staging_hours,
                            deadline=config.lock_seconds)
            reply: dict[str, object] = {"ok": True, "result": result}
        except Refused as exc:
            reply = {"ok": False, "refused": exc.constant}
        except Exception as exc:        # anything else is the helper's failure; its type names no path
            reply = {"ok": False, "refused": HELPER_FAILED, "type": type(exc).__name__}
        conn.sendall(json.dumps(reply, sort_keys=True).encode() + b"\n")
    finally:
        for fd in fds:
            os.close(fd)
    return trusted


def main(argv: Sequence[str] | None = None) -> int:
    """The helper as systemd runs it for one connection (`Accept=yes`): the socket is stdin."""
    parser = argparse.ArgumentParser(prog="store_core")
    parser.add_argument("--config", type=Path, default=CONFIG)
    ns = parser.parse_args(argv)
    with socket.socket(fileno=0) as conn:
        try:
            config = load_config(cast(Path, ns.config))
        except Refused as exc:
            os.write(2, exc.constant.encode() + b"\n")
            _hang_up(conn, read=False)
            return CONFIG_EXIT
        _hang_up(conn, read=serve(conn, config, time.time))
    return 0


def _hang_up(conn: socket.socket, *, read: bool) -> None:
    """End the reply, then discard, unparsed, whatever request bytes are still queued: closing a Unix socket
    with unread data resets it, and the peer would lose the reply. A request never read may still be on its
    way, so that one is waited for, at most `HANG_UP_SECONDS`. At most `MAX_REQUEST` bytes are dropped; any
    fds among them are released by the kernel."""
    conn.shutdown(socket.SHUT_WR)
    if not read:
        poller = select.poll()
        poller.register(conn, select.POLLIN | select.POLLRDHUP)
        poller.poll(HANG_UP_SECONDS * 1000)
    dropped = 0
    while dropped <= MAX_REQUEST:
        try:
            chunk = conn.recv(1 << 16, socket.MSG_DONTWAIT)
        except BlockingIOError:
            return
        if not chunk:
            return
        dropped += len(chunk)


if __name__ == "__main__":
    raise SystemExit(main())
