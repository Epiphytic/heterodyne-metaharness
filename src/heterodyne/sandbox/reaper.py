"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline.

A sandbox it finds absent may still appear: wsd starts it before the create, wsd may stall past the
deadline, and a create that failed or timed out may complete later. So absence ends nothing early: wsd kills
it once the session's end is confirmed and its create is known to have returned, and otherwise it watches
until LINGER_SECONDS after the deadline (and, past that, until a deletion is confirmed)."""

import argparse
import contextlib
import os
import sys
import time
from collections.abc import Callable

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend

POLL_SECONDS = 30
RETRY_SECONDS = 5
# Assumed far beyond any create that could still land (OpenShell's own create timeout is minutes) and past
# the day-long gateway outage the backstop is tested through: a watcher retained for an inconclusive
# create can't outlive it, so leaked watchers don't pile up.
LINGER_SECONDS = 72 * 3600


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
    """Wait for the deadline, then kill the sandbox's containers locally, which needs no OpenShell control
    service, and ask the backend to delete it: every RETRY_SECONDS until a deletion is confirmed, then every
    POLL_SECONDS. It returns only once a deletion is confirmed LINGER_SECONDS or more after the deadline,
    so an outage of any length ends with the workload killed meanwhile and the sandbox deleted after."""
    while (left := deadline - clock()) > 0:
        sleep(min(POLL_SECONDS, left))
    while True:
        with contextlib.suppress(BackendUnavailable, BackendError):
            kill(name)
        try:
            gone = delete(name)
        except (BackendUnavailable, BackendError):
            gone = False
        if gone and clock() >= deadline + LINGER_SECONDS:
            return
        sleep(POLL_SECONDS if gone else RETRY_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heterodyne-reaper")
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--openshell", required=True)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("name")
    args = parser.parse_args(argv)
    backend = OpenShellBackend(args.openshell, args.podman, args.image, dict(os.environ))
    reap(args.name, args.deadline, backend.delete, backend.kill)
    return 0


if __name__ == "__main__":
    sys.exit(main())
