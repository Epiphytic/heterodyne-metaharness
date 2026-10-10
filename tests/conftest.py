"""Shared pytest configuration."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from basetemp import ram_base

from heterodyne import platform

# tmux_guard: test tmux servers are cleaned up even if pytest is killed.
# tier_marks: the `slow` and `safety` markers.
pytest_plugins = ("tmux_guard", "tier_marks")

DISK_TMP_ENV = "HZ_TEST_DISK_TMP"     # set to 1 to keep pytest's default on-disk base temp


def pytest_configure(config: pytest.Config) -> None:
    """Pick a base temp, unless the user passed --basetemp. xdist workers inherit the controller's
    basetemp, so only the controller (or a plain run) creates it.

    macOS: keep tmp_path short. admind tests bind real Unix sockets under tmp_path, and macOS limits
    sun_path to 104 bytes while its default pytest base temp lives under /private/var/folders/... (far too
    long). Use a short base under /tmp (resolved to /private/tmp, 12 bytes).
    Worst case: /private/tmp/hzXXXXXXXX (23) + test dir (<= 32) + /state/admind/marmot/ctl/wn-agent.sock
    (38) = 93 bytes, under both 104 and admind's own 100-byte limit.

    Linux: use a base under /dev/shm when it is a writable tmpfs with at least 1 GiB free (basetemp.py).
    The journal and the admind store commit SQLite in WAL mode, which fsyncs every commit; on a busy disk
    that is most of a wsd test's time (test_wsd_defer.py: 125 s on disk, 18 s on tmpfs). The fsyncs still run;
    they are just cheap. The base is removed at exit, so to keep a failing test's files, pass --basetemp
    (or set HZ_TEST_DISK_TMP=1 for pytest's default, which keeps the last three runs).
    """
    if config.option.basetemp:
        return
    if platform.detect() == "macos":
        base = Path(tempfile.mkdtemp(prefix="hz", dir="/tmp")).resolve()
    else:
        ram = None if os.environ.get(DISK_TMP_ENV) == "1" else ram_base()
        if ram is None:
            return
        base = ram
    config.option.basetemp = base
    config.add_cleanup(lambda: shutil.rmtree(base, ignore_errors=True))

