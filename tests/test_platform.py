import subprocess

import pytest

from heterodyne import platform


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
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{ sec = 1700000000, usec = 1 } Tue\n", stderr="")

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    assert platform.boot_id("macos") == "{ sec = 1700000000, usec = 1 } Tue"
    assert calls == [["sysctl", "-n", "kern.boottime"]]


def test_boot_id_macos_sysctl_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(platform.subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        platform.boot_id("macos")
