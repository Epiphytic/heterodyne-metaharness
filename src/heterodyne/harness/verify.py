"""The launcher's check of a pinned install (ADR r16 §7 Managed installs; plan 4c E17, E22, E23): `verify_pin`
reads the install straight from the store, without its lock, and refuses with one of `REFUSALS`. An integrity
refusal (`TAINTING`) first writes the install's taint marker, durably, and only then raises: the marker is
what makes the refusal outlive a crash, and while it exists every launch on the install refuses."""

import json
import os
import secrets
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from heterodyne.agents.base import UNDECLARED_VERSION, Adapter, Cli
from heterodyne.harness.resources import store_core as sc
from heterodyne.harness.selection import InstallPin
from heterodyne.harness.store import Store
from heterodyne.wsd.runtime import INSTALL_UNPINNED, NOT_SELECTED

INSTALL_TAINTED = ("the install is tainted: a verification of it failed; check it with "
                   "`wsctl harness verify` or remove it")
STORE_UNSAFE = "the install store's location is unsafe; `heterodyne config check` lists its problems"
NO_SUCH_INSTALL = sc.NO_SUCH_INSTALL
META_UNREADABLE = "the install's meta manifest or record is missing, unreadable or malformed"
DIGEST_DIFFERS = "the install's manifest digest is not the one its launch pinned"
INSTALL_CHANGED = "the install's tree is not the one its manifest and record describe"
TAINTING = (META_UNREADABLE, DIGEST_DIFFERS, INSTALL_CHANGED)
REFUSALS = (INSTALL_TAINTED, STORE_UNSAFE, NO_SUCH_INSTALL, META_UNREADABLE, DIGEST_DIFFERS, INSTALL_CHANGED,
            UNDECLARED_VERSION, NOT_SELECTED, INSTALL_UNPINNED)
MARKER_BYTES = 4096


class VerifyRefused(Exception):
    """`verify_pin` refused: `.constant` is one of `REFUSALS`, and the whole message."""

    def __init__(self, constant: str) -> None:
        super().__init__(constant)
        self.constant = constant


@dataclass(frozen=True)
class VerifiedInstall:
    adapter: str
    name: str
    digest: str
    root: Path
    manifest: sc.Manifest
    record: sc.Record

    @property
    def cli(self) -> Cli:
        return Cli(self.root / self.manifest.entry_point, self.root, self.manifest.version, self.name,
                   self.digest)


def install_record(v: VerifiedInstall, empty_read_only: Sequence[str] = ()) -> str:
    """The Managed install probe's input (`/run/hz/install-record.json`): the install path, the root's
    `(dev, ino, mode, ctime_ns)`, and every entry's `(path, kind, mode, size, nlink, dev, ino, ctime_ns,
    target)` from the verified manifest and record, which S9-9 confirmed read the same inside; and the inside
    paths of E25's empty read-only binds. The mount ID is never in it: each namespace numbers its own."""
    dev, ino, ctime_ns = v.record[""]
    entries = [[e.path, e.kind, e.mode, e.size, e.nlink, *v.record[e.path], e.target]
               for e in v.manifest.entries]
    return json.dumps({"path": str(v.root), "root": [dev, ino, 0o555, ctime_ns], "entries": entries,
                       "empty_read_only": list(empty_read_only)})


Taint = Callable[[Path, str, str, str], None]


def _marker(taint_dir: Path, adapter: str, install: str) -> Path:
    return taint_dir / adapter / install


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def taint(taint_dir: Path, adapter: str, install: str, constant: str) -> None:
    """Write the install's taint marker (`<taint_dir>/<adapter>/<install>`, 0600, holding `constant`): a temp
    file, fsynced, renamed into place, then its directory fsynced."""
    folder = taint_dir / adapter
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = folder / f".{install}.{secrets.token_hex(8)}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(constant + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        temp.replace(folder / install)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    _fsync_dir(folder)


def tainted(taint_dir: Path, adapter: str, install: str) -> bool:
    """Whether the install's taint marker exists. A marker that can't be read as a regular file (a directory
    where it should be, no permission) raises OSError: the caller refuses as if it were tainted."""
    path = _marker(taint_dir, adapter, install)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("the taint marker is not a regular file")
        os.read(fd, MARKER_BYTES)
    finally:
        os.close(fd)
    return True


def clear_taint(taint_dir: Path, adapter: str, install: str) -> None:
    """Remove the install's taint marker, if any, and fsync its directory."""
    path = _marker(taint_dir, adapter, install)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_dir(path.parent)


def verify_pin(store: Store, adapter: Adapter, pin: InstallPin, taint_dir: Path, *,
               problems: Callable[[], Sequence[str]], mark: Taint = taint) -> VerifiedInstall:
    """The install `pin` names, checked in E22's order: no taint marker (`INSTALL_TAINTED`), no store problem
    (`STORE_UNSAFE`; `problems` raising counts as one), the install exists (`NO_SUCH_INSTALL`), its meta files
    read (`META_UNREADABLE`), the meta manifest's digest is the pin's (`DIGEST_DIFFERS`), `check_fast` passes
    (`INSTALL_CHANGED`), and its version is one the adapter declares (`UNDECLARED_VERSION`). For a refusal in
    `TAINTING`, `mark` writes the marker before `VerifyRefused` is raised."""
    if not sc.PLAIN.fullmatch(adapter.name) or not sc.INSTALL.fullmatch(pin.install):
        raise VerifyRefused(NO_SUCH_INSTALL)        # before any path is built from the names
    try:
        if tainted(taint_dir, adapter.name, pin.install):
            raise VerifyRefused(INSTALL_TAINTED)
    except OSError:
        raise VerifyRefused(INSTALL_TAINTED) from None
    try:
        unsafe = bool(problems())
    except Exception:  # noqa: BLE001 - problems that can't be listed are not "no problems"
        unsafe = True
    if unsafe:
        raise VerifyRefused(STORE_UNSAFE)

    def refuse(constant: str) -> VerifyRefused:
        mark(taint_dir, adapter.name, pin.install, constant)
        return VerifyRefused(constant)

    try:
        root_fd = sc.open_install(store.root, adapter.name, pin.install)
    except (sc.Refused, OSError):
        raise VerifyRefused(NO_SUCH_INSTALL) from None
    try:
        try:
            meta = sc.read_meta(store.root, adapter.name, pin.install)
        except (sc.Refused, OSError):
            meta = None
        if meta is None:
            raise refuse(META_UNREADABLE)
        manifest, record = meta
        if sc.digest(manifest) != pin.digest:
            raise refuse(DIGEST_DIFFERS)
        try:
            sc.check_fast(root_fd, manifest, record)
        except (sc.Refused, OSError):
            raise refuse(INSTALL_CHANGED) from None
    finally:
        os.close(root_fd)
    if manifest.version not in adapter.versions:
        raise VerifyRefused(UNDECLARED_VERSION)
    return VerifiedInstall(adapter.name, pin.install, pin.digest, store.root / adapter.name / pin.install,
                           manifest, record)
