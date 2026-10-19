"""Native-owner adapter: enrolled counter, one native attempt, then a companion notification.

Deployment alone enrolls exact identity and owner bytes. Missing/corrupt counter is a hold, never a
new namespace. Companion failure cannot replace the actual native result or replay its attempt.
"""

import json
import logging
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from heterodyne.agents.base import write_private
from heterodyne.local_alerts import locked
from heterodyne.wsd.backup_cycles import NativeStatus


@dataclass(frozen=True)
class Binding:
    installation: str
    generation: str
    owner_hash: str

    def record(self, cycle: int) -> dict[str, str | int]:
        return {
            "version": 1,
            "installation": self.installation,
            "generation": self.generation,
            "owner_hash": self.owner_hash,
            "cycle": cycle,
        }


def initialize(directory: Path, binding: Binding) -> None:
    """Explicit first-install operation, not called by ordinary native cycles."""
    with locked(directory):
        path = directory / "native-backup-cycle.state"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        write_private(path, json.dumps(binding.record(0), separators=(",", ":")))


def _next_cycle(directory: Path, binding: Binding) -> int:
    with locked(directory):
        path = directory / "native-backup-cycle.state"
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_size > 1024
            ):
                raise ValueError("native backup counter is not private and bounded")
            raw = json.loads(os.read(fd, 1025))
        finally:
            os.close(fd)
        cycle = raw.get("cycle")
        if type(cycle) is not int or not 0 <= cycle < (1 << 64) - 1 or raw != binding.record(cycle):
            raise ValueError("native backup counter identity or sequence")
        cycle += 1
        write_private(path, json.dumps(binding.record(cycle), separators=(",", ":")))
        return cycle


def paired_attempt(
    directory: Path,
    binding: Binding,
    native: Callable[[], NativeStatus],
    companion: Callable[[int, NativeStatus], object],
) -> tuple[int | None, NativeStatus]:
    """Called inside the actual native owner's existing exclusive cycle ownership.

    Counter/companion failures hold pairing only. None is an explicit unpaired native cycle,
    never a replacement namespace or an invented sequence.
    """
    cycle: int | None = None
    try:
        cycle = _next_cycle(directory, binding)
    except (OSError, ValueError, TypeError, AttributeError, TimeoutError):
        logging.getLogger(__name__).warning(
            "paired backup counter unavailable; native cycle remains independent")
    def notify(status: NativeStatus) -> None:
        if cycle is not None:
            try:
                companion(cycle, status)
            except Exception:
                logging.getLogger(__name__).warning(
                    "paired companion unavailable; native result retained")

    try:
        status = native()
    except Exception:
        notify("failed")
        raise
    # BaseException (including shutdown) propagates without beginning companion IPC.
    if status not in ("succeeded", "failed", "unknown"):
        status = "unknown"
    notify(status)
    return cycle, status
