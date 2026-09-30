"""Platform seam (ADR 0001 §3.2). The only module allowed to read sys.platform."""

import subprocess
import sys
from pathlib import Path
from typing import Literal

OsName = Literal["linux", "macos"]

_BACKENDS: dict[OsName, dict[str, str]] = {
    "linux": {"service_manager": "systemd", "sandbox": "bubblewrap"},
    "macos": {"service_manager": "launchd", "sandbox": "seatbelt"},
}


class UnsupportedPlatform(RuntimeError):
    pass


def detect(platform: str = sys.platform) -> OsName:
    if platform.startswith("linux"):
        return "linux"
    if platform == "darwin":
        return "macos"
    raise UnsupportedPlatform(f"{platform} is not supported (Linux and macOS only)")


def backends(os_name: OsName) -> dict[str, str]:
    return dict(_BACKENDS[os_name])


def boot_id(os_name: OsName) -> str:
    if os_name == "linux":
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                          check=True).stdout.strip()
