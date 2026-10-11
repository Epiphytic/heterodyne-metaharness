"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline.

A sandbox it finds absent may still appear: wsd starts it before the create, wsd may stall past the
deadline, and a create that failed or timed out may complete later. So absence ends nothing early: wsd kills
it once the session's end is confirmed and its create is known to have returned, and otherwise it watches
until LINGER_SECONDS after the deadline (and, past that, until a deletion is confirmed). Even then it goes
only once the session record says no create is outstanding (`settled`): a wsd that died inside a create leaves
`create_started` without `created`, and the watcher stays until a later wsd ends the record."""

import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend

POLL_SECONDS = 30
RETRY_SECONDS = 5
# Assumed far beyond any create that could still land (OpenShell's own create timeout is minutes) and past
# the day-long gateway outage the backstop is tested through: a watcher retained for an inconclusive
# create can't outlive it, so leaked watchers don't pile up.
LINGER_SECONDS = 72 * 3600


def settled(record: Path, name: str) -> bool:
    """Whether the session record shows no create of `name` outstanding: it returned (`created`), was never
    submitted (no `create_started`), the session ended, or a later generation replaced the record (which
    needs this one ended). A record removed by an operator after the end settles it too. Unreadable
    evidence never does."""
    try:
        data = json.loads(record.read_bytes())
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    fields: dict[str, object] = data  # pyright: ignore[reportUnknownVariableType]
    if fields.get("sandbox") != name:
        return True
    return fields.get("created") is True or fields.get("create_started") is not True or \
        fields.get("phase") == "ended"


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         settled: Callable[[], bool] = lambda: True) -> None:
    """Wait for the deadline, then kill the sandbox's containers locally, which needs no OpenShell control
    service, and ask the backend to delete it: every RETRY_SECONDS until a deletion is confirmed, then every
    POLL_SECONDS. It returns only once a deletion is confirmed LINGER_SECONDS or more after the deadline and
    `settled()` says no create is outstanding, so an outage of any length ends with the workload killed
    meanwhile and the sandbox deleted after."""
    while (left := deadline - clock()) > 0:
        sleep(min(POLL_SECONDS, left))
    while True:
        with contextlib.suppress(BackendUnavailable, BackendError):
            kill(name)
        try:
            gone = delete(name)
        except (BackendUnavailable, BackendError):
            gone = False
        if gone and clock() >= deadline + LINGER_SECONDS and settled():
            return
        sleep(POLL_SECONDS if gone else RETRY_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heterodyne-reaper")
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--openshell", required=True)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("name")
    args = parser.parse_args(argv)
    backend = OpenShellBackend(args.openshell, args.podman, args.image, dict(os.environ))
    record: Path = args.record
    name: str = args.name
    reap(name, args.deadline, backend.delete, backend.kill, settled=lambda: settled(record, name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
