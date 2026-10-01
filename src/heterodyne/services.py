"""Service-manager control (ADR 0001 §3.2). The backend comes from host config, never sys.platform.

v1 is Linux-only (§12), so only systemd user units are implemented. launchd is phase 2.
"""

import re
import subprocess
from dataclasses import dataclass
from typing import Protocol

from heterodyne.config.errors import ConfigError

# A unit name that can't be read as an option, a path or a glob.
UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]*\.(?:service|target|timer|socket)")


@dataclass(frozen=True)
class UnitStatus:
    unit: str
    active: str
    sub: str
    since: str

    def line(self) -> str:
        text = f"{self.unit}: {self.active} ({self.sub})"
        return f"{text} since {self.since}" if self.since else text


class ServiceManager(Protocol):
    def restart(self, unit: str) -> tuple[bool, str]: ...
    def status(self, unit: str) -> UnitStatus: ...


def _check(unit: str) -> None:
    if not UNIT_NAME.fullmatch(unit):
        raise ValueError(f"not a unit name: {unit!r}")


class Systemd:
    def __init__(self, binary: str = "systemctl") -> None:
        self.binary = binary

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.binary, "--user", *args], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=120, check=False)

    def restart(self, unit: str) -> tuple[bool, str]:
        _check(unit)
        proc = self._run("restart", "--", unit)
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:500]

    def status(self, unit: str) -> UnitStatus:
        _check(unit)
        proc = self._run("show", "--property=ActiveState,SubState,ActiveEnterTimestamp", "--", unit)
        fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        return UnitStatus(unit, fields.get("ActiveState", "unknown"), fields.get("SubState", "unknown"),
                          fields.get("ActiveEnterTimestamp", ""))


def for_backend(name: str) -> ServiceManager:
    if name == "systemd":
        return Systemd()
    raise ConfigError(f"service manager {name!r} is not supported yet; v1 supports systemd only "
                      "(launchd is phase 2)")
