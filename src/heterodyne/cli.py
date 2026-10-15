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
from heterodyne.config import accounts, layers, paths
from heterodyne.config.secret_scan import show
from heterodyne.harness import paths as store_paths
from heterodyne.harness import selection
from heterodyne.harness import settings as harness_settings


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
        harness_lines = _harness_lines(cfg, os.environ)
    except hconfig.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    for key, value in _flatten(cfg.values):
        # D10: no message shows a login path; `show` redacts secrets and identifiers (a 64-hex commit).
        shown = "<hidden>" if _is_login_dir(key) else show(value)
        # Plan 4c Q5: the sandbox runs managed installs only; admind still runs the CLI this key names.
        note = ", admind only" if _is_adapter_binary(key) else ""
        print(f"{key} = {shown}    ({cfg.sources.get(key, '?')}{note})")
    print(f"policy: approvers={list(cfg.policy.approvers)}  (policy.toml)")
    if len({p.get("model") for p in cfg.get("profiles", {}).values()}) < 2:
        print("note: only one model configured; reviews will be adversarial (two LLMs recommended, §11.1)")
    for warning in accounts.warnings(cfg.values):
        print(warning)
    for line in harness_lines:
        print(line)
    return 0


def _harness_lines(cfg: hconfig.Config, env: Mapping[str, str]) -> list[str]:
    """Plan 4c: the store in use, the selection with its source, each store problem (a launch would refuse)
    and, under the fallback, its warning. Raises ConfigError for a bad `[harnesses]` or `harnesses.toml`."""
    h = harness_settings.from_config(cfg, env)
    lines: list[str] = []
    if "harnesses.store" not in cfg.sources:
        source = "defaults, fallback" if h.fallback else "defaults"
        lines.append(f"harnesses.store = {show(str(h.store))}    ({source})")
    chosen = selection.load(paths.config_dir(env))
    known = layers.string_list(cfg.get("adapters.known", []), "adapters.known")
    for adapter in sorted(set(known) | set(chosen)):
        sel = chosen.get(adapter)
        shown = "none"
        if sel and sel.current:
            shown = f"{sel.current.install} (digest {sel.current.digest[7:19]}, {len(sel.previous)} previous)"
        elif sel and sel.previous:
            shown = f"none ({len(sel.previous)} previous)"
        lines.append(f"harnesses.selection.{show(adapter, False)} = {shown}    ({selection.FILE})")
    problems = store_paths.location_problems(h, [a.login_dir for a in cfg.accounts.values()], env)
    lines += [f"harnesses problem: {problem}" for problem in problems]
    if h.fallback:
        lines.append(f"warning: {harness_settings.FALLBACK_WARNING}")
    return lines


def _is_adapter_binary(key: str) -> bool:
    parts = key.split(".")
    return len(parts) == 3 and parts[0] == "adapters" and parts[2] == "binary"


def _is_login_dir(key: str) -> bool:
    parts = key.split(".")
    return len(parts) == 3 and parts[0] == "accounts" and parts[2] == "login_dir"


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
