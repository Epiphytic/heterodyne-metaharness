"""`admind` command line: init, run, operators, rearm, ask, unit, hook (ADR 0001 §8; relay spec R1, R2)."""

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn, cast

import msgspec

from heterodyne import config as hconfig
from heterodyne.admind import approvals, asks, ctl, unit
from heterodyne.admind.agent import TMUX_SOCKET, AdminAgent, tmux_launcher
from heterodyne.admind.audit import Audit
from heterodyne.admind.commands import CommandRunner
from heterodyne.admind.daemon import Admind, supervised
from heterodyne.admind.redact import set_login_dirs
from heterodyne.admind.settings import AdmindSettings, resolve
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.config.secret_scan import show
from heterodyne.marmot.control import ControlClient, ControlError
from heterodyne.services import ServiceManager, for_backend
from heterodyne.tmux import Tmux

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it
EX_UNAVAILABLE = 69     # sysexits: `admind ask` found no daemon
EX_TIMEOUT = 3          # `admind ask wait` ran out of time with no answer (R2)
WAIT_POLL = 2.0         # seconds between `admind ask wait` polls (R2); replaced in tests
WAIT_TIMEOUT = 540.0
ASK_READ_SECONDS = 30.0     # one ask.sock reply
NOT_RUNNING = "admind is not running (or did not answer)."
POST_UNANSWERED = ("admind did not answer in time. The ask may still have been posted: check "
                   "`admind ask list` before posting it again.")
IDENTITY_LABEL = "heterodyne-admind"


class StateDirError(Exception):
    """The state directory could not be prepared; the message is built here and holds no path."""


def _resolve() -> AdmindSettings:
    """The settings, with redaction of configured login directories installed before anything is posted,
    audited or summarized."""
    s = resolve(hconfig.load(), os.environ)
    set_login_dirs(s.login_dirs)
    return s


def _load() -> tuple[AdmindSettings, Store, Audit]:
    s = _resolve()
    try:
        return s, Store(s.state_dir / "admind.db"), Audit(s.state_dir / "audit.jsonl")
    except (OSError, sqlite3.Error) as exc:  # the path may hold an npub; name only the kind of failure
        raise StateDirError(
            f"admind: cannot prepare the admind state directory ({type(exc).__name__})") from None


async def init(s: AdmindSettings, store: Store, audit: Audit) -> int:
    """Create admind's identity (if its home has none) and the two-member group with the operator."""
    if store.get("group_id_hex"):
        print("admind is already initialised; its group exists. To start over, stop admind and remove "
              f"{show(str(s.state_dir), False)} (this abandons the old identity and group).")
        return 1
    wn = WnAgent(s.wn_agent, s.marmot_home, s.relays, audit)
    try:
        wn.prepare()
        client = ControlClient(wn.socket_path, wn.token())
        await wn.start(client)
        accounts = [a for a in (await client.account_list()).accounts if a.local_signing]
        if not accounts:
            # bootstrap is a client of the running child's socket (it takes --socket and
            # --wait-for-socket), so the child must be running first; S4's "runtime root is already in
            # use" applies to direct-mode `wn` commands, not to socket clients.
            # Output holds invite details, so it is captured, never printed.
            try:
                proc = await asyncio.to_thread(subprocess.run, wn.bootstrap_argv(IDENTITY_LABEL),
                                               capture_output=True, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"wn-agent bootstrap could not run ({type(exc).__name__})", file=sys.stderr)
                return 1
            if proc.returncode != 0:
                print(f"wn-agent bootstrap failed (exit {proc.returncode})", file=sys.stderr)
                return 1
        account = await wn.account(client)
        created = await client.group_create(account, s.group_name, [o.npub for o in s.operators])
        with store.transaction():
            store.set("account_id_hex", account)
            store.set("group_id_hex", created.group_id_hex.lower())
            store.set("expected_members", str(1 + len(s.operators)))
            store.set("group_operators", json.dumps(sorted(o.hex for o in s.operators)))
        audit.write("init", action="group_created")
    except (WnAgentError, ControlError) as exc:
        # Never the peer's `detail`: it can echo the bearer token. Only the allowlisted code and flag.
        peer = {"code": exc.code, "retryable": exc.retryable} if isinstance(exc, ControlError) else {}
        audit.write("init", action="failed", error=str(exc), **peer)
        print(f"admind init failed: {show(str(exc), False)}", file=sys.stderr)  # own wording only
        return 1
    finally:
        await wn.stop()
    print(f"Created admind's identity and its group with {len(s.operators)} operator(s). Each operator "
          "accepts the invite in their Marmot client; then start admind and send any message in the group: "
          "admind answers once it sees one of you.")
    return 0


