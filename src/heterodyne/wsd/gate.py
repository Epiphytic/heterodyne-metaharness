"""The shared pause gate and the per-workstream claim lock (ADR 0001 §4.3).

Pause is the btq `paused` flag of the workstream-session worker. Every claim runs inside the
workstream's claim lock and re-checks that flag immediately before `claim()`; pausing takes the same
lock before it sets the flag and acknowledges. So once a pause is acknowledged, no claim can start. The
lock is an flock on a file in wsd's state directory, so `wsctl` (another process) and wsd share it.
A direct `btq pause` sets the same flag without the lock: honoured from the next claim check (best effort).
"""

import contextlib
import fcntl
import os
import stat
from collections.abc import Generator
from pathlib import Path

from heterodyne.fsutil import private_dir
from heterodyne.wsd import ids
from heterodyne.wsd.beads import Bead, BeadsAdapter
from heterodyne.wsd.checkpoints import Checkpoint, nothing

POINTS = ("gate.checked",)
PAUSE_POINTS = ("gate.pause.waiting",)


class Paused(Exception):
    """The workstream is paused: no claim was attempted."""


class AlreadyRunning(Exception):
    """Another wsd holds the instance lock for this state directory."""


class ClaimGate:
    def __init__(self, lock_dir: Path, beads: BeadsAdapter, cp: Checkpoint = nothing) -> None:
        self.lock_dir = lock_dir
        self.beads = beads
        self.cp = cp

    @contextlib.contextmanager
    def locked(self, ws: str) -> Generator[None]:
        """The workstream's claim lock. A fresh descriptor per use, so threads exclude each other too."""
        private_dir(self.lock_dir)
        fd = open_lock_file(self.lock_dir / f"{ids.slug(ws, 'workstream')}.claim")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)        # closing the descriptor releases the lock

    def pause(self, ws: str) -> None:
        """Returns (acknowledges) only once the flag reads back, with no claim in flight."""
        self.cp("gate.pause.waiting")
        with self.locked(ws):
            self.beads.set_paused(ws, True)

    def resume(self, ws: str) -> None:
        self.cp("gate.pause.waiting")
        with self.locked(ws):
            self.beads.set_paused(ws, False)

    def claim(self, ws: str, bead: str) -> Bead:
        with self.locked(ws):
            if self.beads.paused(ws):
                raise Paused(ws)
            self.cp("gate.checked")
            return self.beads.claim(ws, bead)


def open_lock_file(path: Path) -> int:
    """Open (creating it 0600) a lock file: a regular file owned by this user, never through a symlink.
    A broader mode left on an existing file is narrowed to 0600; anything else is refused."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError(f"{path.name} is not a regular file")
        if st.st_uid != os.geteuid():
            raise PermissionError(f"{path.name} is not owned by the service user")
        if stat.S_IMODE(st.st_mode) & ~0o600:
            os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


def instance_lock(path: Path) -> int:
    """Take the single-instance lock for a wsd state directory; the descriptor is held for the process's
    life. Two wsd processes on one journal would each believe they own every in-flight operation."""
    private_dir(path.parent)
    fd = open_lock_file(path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AlreadyRunning(str(path.name)) from None
    except BaseException:      # any other failure (ENOLCK, an interrupt) leaves no descriptor behind
        os.close(fd)
        raise
    return fd
