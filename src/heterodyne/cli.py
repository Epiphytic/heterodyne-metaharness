"""`heterodyne` command line."""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from heterodyne import config as hconfig
from heterodyne import platform
from heterodyne.config import layers, paths


def cmd_platform(_: argparse.Namespace) -> int:
    os_name = platform.detect()
    print(json.dumps({"os": os_name, **platform.backends(os_name)}, indent=2))
    return 0


def _flatten(tree: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key, value in tree.items():
        table = layers.as_table(value)
        if table is not None:
            out += _flatten(table, f"{prefix}{key}.")
        else:
            out.append((f"{prefix}{key}", value))
    return out


def cmd_config_check(args: argparse.Namespace) -> int:
    try:
        cfg = hconfig.load(args.workstream)
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    for key, value in _flatten(cfg.values):
        print(f"{key} = {value!r}    ({cfg.sources.get(key, '?')})")
    print(f"policy: approvers={list(cfg.policy.approvers)}  (policy.toml)")
    if len({p.get("model") for p in cfg.get("profiles", {}).values()}) < 2:
        print("note: only one model configured; reviews will be adversarial (two LLMs recommended, §11.1)")
    return 0


def _create_new(dest: Path, text: str) -> bool:
    """Create `dest` 0600 with `text`, atomically refusing any existing entry, including a symlink.

    O_EXCL fails on any existing name (a dangling symlink included), so the check and the create
    cannot race, and the file is 0600 from creation (umask can only remove bits).
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(dest, flags, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return True


def cmd_setup(_: argparse.Namespace) -> int:
    target = paths.config_dir(os.environ)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    examples = Path(str(resources.files("heterodyne"))).parent.parent / "examples"
    os_name = platform.detect()
    chosen = platform.backends(os_name)
    fill = {"<linux-or-macos>": os_name, "<service-manager>": chosen["service_manager"],
            "<sandbox-backend>": chosen["sandbox"]}
    for name in ("config.toml", "policy.toml"):
        dest = target / name
        text = (examples / name).read_text()
        for placeholder, value in fill.items():
            text = text.replace(placeholder, value)
        if _create_new(dest, text):
            print(f"wrote {dest} (edit the <placeholders>)")
        else:
            print(f"kept existing {dest} (not modified)")
    print(f"platform: {os_name} {chosen}")
    try:
        hconfig.load()
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heterodyne")
    sub = parser.add_subparsers(dest="command", required=True)
    platform_cmd = sub.add_parser("platform", help="show the detected platform and its backends")
    platform_cmd.set_defaults(func=cmd_platform)
    cfg = sub.add_parser("config", help="inspect configuration")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    check = cfg_sub.add_parser("check", help="validate the merged config and show each value's source")
    check.add_argument("--workstream")
    check.set_defaults(func=cmd_config_check)
    setup = sub.add_parser("setup", help="create the host config from examples (never overwrites)")
    setup.set_defaults(func=cmd_setup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
