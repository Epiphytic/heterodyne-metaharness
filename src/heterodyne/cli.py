"""`heterodyne` command line."""

import argparse
import json
import sys

from heterodyne import platform


def cmd_platform(_: argparse.Namespace) -> int:
    os_name = platform.detect()
    print(json.dumps({"os": os_name, **platform.backends(os_name)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heterodyne")
    sub = parser.add_subparsers(dest="command", required=True)
    platform_cmd = sub.add_parser("platform", help="show the detected platform and its backends")
    platform_cmd.set_defaults(func=cmd_platform)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
