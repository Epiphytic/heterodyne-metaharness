"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline.

It never exits on its own: wsd starts it before the sandbox is created, so a sandbox it finds absent may
still appear (wsd stalled past the deadline, or a create completed late). wsd kills it once the session's
end is confirmed."""

import argparse
import contextlib
import os
import time
from collections.abc import Callable
from typing import NoReturn

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.openshell import OpenShellBackend

POLL_SECONDS = 30
RETRY_SECONDS = 5


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> NoReturn:
    """Wait for the deadline, then kill the sandbox's containers locally, which needs no OpenShell control
    service, and ask the backend to delete it: every RETRY_SECONDS until a deletion is confirmed, then every
    POLL_SECONDS. It never gives up, so an outage of any length ends with the workload killed meanwhile and
    the sandbox deleted after; and a confirmed absence is no reason to stop watching."""
    while (left := deadline - clock()) > 0:
        sleep(min(POLL_SECONDS, left))
    while True:
        with contextlib.suppress(BackendUnavailable, BackendError):
            kill(name)
        try:
            gone = delete(name)
        except (BackendUnavailable, BackendError):
            gone = False
        sleep(POLL_SECONDS if gone else RETRY_SECONDS)


def main(argv: list[str] | None = None) -> NoReturn:
    parser = argparse.ArgumentParser(prog="heterodyne-reaper")
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--openshell", required=True)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("name")
    args = parser.parse_args(argv)
    backend = OpenShellBackend(args.openshell, args.podman, args.image, dict(os.environ))
    reap(args.name, args.deadline, backend.delete, backend.kill)


if __name__ == "__main__":
    main()
