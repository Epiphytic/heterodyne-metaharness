"""Shared pytest configuration."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from heterodyne import platform

# tmux_guard: test tmux servers are cleaned up even if pytest is killed. tier_marks: the `slow` marker.
pytest_plugins = ("tmux_guard", "tier_marks")

RAM_TMP = Path("/dev/shm")    # noqa: S108 - a private mkdtemp under it, removed at exit
DISK_TMP_ENV = "HZ_TEST_DISK_TMP"     # set to 1 to keep pytest's default on-disk base temp
RAM_TMP_MIN_FREE = 1 << 30            # below this, a run could fill RAM: stay on disk


def pytest_configure(config: pytest.Config) -> None:
    """Pick a base temp, unless the user passed --basetemp. xdist workers inherit the controller's
    basetemp, so only the controller (or a plain run) creates it.

    macOS: keep tmp_path short. admind tests bind real Unix sockets under tmp_path, and macOS limits
    sun_path to 104 bytes while its default pytest base temp lives under /private/var/folders/... (far too
    long). Use a short base under /tmp (resolved to /private/tmp, 12 bytes).
    Worst case: /private/tmp/hzXXXXXXXX (23) + test dir (<= 32) + /state/admind/marmot/ctl/wn-agent.sock
    (38) = 93 bytes, under both 104 and admind's own 100-byte limit.

    Linux: use a base under /dev/shm when it is a writable tmpfs with at least 1 GiB free. The journal
    and the admind store commit SQLite in WAL mode, which fsyncs every commit; on a busy disk that is
    most of a wsd test's time (test_wsd_defer.py: 125 s on disk, 18 s on tmpfs). The fsyncs still run;
    they are just cheap. The base is removed at exit, so to keep a failing test's files, pass --basetemp
    (or set HZ_TEST_DISK_TMP=1 for pytest's default, which keeps the last three runs).
    """
    if config.option.basetemp:
        return
    if platform.detect() == "macos":
        base = Path(tempfile.mkdtemp(prefix="hz", dir="/tmp")).resolve()
    elif os.environ.get(DISK_TMP_ENV) != "1" and _writable_tmpfs(RAM_TMP):
        base = Path(tempfile.mkdtemp(prefix="hz", dir=RAM_TMP)).resolve()
    else:
        return
    config.option.basetemp = base
    config.add_cleanup(lambda: shutil.rmtree(base, ignore_errors=True))


def _writable_tmpfs(path: Path) -> bool:
    try:
        mounts = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return False
    if not any(line.split()[1:3] == [str(path), "tmpfs"] for line in mounts) or not os.access(path, os.W_OK):
        return False
    return shutil.disk_usage(path).free >= RAM_TMP_MIN_FREE
