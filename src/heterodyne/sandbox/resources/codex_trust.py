# pyright: basic
"""Inside a Codex sandbox, before its TUI starts (plan 4 D6; spike S8): ask `codex app-server` over stdio
which hooks it would not run yet. No model request is made. Prints a JSON list of {key, hash, event,
status} for every untrusted or modified hook and exits 0. The host reads an empty list as "every shim hook
will run", so the answer is checked first: one entry, for the worktree, with no load errors, every status
known, and every hook in $CODEX_HOME/hooks.json listed, enabled and unchanged. Anything else, or any
failure, exits 1 with nothing on stdout. The host validates every entry before writing it. Standard
library only.

Usage: python3 -I codex_trust.py <codex binary> <worktree>
"""

import json
import os
import re
import subprocess
import sys
import threading

LIMIT_SECONDS = 60
STATUSES = ("trusted", "untrusted", "modified")


def expected_hooks():
    """{key: command} for each hook the session's hooks.json holds, keyed as codex keys them."""
    path = os.path.join(os.environ["CODEX_HOME"], "hooks.json")
    with open(path) as fh:
        hooks = json.load(fh)["hooks"]
    return {f"{path}:{re.sub(r'(?<!^)(?=[A-Z])', '_', event).lower()}:{i}:{j}": hook["command"]
            for event, groups in hooks.items() for i, group in enumerate(groups)
            for j, hook in enumerate(group["hooks"])}


def checked(result, work, expected):
    """The listed hooks, once the answer is one the host can act on; ValueError otherwise."""
    data = result["data"]
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("hooks/list answered for other directories")
    entry = data[0]
    if entry["cwd"] != work or entry["errors"] != [] or not isinstance(entry["hooks"], list):
        raise ValueError("hooks/list did not load the worktree's hooks cleanly")
    hooks = entry["hooks"]
    seen = {}
    for h in hooks:
        if h["trustStatus"] not in STATUSES or not isinstance(h["key"], str):
            raise ValueError("hooks/list gave a hook an unknown status")
        seen[h["key"]] = h
    for key, command in expected.items():
        h = seen.get(key)
        if h is None or h["command"] != command or h["enabled"] is not True:
            raise ValueError("hooks/list is missing a shim hook, or has it disabled or changed")
    return hooks


def main(argv):
    codex, work = argv
    expected = expected_hooks()
    proc = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True)
    stdin, stdout = proc.stdin, proc.stdout
    if stdin is None or stdout is None:
        raise RuntimeError("codex app-server has no pipes")
    timer = threading.Timer(LIMIT_SECONDS, proc.kill)
    timer.start()

    def send(message):
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()

    def call(id_, method, params):
        send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        while True:
            line = stdout.readline()
            if not line:
                raise RuntimeError("codex app-server closed its output")
            message = json.loads(line)
            if message.get("id") == id_:
                if "error" in message:
                    raise RuntimeError("codex app-server returned an error")
                return message["result"]

    try:
        call(0, "initialize", {"clientInfo": {"name": "heterodyne", "version": "1"}})
        send({"jsonrpc": "2.0", "method": "initialized"})
        hooks = checked(call(1, "hooks/list", {"cwds": [work]}), work, expected)
    finally:
        timer.cancel()
        proc.kill()
        proc.wait(5)
    out = [{"key": h["key"], "hash": h["currentHash"], "event": h["eventName"], "status": h["trustStatus"]}
           for h in hooks if h["trustStatus"] != "trusted"]
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure refuses the launch on the host
        print(f"codex_trust: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
