"""The per-credential-identity host lock (ADR 0001 §4.4 D8; plan 4b E2).

Every harness read of a login and every write back to one holds the lock of the login's credential key:
the runtime's key check and freshness reread, the trusted host-side read, and
`heterodyne setup`. A key is a digest of the login's paths (AU-2), so the lock file's name never leaks a
path. flock locks an open file description, so two threads of one process exclude each other as two
processes do. The wait is bounded: a holder that never lets go refuses the operation, never hangs it.
"""

import contextlib
import fcntl
import os
import re
import stat
import time
from collections.abc import Callable, Generator
from pathlib import Path

from heterodyne.fsutil import private_dir
from heterodyne.wsd.gate import open_lock_file

KEY = re.compile(r"ck1-[0-9a-f]{32}")
POLL_SECONDS = 0.05
LOCK_BUSY = "the account's login is locked by another harness operation"
LOCK_FAILED = "the account's login lock can't be opened or taken"
BAD_KEY = "the credential key is not a valid key"


class CredentialBusy(Exception):
    """The lock was not taken; nothing under it ran. The text is LOCK_BUSY, LOCK_FAILED or BAD_KEY."""


def _open_in(lock_dir: Path, name: str) -> int:
    """Open the lock file relative to a descriptor of the checked directory. The directory is opened
    without following a link and checked again through that descriptor (owned by this user, 0700), so a
    link swapped in after `private_dir` is refused and a swap after the open can't redirect the file."""
    dir_fd = os.open(lock_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(dir_fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise PermissionError("the lock directory is not private")
        return open_lock_file(Path(name), dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


@contextlib.contextmanager
def credential_lock(lock_dir: Path, key: str, wait_seconds: float, *,
                    clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> Generator[None]:
    """Hold the lock of `key` for the body. The key is checked before it is used in a path. The lock
    directory is made 0700 (a symlink is refused) and the lock file 0600, never through a link. The wait
    polls without blocking, for at most `wait_seconds` in all."""
    if not KEY.fullmatch(key):
        raise CredentialBusy(BAD_KEY)
    try:
        private_dir(lock_dir)
        fd = _open_in(lock_dir, f"{key}.lock")
    except OSError:
        raise CredentialBusy(LOCK_FAILED) from None
    try:
        end = clock() + wait_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                left = end - clock()
                if left <= 0:
                    raise CredentialBusy(LOCK_BUSY) from None
                sleep(min(POLL_SECONDS, left))
            except OSError:
                raise CredentialBusy(LOCK_FAILED) from None
        yield
    finally:
        os.close(fd)                # closing the descriptor releases the lock
