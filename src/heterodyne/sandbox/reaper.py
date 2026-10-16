"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline.

Create outcomes live in the host-only create ledger, outside replaceable session records.
An unsettled create is watched indefinitely and escalates after 72 hours. A valid settlement
must precede confirmed deletion and durable gone evidence before the watcher can exit.

g08sd: wsd can't promise to stop a healthy session first (a long job or a stale clock can carry its pickup
past the deadline), so at its first pass the reaper decides, once and before its first kill, whether it
found the generation live, and if so records that in a marker wsd reads as a lifetime end (`mark_live`).
Anything it can't read means no marker, and the kill goes ahead regardless."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from heterodyne.sandbox.backend import BackendError, BackendUnavailable
from heterodyne.sandbox.creates import Ledger, settled, write_once
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.tmux import Tmux, TmuxError

POLL_SECONDS = 30
RETRY_SECONDS = 5
# Settled creates retain the existing linger. Unknown creates escalate here and continue
# indefinitely. This is not a bound on how late a create may land.
LINGER_SECONDS = 72 * 3600


READ_ERRORS = (OSError, ValueError, BackendUnavailable, BackendError, TmuxError, subprocess.TimeoutExpired)


def mark_live(record: Path, marker: Path, name: str, generation: int, listed: Callable[[], set[str]],
              pane_live: Callable[[], bool], clock: Callable[[], float] = time.time) -> bool:
    """g08sd: record that this generation was live at its deadline, only if its record names it running
    (started, no end cause yet), the backend lists its sandbox and its pane lives. True once the marker is
    durable. Every failure to read or write is no marker: the generation then counts as a death."""
    try:
        data = json.loads(record.read_bytes())
        if not isinstance(data, dict):
            return False
        fields: dict[str, object] = data  # pyright: ignore[reportUnknownVariableType]
        started = fields.get("started_at")
        if (fields.get("sandbox") != name or fields.get("generation") != generation
                or fields.get("phase") != "running" or not isinstance(started, int) or started <= 0
                or fields.get("end_cause")):
            return False
        if name not in listed() or not pane_live():
            return False
    except READ_ERRORS:
        return False
    body = json.dumps({"sandbox": name, "generation": generation, "at": int(clock())}).encode()
    return write_once(marker, body)


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         settled: Callable[[], bool] = lambda: True, mark: Callable[[], object] = lambda: None,
         ledger: Ledger | None = None, listed: Callable[[], set[str]] = set) -> None:
    """Wait for the deadline, then kill the sandbox's containers locally, which needs no OpenShell control
    service, and ask the backend to delete it: every RETRY_SECONDS until a deletion is confirmed, then every
    POLL_SECONDS. It returns only once a deletion is confirmed LINGER_SECONDS or more after the deadline and
    `settled()` says no create is outstanding, so an outage of any length ends with the workload killed
    meanwhile and the sandbox deleted after. `mark()` runs once, at the first pass, before the first kill
    (g08sd); it never raises (`mark_live`)."""
    while (left := deadline - clock()) > 0:
        sleep(min(POLL_SECONDS, left))
    mark()
    while True:
        # The settlement must predate the confirmed delete used to write gone.
        was_settled = settled()
        if ledger is not None:
            try:
                if name in listed():
                    ledger.observe(name, "watcher", int(clock()))
            except (BackendUnavailable, BackendError, OSError):
                pass
        with contextlib.suppress(BackendUnavailable, BackendError):
            kill(name)
        try:
            gone = delete(name)
        except (BackendUnavailable, BackendError):
            gone = False
        if clock() >= deadline + LINGER_SECONDS:
            if not was_settled and ledger is not None:
                with contextlib.suppress(OSError):
                    ledger.alert(name, int(clock()))
            if gone and was_settled:
                if ledger is None:
                    return
                if ledger.put(name, "gone", by="watcher", at=int(clock())):
                    if ledger.close_notice(name, int(clock())):
                        return
        sleep(POLL_SECONDS if gone else RETRY_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="heterodyne-reaper")
    parser.add_argument("--deadline", type=int, required=True)
    parser.add_argument("--openshell", required=True)
    parser.add_argument("--podman", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--pane", required=True)
    parser.add_argument("--tmux", required=True)
    parser.add_argument("--tmux-socket", required=True)
    parser.add_argument("--tmux-socket-path", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--alerts", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("name")
    args = parser.parse_args(argv)
    backend = OpenShellBackend(args.openshell, args.podman, args.image, dict(os.environ))
    record: Path = args.record
    name: str = args.name
    tmux = Tmux(args.tmux_socket, args.tmux, socket_path=args.tmux_socket_path)
    pane: str = args.pane
    ledger = Ledger(args.ledger, args.alerts)
    # Opening/enumerating the ledger is part of startup, before readiness.
    ledger.names()
    if args.ready.parent != ledger.directory or not args.ready.name.startswith(f".ready-{name}-"):
        parser.error("ready must be a unique file in the ledger")
    if not write_once(args.ready, b"ready"):
        return 1

    def pane_live() -> bool:
        return tmux.has_session(pane) and not tmux.pane_dead(pane)

    reap(name, args.deadline, backend.delete, backend.kill, settled=lambda: settled(ledger.directory, name),
         mark=lambda: mark_live(record, args.marker, name, args.generation, backend.names, pane_live),
         ledger=ledger, listed=backend.names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
