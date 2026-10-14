"""S9-10 (plan 4c Amendment 1 §4.5): the launcher's own Codex launch shape, from the candidate adapter.

Run from a worktree at the Task 6 PR's head: `python docs/spikes/s9-10/generate.py <T> <store-cli> [--e25]`.
It prepares the home with `Codex.prepare_home` (and, with `--e25`, clears and makes the empty read-only
directories as `prepare_install_home` does) and writes `<T>/argv.json` with `server_argv` and `tui_argv` for
a new session. Part 1 has no resume: a thread needs model traffic (Part 2). Nothing is hand-written."""

import argparse
import json
from pathlib import Path

from heterodyne.agents import exec_dirs
from heterodyne.agents.base import Cli
from heterodyne.agents.codex import APP_SOCKET, Codex
from heterodyne.sandbox.spec import BRIDGE_INSIDE, SessionLayout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("store_cli", type=Path)
    ap.add_argument("--e25", action="store_true")
    ap.add_argument("--model", default="")
    args = ap.parse_args()
    codex = Codex()
    layout = SessionLayout(args.root / "s")
    layout.home.mkdir(parents=True, exist_ok=True)
    worktree = args.root / "wt"
    worktree.mkdir(exist_ok=True)
    codex.prepare_home(layout, worktree)
    if args.e25:
        exec_dirs.prepare(layout.home, codex.empty_read_only)
    binary = (args.store_cli / "bin" / "codex").resolve(strict=True)
    cli = Cli(binary, args.store_cli.resolve(), codex.version_pin)
    profile = {"model": args.model} if args.model else {}
    out = {
        "session": str(layout.root), "home": str(layout.home), "bridge": str(layout.bridge),
        "daemon": str(layout.daemon), "worktree": str(worktree),
        "bridge_inside": str(BRIDGE_INSIDE), "app_socket": APP_SOCKET,
        "server": codex.server_argv(cli),
        "tui_new": codex.tui_argv(cli, profile, native_id=None, resume=False, label="s9-10"),
        "empty_read_only": [str(p) for p in codex.empty_read_only],
        "tool_env": sorted(codex.versions[codex.version_pin].tool_env),
    }
    for name in ("server", "tui_new"):
        argv = out[name]
        if any(a in ("-c", "--config") or a.startswith("-c") for a in argv):
            raise SystemExit(f"{name} carries a config override: E9 says no -c")
    (args.root / "argv.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("server", "tui_new")}, indent=1))


if __name__ == "__main__":
    main()