def ask_daemon(s: AdmindSettings, req: ctl.CtlRequest) -> int:
    try:
        reply = asyncio.run(ctl.request(s.state_dir / ctl.CTL_SOCKET, req))
    except ctl.CtlUnavailable:
        print("admind is not running (or did not answer). Start it first; it starts latched if a "
              "membership change was interrupted.", file=sys.stderr)
        return 1
    print(show(reply.message, False))   # already redacted by the daemon; this escapes terminal controls
    return 0 if reply.result in ("committed", "rearmed") else 1


def post_seconds() -> float:
    """A post's whole budget. Posts run one at a time (R22), so a post may wait for every other post in
    flight, and each may read a bead with approve-bead, reap it, and then its own read; plus the margin of
    an ordinary request. Bounded, and derived from the daemon's own limits."""
    return asks.MAX_IN_FLIGHT * (approvals.READ_SECONDS + approvals.REAP_SECONDS) + ASK_READ_SECONDS


def _ask_request(s: AdmindSettings, req: asks.AskRequest, timeout: float = ASK_READ_SECONDS) -> asks.AskReply:
    """One request on ask.sock, the whole of it (connect, write, read) within `timeout` seconds (raises
    ctl.CtlUnavailable). The reply limit matches the daemon's (R24)."""
    async def bounded() -> asks.AskReply:
        try:
            reply = ctl.request(s.state_dir / asks.ASK_SOCKET, req, timeout, reply_type=asks.AskReply,
                                max_reply=asks.MAX_REPLY)
            return await asyncio.wait_for(reply, timeout)
        except TimeoutError:
            raise ctl.CtlUnavailable("TimeoutError") from None
    return asyncio.run(bounded())


def _terminal(text: str) -> str:
    """Poster-facing text for a terminal: line by line through `show`, so newlines survive and control
    characters, secrets and identifiers do not. `--json` gives the text verbatim."""
    return "\n".join(show(line, False) for line in text.split("\n"))


def _print_view(view: asks.AskView) -> None:
    sm = view.summary
    head = f"ask {sm.ask_id} · {sm.kind} · {sm.status}" + ("" if sm.delivered else " · not yet delivered")
    print(head)
    for a in view.answers:
        print(f"--- {a.kind} from {_terminal(a.operator)} at {a.at}")
        print(_terminal(a.text))
    if sm.outcome is not None:
        print(f"outcome: {_terminal(sm.outcome)}")


def _ask_post(args: argparse.Namespace) -> asks.AskPost | None:
    body = ""
    if args.body_file is not None:
        try:
            body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
        except (OSError, ValueError) as exc:     # ValueError: not UTF-8
            print(f"admind: cannot read the body ({type(exc).__name__})", file=sys.stderr)
            return None
    return asks.AskPost(args.kind, args.title, body, args.pr, args.head, args.bead, args.poster)


def ask_command(s: AdmindSettings, args: argparse.Namespace) -> int:
    """`admind ask post|get|wait|list|cancel`. Exit 0 on success, 1 when refused or failed (the reason on
    stderr), 69 when no daemon answers, 3 when `wait` times out."""
    req: asks.AskRequest
    if args.ask_command == "post":
        post = _ask_post(args)
        if post is None:
            return 1
        req = post
    elif args.ask_command == "list":
        req = asks.AskList()
    elif args.ask_command == "cancel":
        req = asks.AskCancel(args.ask_id)
    else:
        req = asks.AskGet(args.ask_id)
    refusal = asks.check(req)       # the daemon checks again; this gives the reason without a round trip
    if refusal is not None:
        print(f"admind: {show(refusal, False)}", file=sys.stderr)
        return 1
    if args.ask_command == "wait":
        return _ask_wait(s, req, args.timeout, args.json)
    post = isinstance(req, asks.AskPost)
    try:
        reply = _ask_request(s, req, post_seconds() if post else ASK_READ_SECONDS)
    except ctl.CtlUnavailable:
        # a post the daemon received goes on without its client, so it may still be stored and its card sent
        print(NOT_RUNNING + (" " + POST_UNANSWERED if post else ""), file=sys.stderr)
        return EX_UNAVAILABLE
    if getattr(args, "json", False):
        print(msgspec.json.encode(reply).decode())
    elif reply.result in ("refused", "failed"):
        print(f"admind: {_terminal(reply.message)}", file=sys.stderr)
    elif reply.asks is not None:
        for sm in reply.asks:
            print(f"{sm.ask_id} {sm.kind} · {sm.status} · {sm.answer_count} answers · {_terminal(sm.title)}")
    elif args.ask_command == "get" and reply.ask is not None:
        _print_view(reply.ask)
    else:
        print(_terminal(reply.message))
    return 0 if reply.result in ("posted", "ok") else 1


