"""The lifetime backstop (ADR 0001 §7, D13): delete one sandbox at its deadline, independently of wsd.

wsd starts it in its own tmux server, which runs in its own systemd scope, so it outlives a wsd crash or
restart and acts whether or not the queue, the pickup or reconciliation works. wsd's own stop at a turn
boundary comes first; this only guarantees that nothing runs past the deadline.

A sandbox it finds absent may still appear: wsd starts it before the create, wsd may stall past the
deadline, and a create that failed or timed out may complete later. So absence ends nothing early: wsd kills
it once the session's end is confirmed and its create is known to have returned, and otherwise it watches
until LINGER_SECONDS after the deadline (and, past that, until a deletion is confirmed). Even then it goes
only once the session record says no create is outstanding (`settled`): a wsd that died inside a create leaves
`create_started` without `created`, and the watcher stays until a later wsd ends the record.

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
from heterodyne.sandbox.openshell import OpenShellBackend
from heterodyne.tmux import Tmux, TmuxError

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


def write_once(path: Path, body: bytes) -> bool:
    """Write `path` once, durably: a private temporary file, fsynced, linked to the final name (an existing
    one is kept), then the directory fsynced. A crash leaves at most the temporary file, which is ignored."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
        folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    except OSError:
        return False
    return True


def reap(name: str, deadline: int, delete: Callable[[str], bool], kill: Callable[[str], None], *,
         clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         settled: Callable[[], bool] = lambda: True, mark: Callable[[], object] = lambda: None) -> None:
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
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    parser.add_argument("--pane", required=True)
    parser.add_argument("--tmux", required=True)
    parser.add_argument("--tmux-socket", required=True)
    parser.add_argument("--tmux-socket-path", type=Path)
    parser.add_argument("name")
    args = parser.parse_args(argv)
    backend = OpenShellBackend(args.openshell, args.podman, args.image, dict(os.environ))
    record: Path = args.record
    name: str = args.name
    tmux = Tmux(args.tmux_socket, args.tmux, socket_path=args.tmux_socket_path)
    pane: str = args.pane

    def pane_live() -> bool:
        return tmux.has_session(pane) and not tmux.pane_dead(pane)

    reap(name, args.deadline, backend.delete, backend.kill, settled=lambda: settled(record, name),
         mark=lambda: mark_live(record, args.marker, name, args.generation, backend.names, pane_live))
    return 0


if __name__ == "__main__":
    sys.exit(main())
