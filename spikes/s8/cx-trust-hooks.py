# Setup step (S8): pre-accepts Codex's "Hooks need review" dialog through Codex's own configuration.
# Asks `codex app-server` (hooks/list, for the workdir in argv[1]) for each hook's key and current hash,
# and appends a hooks.state trusted_hash entry per untrusted or modified hook to $CODEX_HOME/config.toml.
# No model request is made. Prints each key's event and trust status before the change.
import json
import os
import subprocess
import sys

p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)


def call(id_, method, params):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}) + "\n")
    p.stdin.flush()
    while True:
        m = json.loads(p.stdout.readline())
        if m.get("id") == id_:
            return m


call(0, "initialize", {"clientInfo": {"name": "s8-setup", "version": "0"}})
p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
p.stdin.flush()
res = call(1, "hooks/list", {"cwds": [sys.argv[1]]})["result"]["data"]
p.stdin.close()
p.terminate()
p.wait(5)
lines = []
for entry in res:
    for err in entry["errors"]:
        print("error:", err["message"], file=sys.stderr)
    for h in entry["hooks"]:
        print(h["eventName"], h["trustStatus"], h["source"])
        if h["trustStatus"] in ("untrusted", "modified"):
            lines.append(f'\n[hooks.state."{h["key"]}"]\ntrusted_hash = "{h["currentHash"]}"\n')
with open(os.path.join(os.environ["CODEX_HOME"], "config.toml"), "a") as f:
    f.writelines(lines)
