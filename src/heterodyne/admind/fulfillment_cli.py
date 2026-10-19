"""Explicit fulfillment CLI: no daemon files or external operations are inspected by the server."""

import argparse
import asyncio
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from typing import cast

import msgspec

from heterodyne.admind import ctl
from heterodyne.admind import fulfillment as f
from heterodyne.admind.settings import AdmindSettings


def parser(parent: argparse.ArgumentParser) -> None:
    sub = parent.add_subparsers(dest="fulfillment_command", required=True)
    for name in ("apply", "propose", "dispose", "get", "reconcile", "inspect", "migrate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--json", action="store_true")
        if name == "inspect":
            continue
        if name == "migrate":
            mode = cmd.add_mutually_exclusive_group(required=True)
            mode.add_argument("--plan", action="store_true")
            mode.add_argument("--apply", action="store_true")
        elif name == "apply":
            cmd.add_argument("--receipt", required=True, type=Path)
        elif name == "propose":
            cmd.add_argument("--ask", required=True)
            cmd.add_argument("--declaration", required=True, type=Path)
            cmd.add_argument("--replace-intent")
        elif name == "dispose":
            cmd.add_argument("--ask", required=True)
            cmd.add_argument("--expect-intent", required=True)
            cmd.add_argument(
                "--reason", required=True, choices=["operator-withdrawal", "stale-answers", "expired"]
            )
        elif name == "get":
            cmd.add_argument("ask_id")
        else:
            cmd.add_argument("--receipts", required=True, type=Path)
            cmd.add_argument("--limit", type=int, default=20)
            cmd.add_argument("--continuation", help="exact manifest SHA256:next-index from a prior result")
            mode = cmd.add_mutually_exclusive_group()
            mode.add_argument("--plan", action="store_true")
            mode.add_argument("--apply", action="store_true")


def send(settings: AdmindSettings, req: f.Request) -> f.Reply:
    why = f.check(req)
    if why:
        raise f.Invalid(why)
    return asyncio.run(
        ctl.request(settings.state_dir / f.SOCKET, req, 30, reply_type=f.Reply, max_reply=f.MAX_REPLY)
    )


def command(settings: AdmindSettings, args: argparse.Namespace) -> int:
    try:
        name = args.fulfillment_command
        if name == "migrate":
            print(json.dumps(migration(settings, apply=args.apply)))
            return 0
        if name == "reconcile":
            result = reconcile(settings, args)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if name == "inspect":
            req = f.Inspect()
        elif name == "apply":
            req = f.Apply(f.parse(f.read_file(args.receipt), f.Completion))
        elif name == "propose":
            req = f.Propose(
                args.ask, f.parse(f.read_file(args.declaration), f.Declaration), args.replace_intent
            )
        elif name == "dispose":
            req = f.Dispose(args.ask, args.expect_intent, args.reason)
        else:
            req = f.Get(args.ask_id)
        result = send(settings, req)
        if args.json:
            print(msgspec.json.encode(result).decode())
        else:
            print(result.result + ": " + result.message)
        return 0 if result.result in ("ok", "applied", "already-applied", "pending", "retired") else 1
    except ctl.CtlUnavailable:
        print(
            "admind: fulfillment unavailable; query exact identity before retrying metadata only",
            file=sys.stderr,
        )
        return 69
    except (f.Invalid, OSError, ValueError, msgspec.DecodeError):
        print("admind: invalid fulfillment input", file=sys.stderr)
        return 1


def reconcile(settings: AdmindSettings, args: argparse.Namespace) -> dict[str, object]:
    """Each entry is independent. Unknown apply response stops, never blindly replays it."""
    if not 1 <= args.limit <= 20:
        raise f.Invalid("limit")
    data = f.read_file(args.receipts, manifest=True)
    try:
        raw = json.loads(data, object_pairs_hook=f.unique_pairs)
        receipts = msgspec.convert(raw, type=list[f.Completion], strict=True)
    except (ValueError, RecursionError, msgspec.ValidationError):
        raise f.Invalid("manifest") from None
    if not 1 <= len(receipts) <= 20 or len({r.binding.ask_id for r in receipts}) != len(receipts):
        raise f.Invalid("manifest-bounds")
    manifest_digest = f.digest("manifest", raw)
    offset = 0
    token = getattr(args, "continuation", None)
    if token is not None:
        head, separator, tail = token.partition(":")
        if separator != ":" or head != manifest_digest or not tail.isascii() or not tail.isdecimal():
            raise f.Invalid("continuation")
        if len(tail) > 2 or not 0 <= int(tail) <= len(receipts):
            raise f.Invalid("continuation")
        offset = int(tail)
    results: list[dict[str, object]] = []
    for receipt in receipts[offset : offset + args.limit]:
        ask_id = receipt.binding.ask_id
        try:
            why = f.check(f.Apply(receipt))
            if why:
                outcome = "incomplete"
            else:
                # Observe before every apply, including explicit continuation after a lost response.
                outcome = plan(receipt, send(settings, f.Get(ask_id)))
                if args.apply and outcome == "eligible":
                    applied = send(settings, f.Apply(receipt))
                    outcome = (
                        applied.result
                        if applied.result in ("applied", "already-applied")
                        else refusal_outcome(applied)
                    )
            results.append({"ask_id": ask_id, "outcome": outcome})
            if outcome == "unavailable":
                break
            offset += 1
        except (ctl.CtlUnavailable, f.Invalid, msgspec.DecodeError, KeyError, TypeError, ValueError):
            results.append({"ask_id": ask_id, "outcome": "unavailable"})
            break
    return {
        "schema": "fulfillment-reconcile-v1",
        "results": results,
        "continuation": f"{manifest_digest}:{offset}",
    }


def refusal_outcome(reply: f.Reply) -> str:
    if reply.result == "unavailable":
        return "unavailable"
    if reply.message in ("stale", "unretained-answer"):
        return "stale"
    if reply.message in ("conflict", "operation-conflict"):
        return "conflict"
    return "unsupported"


def plan(receipt: f.Completion, reply: f.Reply) -> str:
    if reply.result != "ok" or reply.view is None:
        return refusal_outcome(reply)
    view = reply.view
    stored = view.get("receipt")
    if stored is not None:
        return (
            "already-applied"
            if cast(dict[str, object], stored)["request_digest"]
            == f.digest("application", f.builtins(receipt))
            else "conflict"
        )
    if view["lifecycle"] == "conflict":
        return "conflict"
    actual = cast(dict[str, object], view["binding"])
    expected = receipt.binding
    if any(
        actual[key] != getattr(expected, key)
        for key in ("ask_store_id", "ask_id", "created_at", "ask_sha256", "answers_sha256")
    ):
        return "stale"
    if view.get("eligible") is not True:
        return "unsupported"
    return (
        "eligible"
        if set(expected.consumed_answers) <= set(msgspec.convert(actual["answer_ids"], type=list[str]))
        else "incomplete"
    )


def migration(settings: AdmindSettings, *, apply: bool) -> dict[str, object]:
    """Explicit local deployment step, never invoked by a daemon/constructor.

    The controller must first quiesce writers and retain its genuine deployment/backup receipts.
    The CLI records schema hashes, not a fabricated claim to have verified human authority.
    """
    from heterodyne.admind.fulfillment_projection import migrate_connection

    path = settings.state_dir / "admind.db"
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise f.Invalid("unsafe-store")
    db = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, isolation_level=None, timeout=1.0
    )
    try:
        return migrate_connection(db, apply=apply)
    finally:
        db.close()