def _ask_wait(s: AdmindSettings, req: asks.AskRequest, timeout: float, as_json: bool) -> int:
    """Poll `get` every WAIT_POLL seconds until the ask has an answer or note, or is terminal (R2). A
    daemon that is down or restarting is retried until the timeout. Exit 0 is not approval evidence: an
    approval ask's first note ends the wait too, and only the read-back status (`get`) says it was decided.
    A refusal goes to stderr, even with `--json`."""
    deadline = time.monotonic() + timeout
    reply: asks.AskReply | None = None
    while (left := deadline - time.monotonic()) > 0:
        try:
            reply = _ask_request(s, req, min(left, ASK_READ_SECONDS))
        except ctl.CtlUnavailable:
            reply = None
        if reply is not None and reply.result != "ok":
            print(f"admind: {_terminal(reply.message)}", file=sys.stderr)
            return 1
        view = None if reply is None else reply.ask
        if reply is not None and view is not None and (view.answers or view.summary.status in asks.TERMINAL):
            if as_json:
                print(msgspec.json.encode(reply).decode())
            else:
                _print_view(view)
            return 0
        time.sleep(max(0.0, min(WAIT_POLL, deadline - time.monotonic())))
    print("admind: no answer yet (timed out)" if reply is not None else NOT_RUNNING, file=sys.stderr)
    return EX_TIMEOUT


def _settings_only() -> AdmindSettings:
    return _resolve()


def _with_settings(args: argparse.Namespace) -> int:
    if args.command == "ask":       # a client only: it opens no database (the daemon owns it)
        try:
            s = _settings_only()
        except hconfig.ConfigError as exc:
            print(f"config error: {show(str(exc), False)}", file=sys.stderr)
            return EX_CONFIG
        return ask_command(s, args)
    try:
        s, store, audit = _load()
    except hconfig.ConfigError as exc:
        print(f"config error: {show(str(exc), False)}", file=sys.stderr)
        return EX_CONFIG
    except StateDirError as exc:
        print(show(str(exc), False), file=sys.stderr)
        return EX_CONFIG
    if args.command == "init":
        return asyncio.run(init(s, store, audit))
    if args.command == "rearm":
        return ask_daemon(s, ctl.CtlRequest("rearm"))
    if args.command == "operators":
        return ask_daemon(s, ctl.CtlRequest(args.action, args.name))
    return run(s, store, audit)


def run(s: AdmindSettings, store: Store, audit: Audit) -> int:
    group = store.get("group_id_hex")
    if group is None:
        print("admind is not initialised: run `admind init` first", file=sys.stderr)
        return EX_CONFIG
    try:
        services = for_backend(s.service_manager)
    except hconfig.ConfigError as exc:
        print(f"config error: {show(str(exc), False)}", file=sys.stderr)
        return EX_CONFIG
    return asyncio.run(_serve(s, store, audit, group, services))


def _leaf(exc: BaseException) -> str:
    """The type name of the first leaf of a (nested) exception group."""
    current: BaseException = exc
    while isinstance(current, BaseExceptionGroup):
        members = cast(tuple[BaseException, ...], current.exceptions)  # pyright: ignore[reportUnknownMemberType]
        if not members:
            break
        current = members[0]
    return type(cast(object, current)).__name__


async def _stop_child(wn: WnAgent) -> None:
    """Stop and reap the child, and finish doing so even if this task is cancelled again meanwhile. Safe
    when the child never started."""
    stopping = asyncio.ensure_future(wn.stop())
    cancelled = False
    while not stopping.done():
        try:
            await asyncio.shield(stopping)
        except asyncio.CancelledError:
            cancelled = True
    stopping.result()
    if cancelled:
        raise asyncio.CancelledError


async def _serve(s: AdmindSettings, store: Store, audit: Audit, group: str, services: ServiceManager) -> int:
    """Run until SIGTERM or SIGINT. The child's lifetime is bounded from before it is started: the
    `finally` stops it whatever ends the run (a signal, any exception, a cancellation, a failed audit
    write), and the signal handlers are in place before the (up to 30 s) startup."""
    wn = WnAgent(s.wn_agent, s.marmot_home, s.relays, audit)
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    signalled = False
    handled: list[signal.Signals] = []

    def on_signal() -> None:
        nonlocal signalled
        signalled = True
        if main_task is not None:
            main_task.cancel()
    try:
        if main_task is not None:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, on_signal)
                handled.append(sig)
        return await _run_with_child(s, store, audit, group, services, wn)
    except asyncio.CancelledError:
        if not signalled:
            raise
        audit.write("admind", action="stop")
        return 0
    finally:
        try:
            await _stop_child(wn)
        finally:
            for sig in handled:
                loop.remove_signal_handler(sig)


