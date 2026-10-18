"""Bounded local recovery client; never opens the daemon database or runs native commands."""

import argparse
import asyncio
import sys
from pathlib import Path

import msgspec

from heterodyne.admind import ctl, recovery
from heterodyne.admind.settings import AdmindSettings


def parser(parent: argparse.ArgumentParser) -> None:
    sub = parent.add_subparsers(dest="recovery_command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--descriptor", required=True, type=Path)
    register.add_argument("--proof-set-sha256", required=True)
    status = sub.add_parser("status")
    status.add_argument("proposal_sha256")
    permit = sub.add_parser("permit")
    permit.add_argument("proposal_sha256")
    permit.add_argument("--certificate-sha256")


def command(settings: AdmindSettings, args: argparse.Namespace) -> int:
    try:
        req: recovery.Request
        if args.recovery_command == "register":
            req = recovery.Register(
                recovery.descriptor(recovery.private_read(args.descriptor)), args.proof_set_sha256
            )
        elif args.recovery_command == "status":
            req = recovery.Status(args.proposal_sha256)
        else:
            req = recovery.Permit(args.proposal_sha256, args.certificate_sha256)
        if recovery.check(req):
            raise recovery.Invalid("request")
        result = asyncio.run(
            ctl.request(
                settings.state_dir / recovery.SOCKET,
                req,
                10,
                reply_type=recovery.Reply,
                max_reply=recovery.MAX_BYTES,
            )
        )
        print(msgspec.json.encode(result).decode())
        return 0 if result.result in {"ok", "registered", "existing", "issued"} else 1
    except ctl.CtlUnavailable:
        print("admind: recovery unavailable; inspect the exact proposal before retry", file=sys.stderr)
        return 69
    except (recovery.Invalid, OSError, ValueError, msgspec.DecodeError):
        print("admind: invalid recovery input", file=sys.stderr)
        return 1
