"""Shared pytest configuration."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from heterodyne import platform


def pytest_configure(config: pytest.Config) -> None:
    """Keep tmp_path short on macOS.

    admind tests bind real Unix sockets under tmp_path, and macOS limits sun_path to 104 bytes while
    its default pytest base temp lives under /private/var/folders/... (far too long). Use a short
    base under /tmp (resolved to /private/tmp, 12 bytes) unless the user passed --basetemp.
    Worst case: /private/tmp/hzXXXXXXXX (23) + test dir (<= 32) + /state/admind/marmot/ctl/wn-agent.sock
    (38) = 93 bytes, under both 104 and admind's own 100-byte limit. xdist workers inherit the
    controller's basetemp, so only the controller (or a plain run) creates it.
    """
    if platform.detect() != "macos" or config.option.basetemp:
        return
    base = Path(tempfile.mkdtemp(prefix="hz", dir="/tmp")).resolve()
    config.option.basetemp = base
    config.add_cleanup(lambda: shutil.rmtree(base, ignore_errors=True))