async def _run_with_child(s: AdmindSettings, store: Store, audit: Audit, group: str, services: ServiceManager,
                          wn: WnAgent) -> int:
    try:
        wn.prepare()
        client = ControlClient(wn.socket_path, wn.token())
        await wn.start(client)
        account = await wn.account(client)
    except (WnAgentError, ControlError) as exc:
        audit.write("admind", action="start-failed", error=type(exc).__name__)
        print(f"admind: {show(str(exc), False)}", file=sys.stderr)    # own wording only
        return 1
    agent = AdminAgent(Tmux(TMUX_SOCKET, launcher=tmux_launcher(s)), store, s, s.state_dir / "hook.sock")
    runner = CommandRunner(agent, services, s.restart_units, wn.alive)
    daemon = Admind(s, client, store, audit, agent, runner, account, group,
                    load_operators=lambda: resolve(hconfig.load(), os.environ).operators)
    audit.write("admind", action="start")
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(supervised("wn-agent", lambda: wn.supervise(client), audit))
            tg.create_task(daemon.run())
    except Exception as exc:  # noqa: BLE001 - the daemon failed; one value-free line, never a traceback
        leaf = _leaf(exc)
        audit.write("admind", action="failed", error=leaf)
        print(f"admind: run failed ({leaf})", file=sys.stderr)
        return 1
    return 0


class _QuietParser(argparse.ArgumentParser):
    """argparse echoes rejected argument values (invalid choice, unrecognised arguments, bad types),
    and those can be an npub or a token. Report a fixed message instead: usage lists only fixed
    names; the exit status stays 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, "admind: invalid arguments (see --help)\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _QuietParser(prog="admind", description="heterodyne admin override channel")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_QuietParser)
    sub.add_parser("init", help="create admind's Marmot identity and its group with the operator")
    sub.add_parser("run", help="run the daemon (normally from its service unit)")
    ops = sub.add_parser("operators", help="add or remove a group member (an operator in policy.toml)")
    ops.add_argument("action", choices=["add", "remove"])
    ops.add_argument("name")
    sub.add_parser("rearm", help="trust the group's current member count and clear the latch "
                                 "(check the members in your client first)")
    _ask_parser(sub.add_parser("ask", help="post a question or merge request to the operators and read "
                                           "the answers"))
    sub.add_parser("unit", help="print a systemd user unit for this install")
    hook = sub.add_parser("hook", help="(internal) forward an agent hook event to admind")
    hook.add_argument("--socket")
    return parser


def _ask_parser(ask: argparse.ArgumentParser) -> None:
    sub = ask.add_subparsers(dest="ask_command", required=True, parser_class=_QuietParser)
    post = sub.add_parser("post", help="post an ask; prints `ask <id> posted`")
    post.add_argument("--kind", required=True, choices=["question", "merge", "approval"])
    post.add_argument("--title", default="")
    post.add_argument("--body-file", help="the context, from a file or - for stdin")
    post.add_argument("--pr", help="merge: https://github.com/<owner>/<repo>/pull/<n>")
    post.add_argument("--head", help="merge: the pull request's 40-hex head commit")
    post.add_argument("--bead")
    post.add_argument("--from", dest="poster", default="local", help="a label for the card (unverified)")
    post.add_argument("--json", action="store_true")
    for name, what in (("get", "an ask's status and answers"), ("cancel", "cancel an open or answered ask"),
                       ("wait", "wait for an answer or note, or for the ask to end; for an approval ask "
                                "this is not approval evidence: read the decision with get")):
        one = sub.add_parser(name, help=what, description=what)
        one.add_argument("ask_id")
        if name != "cancel":
            one.add_argument("--json", action="store_true")
        if name == "wait":
            one.add_argument("--timeout", type=float, default=WAIT_TIMEOUT)
    sub.add_parser("list", help="active asks and the 20 most recent others").add_argument(
        "--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    """Run a subcommand. Last resort: an unexpected exception becomes one line naming only its type,
    never a traceback or `str(exc)` (paths and values in those can hold identifiers)."""
    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv and not argv[0].startswith("-") else "admind"
    try:
        return _dispatch(argv)
    except Exception as exc:  # noqa: BLE001 - the output boundary; SystemExit (argparse) passes through
        print(f"admind: {command} failed ({type(exc).__name__})", file=sys.stderr)
        return 0 if command == "hook" else 1


def _dispatch(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        from heterodyne.admind.hook import hook_main
        return hook_main(argv[1:], sys.stdin.buffer.read())
    args = build_parser().parse_args(argv)
    if args.command == "unit":
        try:
            print(unit.render(sys.executable, os.environ), end="")
        except ValueError as exc:
            print(f"cannot render the unit: {show(str(exc), False)}", file=sys.stderr)
            return EX_CONFIG
        return 0
    return _with_settings(args)
