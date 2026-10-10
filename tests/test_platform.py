import os
import socket as _socket
import subprocess
import sys
from pathlib import Path

import pytest

from heterodyne import platform
from heterodyne.platform import peer_pid_checked, peer_pidfd_checked, pidfd_pid


def test_detect_maps_supported_platforms() -> None:
    assert platform.detect("linux") == "linux"
    assert platform.detect("darwin") == "macos"


def test_detect_rejects_unsupported() -> None:
    with pytest.raises(platform.UnsupportedPlatform):
        platform.detect("win32")


def test_backends_per_platform() -> None:
    assert platform.backends("linux") == {"service_manager": "systemd", "sandbox": "bubblewrap"}
    assert platform.backends("macos") == {"service_manager": "launchd", "sandbox": "seatbelt"}


def test_boot_id_is_stable_within_a_boot() -> None:
    os_name = platform.detect()
    assert platform.boot_id(os_name) == platform.boot_id(os_name) != ""


def test_boot_id_macos_uses_sysctl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="{ sec = 1700000000, usec = 1 } Tue\n", stderr="")

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    assert platform.boot_id("macos") == "{ sec = 1700000000, usec = 1 } Tue"
    assert [cmd for cmd, _ in calls] == [["sysctl", "-n", "kern.boottime"]]
    assert calls[0][1].get("check") is True


def test_boot_id_macos_sysctl_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        platform.boot_id("macos")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SO_PEERCRED is Linux-only")
def test_peer_pid_checked_reads_so_peercred() -> None:
    a, b = _socket.socketpair(_socket.AF_UNIX)
    with a, b:
        assert peer_pid_checked(a) == os.getpid()


def test_peer_pid_checked_raises_on_a_closed_socket() -> None:
    a, b = _socket.socketpair(_socket.AF_UNIX)
    b.close()
    a.close()
    with pytest.raises(OSError):
        peer_pid_checked(a)


linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="pidfds are Linux-only")


@linux_only
def test_peer_pidfd_checked_pins_the_connecting_process() -> None:
    a, b = _socket.socketpair(_socket.AF_UNIX)
    with a, b:
        pid, pidfd = peer_pidfd_checked(a)
        try:
            assert pid == os.getpid() and pidfd_pid(pidfd) == os.getpid()
        finally:
            os.close(pidfd)


@linux_only
def test_peer_pidfd_checked_refuses_a_peer_that_has_exited(tmp_path: Path) -> None:
    """The peer connects and exits before the host looks: its pidfd shows it gone (Pid: -1)."""
    path = tmp_path / "s"
    with _socket.socket(_socket.AF_UNIX) as srv:
        srv.bind(str(path))
        srv.listen(1)
        client = "import socket, sys; s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])"
        subprocess.run([sys.executable, "-I", "-c", client, str(path)], check=True, timeout=30)
        conn, _ = srv.accept()
        with conn, pytest.raises(OSError):
            peer_pidfd_checked(conn)


@linux_only
def test_peer_pidfd_checked_refuses_disagreeing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "peer_pid_checked", lambda s: os.getpid() + 1)
    before = len(os.listdir("/proc/self/fd"))  # noqa: PTH208
    a, b = _socket.socketpair(_socket.AF_UNIX)
    with a, b, pytest.raises(OSError, match="disagree"):
        peer_pidfd_checked(a)
    assert len(os.listdir("/proc/self/fd")) == before  # noqa: PTH208 - the pidfd was closed


def test_peer_pidfd_checked_refuses_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    a, b = _socket.socketpair(_socket.AF_UNIX)
    with a, b, pytest.raises(OSError):
        peer_pidfd_checked(a)


@pytest.mark.parametrize("machine, ok", [("x86_64", True), ("aarch64", True), ("mips64", False),
                                          ("parisc64", False), ("sparc64", False)])
def test_so_peerpidfd_falls_back_to_77_only_on_generic_architectures(
        monkeypatch: pytest.MonkeyPatch, machine: str, ok: bool) -> None:
    monkeypatch.delattr(_socket, "SO_PEERPIDFD", raising=False)
    monkeypatch.setattr(platform.os, "uname", lambda: os.uname_result(("Linux", "h", "r", "v", machine)))
    if ok:
        assert platform._so_peerpidfd() == 77  # noqa: SLF001
    else:
        with pytest.raises(OSError):
            platform._so_peerpidfd()  # noqa: SLF001


@pytest.mark.parametrize("text", ["pos:\t0\nPid:\t-1\n", "pos:\t0\n", "pos:\t0\nPid:\tx\n"])
def test_pidfd_pid_refuses_a_gone_or_unreadable_pidfd(tmp_path: Path, text: str) -> None:
    (tmp_path / "9").write_text(text)
    with pytest.raises(OSError):
        pidfd_pid(9, fdinfo=tmp_path)
