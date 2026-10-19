"""Real private hook listeners remain observable above select's FD_SETSIZE."""

import asyncio
import fcntl
import resource
import socket
from pathlib import Path

import pytest

from heterodyne.admind.audit import Audit
from heterodyne.admind.hook import HookServer


@pytest.mark.parametrize("minimum_fd", [1024, 1100])
def test_high_descriptor_backlog_tracks_empty_pending_and_drained(
    tmp_path: Path,
    minimum_fd: int,
) -> None:
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= minimum_fd:
        pytest.skip("high-descriptor regression requires a larger descriptor limit")
    path = tmp_path / "hook.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as original:
        original.bind(str(path))
        original.listen(2)
        duplicate = fcntl.fcntl(original.fileno(), fcntl.F_DUPFD_CLOEXEC, minimum_fd)
    # One duplicated FD exercises the real numeric boundary without opening 1,100 files.
    with socket.socket(fileno=duplicate) as listener:
        assert listener.fileno() >= minimum_fd
        server = HookServer(path, asyncio.Queue(), Audit(tmp_path / "audit.jsonl"))
        server._listen = listener  # pyright: ignore[reportPrivateUsage]
        assert not server.backlog_waiting()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(path))
            assert server.backlog_waiting()
            accepted, _ = listener.accept()
            accepted.close()
            assert not server.backlog_waiting()


def test_uninspectable_listener_keeps_dispatch_blocked(tmp_path: Path) -> None:
    server = HookServer(tmp_path / "hook.sock", asyncio.Queue(), Audit(tmp_path / "audit.jsonl"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        server._listen = listener  # pyright: ignore[reportPrivateUsage]
    assert server.backlog_waiting()
