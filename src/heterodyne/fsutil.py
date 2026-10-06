"""Filesystem helpers shared by the daemons (admind, wsd)."""

import os
import stat
from pathlib import Path


def private_dir(path: Path) -> None:
    """Create `path` (and missing parents) and make it a 0700 directory owned by this user. A symlink in
    the final component, or a directory someone else owns, is refused: `path` is lstat'ed, opened with
    O_NOFOLLOW | O_DIRECTORY and the two are compared, so the chmod lands on the directory that was
    checked. Parents above `path` are not inspected."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = os.lstat(path)
    if not stat.S_ISDIR(before.st_mode):
        raise PermissionError(f"{path} is not a plain directory (a symlink is refused)")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PermissionError(f"{path} changed while it was checked")
        if opened.st_uid != os.geteuid():
            raise PermissionError(f"{path} is not owned by the service user")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
