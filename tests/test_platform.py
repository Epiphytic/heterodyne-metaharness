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
