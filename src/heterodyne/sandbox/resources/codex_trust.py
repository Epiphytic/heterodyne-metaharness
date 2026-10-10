# pyright: basic
"""Inside a Codex sandbox, before its TUI starts (plan 4 D6; spike S8): ask `codex app-server` over stdio
which hooks it would not run yet. No model request is made. Prints a JSON list of {key, hash, event,
status} for every untrusted or modified hook and exits 0; any failure exits 1 with nothing on stdout.
The host validates every entry before writing it. Standard library only.

Usage: python3 -I codex_trust.py <codex binary> <worktree>
"""

import json
import subprocess
import sys
import threading

LIMIT_SECONDS = 60


def main(argv):
    codex, work = argv
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
        data = call(1, "hooks/list", {"cwds": [work]})["data"]
    finally:
        timer.cancel()
        proc.kill()
        proc.wait(5)
    out = [{"key": h["key"], "hash": h["currentHash"], "event": h["eventName"], "status": h["trustStatus"]}
           for entry in data for h in entry["hooks"] if h["trustStatus"] in ("untrusted", "modified")]
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001 - any failure refuses the launch on the host
        print(f"codex_trust: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
