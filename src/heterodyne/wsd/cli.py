"""`wsd` (run, tick) and `wsctl` (pause, resume, status, reload, unlanded, resolve, harness) command lines
(ADR 0001 §4.3, §9, §16).

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

A version 1 journal (plan 3's) is upgraded in place before anything else uses it (`upgrade.run`, AU-3 §3).
If the beads can't be read for it, the file is left untouched and `wsd run` exits 1: retrying is safe. A
version 2 journal (AU-3's) gets plan 4c's install pin columns the same way (`upgrade.run_2_to_3`), and a
version 3 one plan 4c T10's install tables (`upgrade.run_3_to_4`).

`wsctl harness` manages the installs (plan 4c T10). Its `remove` and `prune` take every workstream's
operation lock (the schedulers' parker locks, in name order) before they check what is in use.
"""

import argparse
import asyncio
import os
import signal
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from functools import partial
from typing import Any

from heterodyne.config import ConfigError
from heterodyne.sandbox import hostread
from heterodyne.sandbox.build import build_harness, build_runtime, build_usage_reader
from heterodyne.wsd import btq, ctl, upgrade
from heterodyne.wsd.beads import BeadsAdapter
from heterodyne.wsd.daemon import Undrained, Wsd, assemble
from heterodyne.wsd.gate import AlreadyRunning, instance_lock
from heterodyne.wsd.journal import (
    V2_VERSION,
    V3_VERSION,
    Journal,
    JournalBusy,
    JournalCorrupt,
    JournalNeedsUpgrade,
)
from heterodyne.wsd.runtime import AgentRuntime
from heterodyne.wsd.settings import WsdSettings, resolve
from heterodyne.wsd.usage import UsageReader
from heterodyne.wsd.workstream import HarnessHooks

Locks = Callable[[], Sequence[AbstractContextManager[Any]]]
HarnessBuilder = Callable[[Journal, Locks], HarnessHooks | None]

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it


def _settings() -> WsdSettings:
    return resolve(os.environ)


def _factory(s: WsdSettings) -> btq.QueueFactory:
    return btq.factory(btq.load(s.btq_checkout), s.btq_locations)


def run(s: WsdSettings, factory: btq.QueueFactory, runtime: AgentRuntime,
        exit_now: Callable[[int], object] = os._exit, *, usage_reader: UsageReader | None = None,
        harness: HarnessBuilder | None = None) -> int:
    try:
        lock = instance_lock(s.instance_lock)
    except AlreadyRunning:
        print("wsd: another wsd is already running on this state directory", file=sys.stderr)
        return 1
    held = False        # jobs outlived shutdown: keep the journal and the lock until the process exits
    try:
        hostread.recover(s.state_dir / "hostread")     # never raises; under the lock, before any read
        try:
            journal = _open(s, factory)
        except upgrade.UpgradeAborted as exc:
            print(f"wsd: {exc}", file=sys.stderr)
            return 1
        except JournalCorrupt as exc:
            print(f"wsd: the journal failed its check ({exc}); it was left in place for inspection. "
                  "Move it aside to start from beads alone.", file=sys.stderr)
            return EX_CONFIG
        try:
            if journal.fresh:
                journal.emit("-", None, "journal_created")
            locks: list[AbstractContextManager[Any]] = []      # every workstream's, once assembled
            hooks = None if harness is None else harness(journal, lambda: tuple(locks))
            parts = assemble(s, journal, factory, runtime, usage_reader=usage_reader, harness=hooks)
            locks.extend(sched.parker.lock for _, sched in sorted(parts.schedulers.items()))
            daemon = Wsd(s, parts)
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


def _open(s: WsdSettings, factory: btq.QueueFactory) -> Journal:
    """The journal, upgraded a version at a time until it opens. Each upgrade either lands its version or
    leaves the file as it was; a version asked for twice is JournalCorrupt, so the loop ends."""
    seen: set[str] = set()
    while True:
        try:
            return Journal(s.journal)
        except JournalNeedsUpgrade as needs:
            if needs.version in seen:
                raise JournalCorrupt("the journal upgrade did not land") from None
            seen.add(needs.version)
            if needs.version == V3_VERSION:
                upgrade.run_3_to_4(s.journal)      # adds empty tables only
            elif needs.version == V2_VERSION:
                upgrade.run_2_to_3(s.journal)      # adds columns only: no bead or account is read
            elif s.accounts is None:
                raise ConfigError("the journal upgrade needs the host's accounts") from None
            else:
                upgrade.run(BeadsAdapter(factory), s.accounts, s.journal)


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


