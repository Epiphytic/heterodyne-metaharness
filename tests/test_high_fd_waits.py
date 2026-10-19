"""Readable pipes above FD_SETSIZE retain byte, EOF and timeout semantics."""

import fcntl
import os
import resource
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Never

import pytest
import tmux_guard
from tmux_guard import _read_ack

from heterodyne.harness.fetch import DEADLINE, Fetcher, FetchRefused


def unused_opener(url: str, *, timeout: float) -> Never:
    raise AssertionError("stream tests must not open network resources")


@pytest.fixture
def high_pipe() -> Iterator[tuple[int, BinaryIO]]:
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 2048:
        pytest.skip("high-descriptor regression requires a larger descriptor limit")
    read, write = os.pipe()
    try:
        high = fcntl.fcntl(read, fcntl.F_DUPFD_CLOEXEC, 2048)
    except BaseException:
        os.close(write)
        raise
    finally:
        os.close(read)
    try:
        with os.fdopen(write, "wb", buffering=0) as writer:
            yield high, writer
    finally:
        os.close(high)


def test_watchdog_ack_accepts_high_descriptor(high_pipe: tuple[int, BinaryIO]) -> None:
    read, write = high_pipe
    write.write(b"ready")
    assert _read_ack(read, 0.1) == b"ready"


def test_watchdog_ack_high_descriptor_timeout(
    high_pipe: tuple[int, BinaryIO], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux_guard, "time", SimpleNamespace(monotonic=lambda: 0.0))
    read, _ = high_pipe
    assert _read_ack(read, 0.01) == b""


def test_watchdog_ack_high_descriptor_eof(high_pipe: tuple[int, BinaryIO]) -> None:
    read, write = high_pipe
    write.close()
    assert _read_ack(read, 0.1) == b""


def test_fetch_stream_accepts_high_descriptor_and_eof(
    high_pipe: tuple[int, BinaryIO], tmp_path: Path,
) -> None:
    read, write = high_pipe
    write.write(b"archive bytes")
    write.close()
    fetcher = Fetcher(unused_opener, tmp_path, lambda: 0.0, 1)
    with (tmp_path / "output").open("w+b") as output:
        fetcher._stream(read, output.fileno(), 1)
        output.seek(0)
        assert output.read() == b"archive bytes"


def test_fetch_stream_high_descriptor_deadline(
    high_pipe: tuple[int, BinaryIO], tmp_path: Path,
) -> None:
    read, _ = high_pipe
    fetcher = Fetcher(unused_opener, tmp_path, lambda: 0.0, 1)
    with (tmp_path / "output").open("wb") as output:
        with pytest.raises(FetchRefused) as error:
            fetcher._stream(read, output.fileno(), 0.01)
    assert error.value.constant == DEADLINE
