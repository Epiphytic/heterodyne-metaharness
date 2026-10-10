"""Platform seam (ADR 0001 §3.2). The only module allowed to read sys.platform."""

import socket
import struct
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


_SO_PEERCRED = getattr(socket, "SO_PEERCRED", 17)   # Linux; the value differs on a few architectures
_SOL_LOCAL, _LOCAL_PEERPID = 0, 2                   # macOS <sys/un.h>


def peer_pid(sock: socket.socket | None, platform: str = sys.platform) -> int | None:
    """The PID of the process at the other end of a connected Unix socket: SO_PEERCRED on Linux,
    LOCAL_PEERPID on macOS, else (or on any failure) None. For the audit only, never for a decision."""
    if sock is None:
        return None
    try:
        if platform.startswith("linux"):
            return int(struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED,
                                                           struct.calcsize("3i")))[0])
        if platform == "darwin":
            return int(struct.unpack("i", sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID,
                                                          struct.calcsize("i")))[0])
    except (OSError, struct.error):
        return None
    return None


def peer_pid_checked(sock: socket.socket) -> int:
    """The host PID of the process at the other end of a connected Unix socket, by SO_PEERCRED, for a
    decision (the agent-path probe channel). Raises OSError on any failure and off Linux: there is no
    fallback, and the caller treats a failure as an unverified peer."""
    if not sys.platform.startswith("linux"):
        raise OSError("peer credentials need Linux")
    try:
        cred = sock.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
        pid = int(struct.unpack("3i", cred)[0])
    except struct.error:
        raise OSError("peer credentials unreadable") from None
    if pid <= 0:
        raise OSError("no peer process")
    return pid
