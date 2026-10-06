"""`wsd` (run, tick) and `wsctl` (pause, resume, status) command lines (ADR 0001 §4.3, §9, §16).

Every command except `wsd run` asks the running wsd over its control socket. `wsctl pause` goes through
wsd as §4.3 requires: wsd sets btq's shared pause flag under the workstream's claim lock, so a pause is
acknowledged only once no claim can start. With wsd stopped nothing claims, and nothing is acknowledged.

A journal locked by another process (JournalBusy) at startup ends `wsd run` with exit 1, not EX_CONFIG:
nothing was changed, so the unit's restart retries safely. Something other than a socket at the control
socket's path ends it with EX_CONFIG, after the startup recovery and pickup, and after shutting down as
on SIGTERM: a restart would only meet it again, and wsd never removes what it did not create.

`run` closes the journal and then releases the instance lock, in that order, on every path, once the
daemon has drained its jobs. If jobs are still running when it gives up waiting (Undrained), it ends the
process at once with exit 1 (`os._exit`, after flushing its message): a thread can't be stopped, and
returning would leave it able to write to the journal with no lock held, or keep the process alive
indefinitely. The kernel releases the lock with the process. To the journal that is a crash, which
recovery replays like any other.
"""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Callable, Sequence

from heterodyne.config import ConfigError
from heterodyne.wsd import btq, ctl
from heterodyne.wsd.daemon import Undrained, Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.journal import Journal, JournalBusy, JournalCorrupt
from heterodyne.wsd.runtime import AgentRuntime, NoRuntime
from heterodyne.wsd.settings import WsdSettings, resolve

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it


def _settings() -> WsdSettings:
    return resolve(os.environ)


def _factory(s: WsdSettings) -> btq.QueueFactory:
    return btq.factory(btq.load(s.btq_checkout), s.btq_locations)


def run(s: WsdSettings, factory: btq.QueueFactory, runtime: AgentRuntime,
        exit_now: Callable[[int], object] = os._exit) -> int:
    try:
        lock = instance_lock(s.instance_lock)
    except AlreadyRunning:
        print("wsd: another wsd is already running on this state directory", file=sys.stderr)
        return 1
    held = False        # jobs outlived shutdown: keep the journal and the lock until the process exits
    try:
        try:
            journal = Journal(s.journal)
        except JournalCorrupt as exc:
            print(f"wsd: the journal failed its check ({exc}); it was left in place for inspection. "
                  "Move it aside to start from beads alone.", file=sys.stderr)
            return EX_CONFIG
        try:
            if journal.fresh:
                journal.emit("-", None, "journal_created")
            daemon = Wsd(s, assemble(s, journal, factory, runtime))
            asyncio.run(_serve(daemon))
            return 0
        except ctl.SocketPathTaken as exc:
            print(f"wsd: the control socket can't be created ({exc}); move it aside and restart",
                  file=sys.stderr)
            return EX_CONFIG
        except Undrained as exc:
            held = True
            try:
                print(f"wsd: {exc}; exiting now, as a crash would (recovery replays the journal)",
                      file=sys.stderr)
                sys.stderr.flush()
                sys.stdout.flush()
            finally:
                exit_now(1)     # even if stderr is gone (a closed pipe): nothing may keep the process up
            return 1        # only a test's exit_now returns
        finally:
            if not held:
                journal.close()
    except JournalBusy:
        print("wsd: the journal is locked by another process; retrying is safe", file=sys.stderr)
        return 1
    finally:
        if not held:
            os.close(lock)


async def _serve(daemon: Wsd) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await daemon.serve(stop)


def _ask(s: WsdSettings, req: ctl.CtlRequest) -> ctl.CtlReply | None:
    try:
        return asyncio.run(ctl.request(s.socket, req))
    except ctl.CtlUnavailable:
        print("wsd is not running (no answer on its control socket)", file=sys.stderr)
        return None


def _print(reply: ctl.CtlReply) -> int:
    print(reply.message)
    for name, fields in sorted(reply.data.items()):
        print(name + ": " + " ".join(f"{k}={v}" for k, v in fields.items() if v))
    return 0 if reply.result == "ok" else 1


def wsd_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the workstream daemon")
    tick = sub.add_parser("tick", help="ask the running wsd to run a job now (timers call this)")
    tick.add_argument("job", choices=["pickup", "reconcile"])
    tick.add_argument("--ws", default=None)
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "run":
            return run(s, _factory(s), NoRuntime())
    except ConfigError as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    except btq.BtqUnavailable as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    reply = _ask(s, ctl.CtlRequest("tick", job=args.job, ws=args.ws))
    return 1 if reply is None else _print(reply)


def wsctl_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsctl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} new claims for a workstream").add_argument("ws")
    status = sub.add_parser("status", help="show what wsd is doing")
    status.add_argument("ws", nargs="?")
    status.add_argument("--all", action="store_true", help="list closed and dropped beads too")
    args = parser.parse_args(argv)
    try:
        s = _settings()
        if args.cmd == "status":
            reply = _ask(s, ctl.CtlRequest("status", ws=args.ws, all=args.all))
            return 1 if reply is None else _print(reply)
        reply = _ask(s, ctl.CtlRequest(args.cmd, ws=args.ws))
        return 1 if reply is None else _print(reply)
    except ConfigError as exc:
        print(f"wsctl: {exc}", file=sys.stderr)
        return EX_CONFIG
