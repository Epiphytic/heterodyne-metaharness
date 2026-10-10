"""Where pytest's base temp goes on Linux (tests/conftest.py): a private directory under /dev/shm when
that is a writable tmpfs with room to spare, else None (pytest's default, on disk)."""

import os
import shutil
import tempfile
from pathlib import Path

RAM_TMP = Path("/dev/shm")    # noqa: S108 - a private mkdtemp under it, removed at exit
MIN_FREE = 1 << 30            # below this, a run could fill RAM: stay on disk
MOUNTS = Path("/proc/self/mounts")


def writable_tmpfs(path: Path = RAM_TMP, mounts: Path = MOUNTS, min_free: int = MIN_FREE) -> bool:
    try:
        lines = mounts.read_text().splitlines()
        if not any(line.split()[1:3] == [str(path), "tmpfs"] for line in lines):
            return False
        return os.access(path, os.W_OK) and shutil.disk_usage(path).free >= min_free
    except OSError:
        return False


def ram_base(path: Path = RAM_TMP, mounts: Path = MOUNTS, min_free: int = MIN_FREE) -> Path | None:
    """A new private directory under `path`, or None when `path` won't do or the directory can't be made."""
    if not writable_tmpfs(path, mounts, min_free):
        return None
    try:
        return Path(tempfile.mkdtemp(prefix="hz", dir=path)).resolve()
    except OSError:
        return None