def _print_unlanded(reply: ctl.CtlReply) -> int:
    """One block per session; its subjects one per line, after a label that says they are the agent's."""
    print(reply.message)
    for sid, fields in sorted(reply.data.items()):
        print(f"{sid}:")
        for k, v in fields.items():
            if k != "subjects":
                print(f"  {k}: {v}")
        if fields.get("subjects"):
            print("  subjects (untrusted, written by the agent):")
            for line in fields["subjects"].split("\n"):
                print(f"    {line}")
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
            runtime = build_runtime(s, os.environ)
            factory = _factory(s)
            return run(s, factory, runtime, usage_reader=build_usage_reader(s, os.environ, runtime),
                       harness=partial(build_harness, s, os.environ, factory, runtime))
    except ConfigError as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    except btq.BtqUnavailable as exc:
        print(f"wsd: {exc}", file=sys.stderr)
        return EX_CONFIG
    reply = _ask(s, ctl.CtlRequest("tick", job=args.job, ws=args.ws))
    return 1 if reply is None else _print(reply)


def _harness_parser(harness: argparse.ArgumentParser) -> None:
    """`wsctl harness`: managed installs (plan 4c Task 10)."""
    actions = harness.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="show each install and its state")
    listing.add_argument("adapter", nargs="?")
    install = actions.add_parser("install", help="fetch and stage an install, then ask for its approval")
    install.add_argument("adapter")
    install.add_argument("version", nargs="?", help="the recipe's version when not given")
    for name, text in (("verify", "rehash an install against its manifest"),
                       ("use", "select an install for new sessions"),
                       ("remove", "remove an install no session or selection uses")):
        one = actions.add_parser(name, help=text)
        one.add_argument("adapter")
        one.add_argument("install")
    actions.add_parser("rollback", help="select the install selected before").add_argument("adapter")
    prune = actions.add_parser("prune", help="show what prune would remove, or remove it with --confirm")
    prune.add_argument("--confirm", metavar="CODE", help="the code the last prune printed")


def _harness_request(args: argparse.Namespace) -> ctl.HarnessRequest:
    fields = {k: getattr(args, k, None) for k in ("adapter", "install", "version", "confirm")}
    return ctl.HarnessRequest(args.action, **{k: v for k, v in fields.items() if v is not None})


def wsctl_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wsctl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pause", "resume"):
        sub.add_parser(name, help=f"{name} new claims for a workstream").add_argument("ws")
    status = sub.add_parser("status", help="show what wsd is doing")
    status.add_argument("ws", nargs="?")
    status.add_argument("--all", action="store_true", help="list closed and dropped beads too")
    sub.add_parser("reload", help="reload accounts, usage and models, then re-gate account changes")
    unlanded = sub.add_parser("unlanded", help="show a workstream's sessions whose commits were not landed")
    unlanded.add_argument("ws")
    resolve = sub.add_parser("resolve", help="land, keep or discard an unlanded session's commits")
    resolve.add_argument("ws")
    resolve.add_argument("session", help="the session's short ID, as wsctl unlanded shows it")
    resolve.add_argument("--generation", type=int, required=True)
    actions = resolve.add_mutually_exclusive_group(required=True)
    for action, text in (("land", "fast-forward the bead branch to the session's tip"),
                         ("keep", "keep the tip on refs/heads/unlanded/<bead>/<session>-g<generation>"),
                         ("discard", "drop the session's commits")):
        actions.add_argument(f"--{action}", dest="action", action="store_const", const=action, help=text)
    resolve.add_argument("--tip", help="the session's tip, as wsctl unlanded shows it: the confirmation")
    _harness_parser(sub.add_parser("harness", help="list, install, verify, use and remove managed installs"))
    args = parser.parse_args(argv)
    if args.cmd == "resolve" and args.action != "discard" and args.tip is None:
        resolve.error(f"--{args.action} needs --tip")
    try:
        s = _settings()
        if args.cmd == "status":
            reply = _ask(s, ctl.CtlRequest("status", ws=args.ws, all=args.all))
            return 1 if reply is None else _print(reply)
        if args.cmd == "reload":
            reply = _ask(s, ctl.CtlRequest("reload"))
            return 1 if reply is None else _print(reply)
        if args.cmd == "unlanded":
            reply = _ask(s, ctl.CtlRequest("unlanded", ws=args.ws))
            return 1 if reply is None else _print_unlanded(reply)
        if args.cmd == "harness":
            reply = _ask(s, ctl.CtlRequest("harness", harness=_harness_request(args)))
            return 1 if reply is None else _print(reply)
        if args.cmd == "resolve":
            reply = _ask(s, ctl.CtlRequest("resolve", ws=args.ws, session=args.session,
                                           generation=args.generation, action=args.action, tip=args.tip))
            return 1 if reply is None else _print(reply)
        reply = _ask(s, ctl.CtlRequest(args.cmd, ws=args.ws))
        return 1 if reply is None else _print(reply)
    except ConfigError as exc:
        print(f"wsctl: {exc}", file=sys.stderr)
        return EX_CONFIG
