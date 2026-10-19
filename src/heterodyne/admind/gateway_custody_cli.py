"""Closed same-UID custody control client. Never reads or writes daemon SQLite."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import msgspec

from heterodyne.admind import ctl, recovery
from heterodyne.admind import gateway_custody as custody
from heterodyne.admind import gateway_custody_control as control
from heterodyne.admind.settings import AdmindSettings


def parser(parent: argparse.ArgumentParser) -> None:
    sub = parent.add_subparsers(dest="custody_command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--descriptor", required=True, type=Path)
    sub.add_parser("status").add_argument("proposal_sha256")
    permit = sub.add_parser("permit")
    permit.add_argument("proposal_sha256")
    permit.add_argument("--nonconsumption-sha256")


def command(settings: AdmindSettings, args: argparse.Namespace) -> int:
    try:
        req: custody.Request
        if args.custody_command == "register":
            req = custody.GatewayCustodyRegister(custody.descriptor(recovery.private_read(args.descriptor)))
        elif args.custody_command == "status":
            req = custody.GatewayCustodyStatus(args.proposal_sha256)
        else:
            req = custody.GatewayCustodyPermit(args.proposal_sha256, args.nonconsumption_sha256)
        if custody.check(req) is not None:
            raise custody.Invalid("request")
        reply = asyncio.run(
            ctl.request(
                settings.state_dir / control.SOCKET,
                req,
                10,
                reply_type=control.Reply,
                max_reply=custody.MAX_REQUEST,
                require_uid=os.getuid(),
            )
        )
        print(msgspec.json.encode(reply).decode())
        return 0 if reply.result in ("ok", "registered", "issued") else 1
    except ctl.CtlUnavailable:
        print("admind: custody unavailable; read back retained state before retry", file=sys.stderr)
        return 69
    except (custody.Invalid, OSError, ValueError, msgspec.DecodeError):
        print("admind: invalid custody input", file=sys.stderr)
        return 1
