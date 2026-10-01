"""`admind` command line: init, run, rearm, unit, hook (ADR 0001 §8)."""

import argparse
import asyncio
import os
import subprocess
import sys

from heterodyne import config as hconfig
from heterodyne.admind import unit
from heterodyne.admind.audit import Audit
from heterodyne.admind.settings import AdmindSettings, resolve
from heterodyne.admind.store import Store
from heterodyne.admind.wnagent import WnAgent, WnAgentError
from heterodyne.config.secret_scan import show
from heterodyne.marmot.control import ControlClient, ControlError

EX_CONFIG = 78  # sysexits: configuration error; the unit does not restart on it
IDENTITY_LABEL = "heterodyne-admind"


def _load() -> tuple[AdmindSettings, Store, Audit]:
    s = resolve(hconfig.load(), os.environ)
    return s, Store(s.state_dir / "admind.db"), Audit(s.state_dir / "audit.jsonl")


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
        created = await client.group_create(account, s.group_name, [s.operator_npub])
        store.set("account_id_hex", account)
        store.set("group_id_hex", created.group_id_hex.lower())
        audit.write("init", action="group_created")
    except (WnAgentError, ControlError) as exc:
        audit.write("init", action="failed", error=str(exc), detail=getattr(exc, "detail", ""))
        print(f"admind init failed: {exc}", file=sys.stderr)  # admind's own wording only; see ControlError
        return 1
    finally:
        await wn.stop()
    print("Created admind's identity and its group with the operator. Accept the invite in your Marmot "
          "client, start admind, then send any message in the group: admind answers once it sees you.")
    return 0


def rearm(s: AdmindSettings, store: Store, audit: Audit) -> int:
    reason = show(store.get("latched"), False) if store.get("latched") else None
    store.delete("latched")
    audit.write("guard", action="rearm", previous=reason)
    print(f"Cleared the latch ({reason or 'was not latched'}). Check the group's member list in your "
          "client first: admind re-checks the member count, but can't see a one-for-one swap.")
    return 0


def _with_settings(fn: str) -> int:
    try:
        s, store, audit = _load()
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EX_CONFIG
    if fn == "init":
        return asyncio.run(init(s, store, audit))
    if fn == "rearm":
        return rearm(s, store, audit)
    return run(s, store, audit)


def run(s: AdmindSettings, store: Store, audit: Audit) -> int:
    raise NotImplementedError  # Task 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="admind", description="heterodyne admin override channel")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create admind's Marmot identity and its group with the operator")
    sub.add_parser("run", help="run the daemon (normally from its service unit)")
    sub.add_parser("rearm", help="clear the membership latch after checking the group")
    sub.add_parser("unit", help="print a systemd user unit for this install")
    hook = sub.add_parser("hook", help="(internal) forward an agent hook event to admind")
    hook.add_argument("--socket")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:
        from heterodyne.admind.hook import hook_main
        return hook_main(argv[1:], sys.stdin.buffer.read())
    args = build_parser().parse_args(argv)
    if args.command == "unit":
        try:
            print(unit.render(sys.executable, os.environ), end="")
        except ValueError as exc:
            print(f"cannot render the unit: {exc}", file=sys.stderr)
            return EX_CONFIG
        return 0
    return _with_settings(args.command)
