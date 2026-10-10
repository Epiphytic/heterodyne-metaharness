"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline."""

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


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
    """Wait for the deadline, then, until the backend confirms the sandbox is deleted: kill its containers
    locally, which needs no OpenShell control service, and ask the backend to delete it. It never gives up,
    so an outage of any length ends with the workload killed meanwhile and the sandbox deleted after."""
    while (left := deadline - clock()) > 0:
        sleep(min(POLL_SECONDS, left))
    while True:
        with contextlib.suppress(BackendUnavailable, BackendError):
            kill(name)
        try:
            if delete(name):
                return
        except (BackendUnavailable, BackendError):
            pass                             # retried: only a confirmed deletion ends the backstop
        sleep(RETRY_SECONDS)


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
