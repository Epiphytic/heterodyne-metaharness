"""S9-10 Part 2's launcher steps, from the candidate adapter, run inside the run's namespaces by inner2.sh.

    part2_steps.py run-files <dir>                     # Codex.run_files() into RUN_INSIDE
    part2_steps.py trust <session> <store-cli> <wt>    # runtime._trust: list, seed, list again (D6)
    part2_steps.py thread <session>                    # the new session's thread ID, from its rollout
    part2_steps.py answered <session>                  # exit 0 once the rollout holds an assistant message

`trust` runs the adapter's `trust_argv` with the same `env -i` as the CLI. Prints only counts and IDs."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from heterodyne.agents.base import Cli
from heterodyne.agents.codex import Codex
from heterodyne.sandbox.spec import SessionLayout

ROLLOUT = re.compile(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl")


def env(layout: SessionLayout, cli: Path) -> dict[str, str]:
    return {"HOME": str(layout.home), "CODEX_HOME": str(layout.home / ".codex"),
            "PATH": f"{cli / 'bin'}:/usr/bin:/bin", "TERM": "xterm-256color"}


def main() -> None:
    codex = Codex()
    step = sys.argv[1]
    if step == "run-files":
        out = Path(sys.argv[2])
        for name, text in codex.run_files().items():
            (out / name).write_text(text)
        return
    layout = SessionLayout(Path(sys.argv[2]))
    if step == "trust":
        cli_root = Path(sys.argv[3]).resolve()
        cli = Cli(cli_root / "bin" / "codex", cli_root, codex.version_pin)
        argv = codex.trust_argv(cli, Path(sys.argv[4]))
        if argv is None:
            raise SystemExit("codex has no trust step")
        for attempt in range(2):
            r = subprocess.run(argv, env=env(layout, cli_root), capture_output=True, timeout=60,  # noqa: S603
                               cwd=sys.argv[4], check=False)
            if r.returncode != 0:
                raise SystemExit(f"the hook trust listing failed (attempt {attempt}, rc {r.returncode})")
            listed = codex.apply_trust(layout, r.stdout)
            print(f"trust attempt {attempt}: {listed} untrusted or modified hooks listed")
            if listed and attempt:
                raise SystemExit("hooks are still untrusted after seeding")
            if not listed:
                return
        return
    sessions = layout.home / ".codex" / "sessions"
    rollouts = sorted((Path(d) / f, m.group(1)) for d, _, files in os.walk(sessions)
                      for f in files if (m := ROLLOUT.fullmatch(f)))
    if step == "answered":
        for path, _ in rollouts:
            for line in path.read_text(errors="replace").splitlines():
                item = json.loads(line) if line.startswith("{") else {}
                payload = item.get("payload") or {}
                if item.get("type") == "response_item" and payload.get("role") == "assistant":
                    return
        raise SystemExit(1)
    if step == "thread":
        found = [thread for _, thread in rollouts]
        if len(found) != 1 or not codex.has_state(layout.home, found[0]):
            raise SystemExit(f"expected one rollout with state, found {len(found)}")
        print(found[0])
        return
    raise SystemExit(f"unknown step {step}")


if __name__ == "__main__":
    main()
