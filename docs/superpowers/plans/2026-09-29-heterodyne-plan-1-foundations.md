# heterodyne-metaharness Plan 1: Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** stand up the new `heterodyne-metaharness` repo, answer spikes S1–S4, land the btq prerequisites, and ship a tested package skeleton with the platform seam, config layering, host-only policy and install-agnostic enforcement.

**Architecture:** a fresh clone replaces the old repo contents in one PR, keeping LICENSE. Spikes are investigations with written findings and pass/fail tables. Their throwaway code lives under `spikes/`, never in the package. btq gets three small, backwards-compatible changes in its own repo. The Python package (`src/heterodyne/`) starts with the pieces every later plan depends on: `platform.py`, `config/`, `cli.py`, and the default and example TOML.

**Tech Stack:** Python 3.12+, uv, pytest, hypothesis, ruff, pyright (strict), tomllib, bubblewrap 0.9 (spike), GitHub Actions, gitleaks (container, CI only).

**Spec:** ADR 0001 at revision `e36f6d09563897198cb7641cad8c9e33bb02e6d3` (`$DESIGN_REPO/docs/adr/0001-workstreams-v2.md`), approved in bead `btq-96hm`. Section numbers (§) below refer to it.

## Global Constraints

- **Variables:** `$HZ`, `$DESIGN_REPO` and `$BTQ_REPO` as defined in the roadmap. No step may write an install path into a committed file.
- **Old harness is off-limits:** never modify, move or delete the old harness checkout, or any symlink pointing to it. Its services are still running. `$HZ` must be a **new** clone at a new path.
- **Python** `>=3.12`, managed with `uv`. The runtime dependency list for this plan is empty. Dev dependencies: `pytest`, `hypothesis`, `ruff`, `pyright`.
- **`sys.platform`** appears only in `src/heterodyne/platform.py` (§3.2).
- **No model names or agent pairings** in `src/` (§4.1). Enforced by the checker in Task 9.
- **Install-agnostic repo** (§15): no absolute home paths, IP:port literals, npubs, DIDs, Radicle IDs, nsecs, emails other than example domains, or secrets, outside `examples/` and `tests/fixtures/`. Examples use `<placeholder>` values only.
- **Config precedence** (§15): defaults → host `config.toml` → `workstreams/<ws>.toml` → bead role label → env/CLI. Env vars cover locations and debugging only. `policy.toml` is host-only, and lower layers may only tighten policy through `[restrict]`.
- **Review rule** (§11.1): every code task ends with a review by a **different LLM than the implementer**.
  - If the implementer is Claude: `codex exec -m gpt-6-sol -c model_reasoning_effort=medium -s read-only -o /tmp/review.md "<brief>"`.
  - If the implementer is Codex/GPT: `claude -p --model claude-opus-5-5 --permission-mode plan "<brief>" > /tmp/review.md`.
  - The brief names the diff range and the ADR sections, and asks for `[BLOCKING]`/`[NON-BLOCKING]` findings. Fix or rebut every blocking finding.
  - The close evidence includes `Code-Review: reviewer=<model> author=<model> mode=cross-model range=<BASE>..<HEAD>`.
- **Beads:** each task below is one bead. Code tasks are `kind:task` with `metadata.design_approval=btq-96hm` and `metadata.adr_revision=e36f6d09563897198cb7641cad8c9e33bb02e6d3`, plus a blocking link to `btq-96hm`. Spikes (Tasks 2–5) and the gate (Task 6) are `kind:research`.
- **Commits:** at every green step. Nothing is pushed until Task 11, which pushes a branch and opens a PR. Merging it is the operator's action.

## File structure (this plan)

```
$HZ/
  LICENSE                                  kept from the old repo
  README.md                                Task 10
  pyproject.toml                           Task 7
  .gitignore                               Task 7
  .pre-commit-config.yaml                  Task 9
  .github/workflows/ci.yml                 Task 9
  src/heterodyne/__init__.py               Task 7
  src/heterodyne/platform.py               Task 7   platform seam (§3.2)
  src/heterodyne/config/__init__.py        Task 8   public API: load(), Config, ConfigError
  src/heterodyne/config/paths.py           Task 7   XDG / HETERODYNE_* locations
  src/heterodyne/config/layers.py          Task 8   read, merge, provenance, layer rules
  src/heterodyne/config/policy.py          Task 8   host policy and effective tiers
  src/heterodyne/defaults/defaults.toml    Task 8
  src/heterodyne/cli.py                    Tasks 7–8  `heterodyne platform|setup|config check`
  examples/config.toml                     Task 8
  examples/policy.toml                     Task 8
  examples/workstreams/example.toml        Task 8
  scripts/check_install_agnostic.py        Task 9
  tests/test_platform.py                   Task 7
  tests/test_paths.py                      Task 7
  tests/test_config.py                     Task 8
  tests/test_policy.py                     Task 8
  tests/test_check_install_agnostic.py     Task 9
  spikes/s3/{proxy.py,bridge.py,session_stub.py,launch.py,probes.sh}   Task 3
  docs/adr/0001-workstreams-v2.md          Task 1 (sanitised copy)
  docs/reviews/*.md                        Task 1 (sanitised copies)
  docs/spikes/S1-codex.md … S4-marmot.md   Tasks 2–5
  docs/spikes/GATE.md                      Task 6
  docs/superpowers/plans/*.md              Task 1
  docs/{install,configuration,security-model}.md   Task 10
$BTQ_REPO/
  bin/btq                                  Tasks 7b, 7c (below: "btq" tasks B1–B2)
  bin/approve-bead                         Task B2 (moved from the operator's ~/.local/bin)
  deploy/add-agent.py                      Task B1
  tests/test_queue.py                      Tasks B1–B2
  docs/PICKUP.md                           Task B1
```

Task order: 1 → 2–5 (spikes, in any order) → 6 (gate) → B1 → B2 → 7 → 8 → 9 → 10 → 11.

---

### Task 1: Fresh repo, wipe old contents, import design record

**Files:**
- Create: `$HZ` (new clone), branch `v2-replace`
- Delete: every tracked file except `LICENSE`
- Create: `docs/adr/0001-workstreams-v2.md`, `docs/reviews/*.md`, `docs/superpowers/plans/*.md` (sanitised copies)

**Interfaces:**
- Consumes: nothing.
- Produces: `$HZ` on branch `v2-replace`, containing only LICENSE and `docs/`. Later tasks commit onto this branch.

- [ ] **Step 1: Clone to a new path and check it is not the old harness**

```bash
test ! -e "$HZ" || { echo "\$HZ already exists; pick a new path"; exit 1; }
git clone https://github.com/Epiphytic/heterodyne-metaharness.git "$HZ"
cd "$HZ" && git remote -v && git log --oneline -1
```
Expected: remote `origin` is `Epiphytic/heterodyne-metaharness`, and `$HZ` is a real directory (`test ! -L "$HZ"`).

- [ ] **Step 2: Remove everything except LICENSE**

```bash
cd "$HZ" && git switch -c v2-replace
git ls-files | grep -v '^LICENSE$' | xargs git rm -q
git status --short | head; ls -A
```
Expected: only `.git` and `LICENSE` remain.

- [ ] **Step 3: Copy the design record and sanitise local paths**

```bash
mkdir -p docs/adr docs/reviews docs/superpowers/plans
cp "$DESIGN_REPO"/docs/adr/0001-workstreams-v2.md docs/adr/
cp "$DESIGN_REPO"/docs/reviews/*.md docs/reviews/
cp "$DESIGN_REPO"/docs/superpowers/plans/*.md docs/superpowers/plans/
python3 - <<'PY'
import os, pathlib, re
home = os.path.expanduser('~')
for p in pathlib.Path('docs').rglob('*.md'):
    s = p.read_text()
    t = re.sub(re.escape(home) + r'/repos/([A-Za-z0-9._-]+)', r'<repos>/\1', s)
    t = t.replace(home, '~')
    if p.parts[1] == 'reviews' and t != s:
        t = ('> Imported copy: local paths were normalised (`<repos>/`, `~`). The canonical, '
             'digest-pinned record is the design repo at commit `e36f6d0` (bead `btq-96hm`).\n\n' + t)
    p.write_text(t)
PY
grep -rn "$HOME" docs || echo "no home paths"
```
Expected: `no home paths`.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -qm "Replace v1 contents with v2 design record (ADR 0001 rev e36f6d0); keep LICENSE"
```

---

### Task 2: Spike S1 — Codex parity

**Files:**
- Create: `$HZ/docs/spikes/S1-codex.md`

**Interfaces:**
- Produces: answers that plan 4's `codex` adapter relies on: the pre-tool deny mechanism, hook trust, session naming, and live steering.

Use a scratch `CODEX_HOME` so the real config is never changed. Use `-c model_reasoning_effort=low` to keep costs down.

- [ ] **Step 1: Scratch home**

```bash
export S1=$(mktemp -d) && mkdir -p "$S1/codex" "$S1/work" && cp ~/.codex/auth.json "$S1/codex/" && chmod 600 "$S1/codex/auth.json"
export CODEX_HOME="$S1/codex"; cd "$S1/work" && git init -q
codex --version; codex --help > "$S1/help.txt"; codex exec --help >> "$S1/help.txt"; codex queue --help >> "$S1/help.txt"; codex resume --help >> "$S1/help.txt"; codex features list > "$S1/features.txt" 2>&1
```

- [ ] **Step 2: Q1: is there a pre-tool hook that can deny with a reason under `--dangerously-bypass-approvals-and-sandbox`?**
  - Check `features.txt` and the hooks documentation (`codex --help`, and `https://developers.openai.com/codex` through context7 or a web fetch) for any pre-tool-use event.
  - If one exists, configure a hook in `$CODEX_HOME/config.toml` that always denies with reason `S1-DENY`, then run `codex exec --dangerously-bypass-approvals-and-sandbox "run: touch denied.txt"`.
  - **Pass** if `denied.txt` does not exist and the transcript shows `S1-DENY`.
  - If there is no such hook, record **FAIL**. The ADR fallback applies (catch the sandbox failure and park the bead), so no ADR change is needed.

- [ ] **Step 3: Q2: does `--dangerously-bypass-hook-trust` make generated hooks run without per-hook trust hashes?**
  - Add a `session_start` hook that writes `$S1/hook-ran`, and run once without the flag and once with it.
  - Record which run executed the hook.

- [ ] **Step 4: Q3: can the session ID or name be set at launch?**
  - Using `help.txt`, try any `--name`, `--session-id` or `-c` option that sets the thread name. Otherwise, confirm that `codex resume <name>` works after renaming.
  - **Pass** if a deterministic name derived from `uuid5` can address the session.

- [ ] **Step 5: Q4: does `codex queue` deliver into a live interactive TUI?**

```bash
tmux new -d -s s1 -c "$S1/work" "CODEX_HOME=$CODEX_HOME codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low"
sleep 8; tmux send-keys -t s1 "Reply only with READY" Enter; sleep 20
THREAD=$(ls -t "$CODEX_HOME"/sessions/*/*/*/*.jsonl | head -1)   # locate the live session file
codex queue --thread "$(basename "$THREAD" .jsonl | sed 's/^rollout-[^-]*-[^-]*-[^-]*-[^-]*-//')" --message "Reply only with PONG"
sleep 25; tmux capture-pane -pt s1 | tail -20; tmux kill-session -t s1
```
  - If the thread-ID extraction doesn't match the file layout, use `codex queue --help` and record the real addressing scheme.
  - **Pass** if `PONG` appears in the pane without any send-keys.

- [ ] **Step 6: Write `docs/spikes/S1-codex.md`**
  - A table with columns `Question | Result (PASS/FAIL) | Evidence (command + trimmed output) | Consequence for plan 4`.
  - One row each for Q1–Q4, plus an "ADR impact" line. It is `none` unless a result contradicts §4.2 or §13.

- [ ] **Step 7: Commit, then clean up**

```bash
cd "$HZ" && git add docs/spikes/S1-codex.md && git commit -qm "Spike S1: Codex parity findings"
rm -rf "$S1"
```

---

### Task 3: Spike S3 — bubblewrap sandbox with the §7 self-test

**Files:**
- Create: `$HZ/spikes/s3/proxy.py`, `bridge.py`, `session_stub.py`, `launch.py`, `probes.sh`
- Create: `$HZ/docs/spikes/S3-sandbox.md`

**Interfaces:**
- Produces: a working `bwrap` argument set, the egress proxy design, the session-socket protocol stub, and the probe list. Plan 4 turns these into `heterodyne/sandbox/bubblewrap.py` and the launch self-test.
- The session socket protocol (a JSON line per request): `{"token": str, "type": "hook_event"|"ws_request", "payload": {...}}` → `{"ok": bool, "error"?: str}`.

- [ ] **Step 1: Egress proxy (outside the sandbox): `spikes/s3/proxy.py`**

```python
"""Allowlisting HTTP CONNECT proxy on a unix socket. Spike S3; not package code."""
import asyncio
import json
import sys


def allowed(host: str, allow: list[str]) -> bool:
    return any(host == a or (a.startswith('.') and host.endswith(a)) for a in allow)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def handle(reader, writer, allow):
    line = (await reader.readline()).decode('latin-1').strip()
    while (await reader.readline()) not in (b'\r\n', b'\n', b''):
        pass
    method, _, rest = line.partition(' ')
    target = rest.split(' ')[0]
    host, _, port = target.rpartition(':')
    ok = method == 'CONNECT' and port == '443' and allowed(host, allow)
    print(json.dumps({'method': method, 'target': target, 'allowed': ok}), file=sys.stderr, flush=True)
    if not ok:
        writer.write(b'HTTP/1.1 403 Forbidden\r\n\r\n')
        await writer.drain()
        writer.close()
        return
    up_r, up_w = await asyncio.open_connection(host, int(port))
    writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
    await writer.drain()
    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))


async def main(path: str, allow: list[str]) -> None:
    server = await asyncio.start_unix_server(lambda r, w: handle(r, w, allow), path=path)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1], sys.argv[2].split(',')))
```

- [ ] **Step 2: In-sandbox TCP→unix bridge and agent launcher: `spikes/s3/bridge.py`**

```python
"""Inside the sandbox: listen on 127.0.0.1:3128, forward to the bound proxy socket, run the agent."""  # install-agnostic: allow=ip-port (loopback address inside the sandbox netns, same on every install)
import os
import socket
import subprocess
import sys
import threading


def forward(a: socket.socket, b: socket.socket) -> None:
    try:
        while data := a.recv(65536):
            b.sendall(data)
    finally:
        b.close()


def serve(unix_path: str) -> None:
    listener = socket.create_server(('127.0.0.1', 3128))
    while True:
        client, _ = listener.accept()
        upstream = socket.socket(socket.AF_UNIX)
        upstream.connect(unix_path)
        threading.Thread(target=forward, args=(client, upstream), daemon=True).start()
        threading.Thread(target=forward, args=(upstream, client), daemon=True).start()


if __name__ == '__main__':
    threading.Thread(target=serve, args=(sys.argv[1],), daemon=True).start()
    env = dict(os.environ, HTTPS_PROXY='http://127.0.0.1:3128', HTTP_PROXY='http://127.0.0.1:3128',  # install-agnostic: allow=ip-port (loopback address inside the sandbox netns, same on every install)
               NO_PROXY='')
    sys.exit(subprocess.call(sys.argv[3:], env=env))   # argv: bridge.py <sock> -- <cmd...>
```

- [ ] **Step 3: Session socket stub (outside): `spikes/s3/session_stub.py`**

```python
"""Per-session socket: accepts only hook_event and ws_request carrying the launch token."""
import asyncio
import json
import sys

ALLOWED = {'hook_event', 'ws_request'}


async def handle(reader, writer, token, log):
    line = await reader.readline()
    try:
        req = json.loads(line)
        ok = req.get('token') == token and req.get('type') in ALLOWED
        reply = {'ok': ok} if ok else {'ok': False, 'error': 'forbidden'}
    except ValueError:
        reply = {'ok': False, 'error': 'malformed'}
    log.write(json.dumps({'req': line.decode(errors='replace').strip(), 'reply': reply}) + '\n')
    log.flush()
    writer.write((json.dumps(reply) + '\n').encode())
    await writer.drain()
    writer.close()


async def main(path, token, log_path):
    with open(log_path, 'a') as log:
        server = await asyncio.start_unix_server(lambda r, w: handle(r, w, token, log), path=path)
        async with server:
            await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main(*sys.argv[1:4]))
```

- [ ] **Step 4: Launcher: `spikes/s3/launch.py`**

```python
"""Build and run the bwrap command for one agent session. Spike S3."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

RO_SYSTEM = ['/usr', '/etc/ssl', '/etc/ca-certificates', '/etc/alternatives', '/etc/passwd',
             '/etc/group', '/etc/nsswitch.conf', '/etc/localtime']


def binary_roots(names: list[str]) -> list[str]:
    """Real install dirs of the agent CLIs (they may live under the real home)."""
    roots = set()
    for name in names:
        path = shutil.which(name)
        if path:
            real = Path(path).resolve()
            roots.add(str(real.parent.parent if real.parent.name == 'bin' else real.parent))
    return sorted(roots)


def argv(work: Path, home: Path, run: Path, cmd: list[str], agents: list[str]) -> list[str]:
    a = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session',
         '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
         '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
         '--symlink', 'usr/sbin', '/sbin']
    for p in RO_SYSTEM:
        if os.path.exists(p):
            a += ['--ro-bind', p, p]
    for p in binary_roots(agents):
        a += ['--ro-bind', p, p]
    a += ['--bind', str(home), str(home), '--setenv', 'HOME', str(home),
          '--bind', str(work), str(work), '--chdir', str(work),
          '--ro-bind', str(run), '/run/hz',
          '--setenv', 'HZ_SESSION_SOCKET', '/run/hz/session.sock',
          '--setenv', 'PATH', ':'.join(['/usr/bin', *[f'{r}/bin' for r in binary_roots(agents)], *binary_roots(agents)])]
    return a + ['--', 'python3', '/run/hz/bridge.py', '/run/hz/proxy.sock', '--', *cmd]


if __name__ == '__main__':
    work, home, run = (Path(p) for p in sys.argv[1:4])
    sys.exit(subprocess.call(argv(work, home, run, sys.argv[4:], ['claude', 'codex'])))
```

- [ ] **Step 5: Probes (run inside the sandbox): `spikes/s3/probes.sh`**

```bash
#!/bin/sh
# Usage (inside sandbox): probes.sh <real-home> <token>. Prints one PASS/FAIL line per probe.
REAL_HOME=$1; TOKEN=$2; rc=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1"; else echo "FAIL $1 (got $2, want $3)"; rc=1; fi; }
cat "$REAL_HOME/.hz-canary" >/dev/null 2>&1; check real-home-canary-unreadable $? 1
curl -s -o /dev/null --max-time 8 https://example.org; check non-allowlisted-host-blocked $([ $? -ne 0 ] && echo 1 || echo 0) 1
curl -s -o /dev/null --noproxy '*' --max-time 5 https://1.1.1.1; check direct-network-blocked $([ $? -ne 0 ] && echo 1 || echo 0) 1
r=$(printf '{"token":"%s","type":"approve","payload":{}}\n' "$TOKEN" | python3 -c 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect("/run/hz/session.sock");s.sendall(sys.stdin.buffer.read());print(s.makefile().readline().strip())')
check control-op-rejected "$r" '{"ok": false, "error": "forbidden"}'
test -e /run/hz/wsd.sock; check wsd-socket-absent $? 1
curl -s -o /dev/null --max-time 8 https://api.openai.com; check allowlisted-host-reachable $? 0
r=$(printf '{"token":"%s","type":"hook_event","payload":{}}\n' "$TOKEN" | python3 -c 'import socket,sys;s=socket.socket(socket.AF_UNIX);s.connect("/run/hz/session.sock");s.sendall(sys.stdin.buffer.read());print(s.makefile().readline().strip())')
check hook-event-accepted "$r" '{"ok": true}'
exit $rc
```

- [ ] **Step 6: Run the self-test**

```bash
cd "$HZ/spikes/s3" && export S3=$(mktemp -d) && mkdir -p "$S3/work" "$S3/home" "$S3/run"
echo canary > ~/.hz-canary
TOKEN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
cp bridge.py probes.sh "$S3/run/"
python3 proxy.py "$S3/run/proxy.sock" "api.openai.com,.openai.com,api.anthropic.com,.anthropic.com,.claude.ai" 2>"$S3/proxy.log" &
python3 session_stub.py "$S3/run/session.sock" "$TOKEN" "$S3/session.log" &
sleep 1; python3 launch.py "$S3/work" "$S3/home" "$S3/run" sh /run/hz/probes.sh "$HOME" "$TOKEN"
```
Expected: 7 `PASS` lines. Record any `FAIL` with a fix, or as a finding.

- [ ] **Step 7: Agent login and hooks inside the sandbox**
  1. Seed the synthetic home, per §7: `mkdir -p "$S3/home/.codex" "$S3/home/.claude" && cp ~/.codex/auth.json "$S3/home/.codex/" && cp ~/.claude/.credentials.json "$S3/home/.claude/" && cp ~/.claude.json "$S3/home/"`.
  2. `python3 launch.py "$S3/work" "$S3/home" "$S3/run" codex exec --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low "Reply only with OK"`. **Pass** if the output is `OK`.
  3. `python3 launch.py "$S3/work" "$S3/home" "$S3/run" claude -p --permission-mode bypassPermissions "Reply only with OK"`. **Pass** if the output is `OK`.
  4. Add a Claude `PreToolUse` hook to `$S3/home/.claude/settings.json` that pipes the event to the session socket with the token. Run `claude -p ... "run: echo hi"`. **Pass** if `$S3/session.log` shows the `hook_event` with `ok: true`.
  5. **Credential refresh question (ADR-relevant):** read the token expiry fields in the copied credential files (`expiresAt` in `.credentials.json`; `tokens`/`last_refresh` in `auth.json`). Record whether each CLI writes its credential file during a session: `stat` it before and after a run, and check `inotifywait` if available. If a CLI must write its credentials, then §7's "copied in read-only" needs an ADR amendment (for example, bind that one file read-write). Record it as a **finding**; do not change the ADR here.
  6. Check `proxy.log` and record the egress hosts each CLI needed. Plan 4 turns these into adapter defaults.

- [ ] **Step 8: Write `docs/spikes/S3-sandbox.md`**
  - The probe table (7 rows), the login and hook results, the list of required egress hosts per CLI, and the credential-write finding.
  - The bwrap argv as run, and an "ADR impact" line.

- [ ] **Step 9: Commit, then clean up**

```bash
kill %1 %2; rm -rf "$S3" ~/.hz-canary
cd "$HZ" && git add spikes/s3 docs/spikes/S3-sandbox.md && git commit -qm "Spike S3: bubblewrap sandbox prototype and self-test findings"
```

---

### Task 4: Spike S4 — Marmot threads, reactions and sender identity

**Files:**
- Create: `$HZ/docs/spikes/S4-marmot.md`

**Interfaces:**
- Produces: the exact `wn-agent` request and event shapes that plan 6 (router, outbox) and plan 2 (admind) use. That covers `send_final` with `reply_to_message_id_hex` and `idempotency_key`; `send_reaction`; inbound `reply_to`, `reaction_added` and `reaction_removed`; the sender pubkey field; and membership-change events.

- [ ] **Step 1: A second identity for admind, in a scratch data home**

```bash
export S4=$(mktemp -d); wn --home "$S4/admind" create-identity --help   # read flags, then:
wn --home "$S4/admind" create-identity
wn --home "$S4/admind" whoami --json | tee "$S4/admind.json"
```

- [ ] **Step 2: A test group between the harness identity and the admind identity**
  - Using the harness's `wn` home (the default one used by `wn-agent`), run `wn groups --help`, then create a group named `hz-s4-test` and invite the admind npub from `admind.json`.
  - Accept on the admind side (`wn --home "$S4/admind" groups ...`). Record the exact commands.

- [ ] **Step 3: Threads and reactions, both directions**
  1. Start `wn --json messages subscribe` for the harness identity, and a separate one for admind, each writing to a log file.
  2. The harness sends a card. Admind replies to it (`wn messages send --help` for the reply flag) and reacts 👍 (`wn messages react`).
  3. Then the reverse direction.
  4. **Pass** if each subscription log shows a reply event carrying `reply_to` (or `reply_to_message_id_hex`) equal to the card's ID, and a `reaction_added` event carrying the target ID and emoji.

- [ ] **Step 4: Idempotent send**
  - Send twice through the `wn-agent` control socket with the same `idempotency_key`. The request shape is the Hermes Marmot adapter's `send_final`: `{"type":"send_final","account_id_hex":…,"group_id_hex":…,"text":…,"reply_to_message_id_hex":null,"idempotency_key":"s4-1"}`.
  - **Pass** if exactly one message appears.

- [ ] **Step 5: Authenticated sender and membership changes (§3.4)**
  - Record which inbound event field carries the MLS-authenticated sender pubkey. It must come from the event metadata, not the text.
  - Add, then remove, a third identity (`wn --home "$S4/third" create-identity`). Record the membership-change event shapes that `wn-agent` emits. **Pass** if both are observable.

- [ ] **Step 6: Real-client check (operator, about 2 minutes)**
  - Ask the operator to join `hz-s4-test` from their phone, reply to one card and react to another.
  - **Pass** if the harness subscription shows both, and the operator confirms the thread rendered as a thread in their client.

- [ ] **Step 7: Write `docs/spikes/S4-marmot.md`, then commit**
  - Include the tables of request and event JSON shapes (trimmed, with IDs replaced by `<id>`, and no npubs), and the pass/fail per step.
  - Then leave the `hz-s4-test` group from both scratch identities.

```bash
cd "$HZ" && git add docs/spikes/S4-marmot.md && git commit -qm "Spike S4: Marmot threads, reactions, sender identity findings"
rm -rf "$S4"
```

---

### Task 5: Spike S2 — Claude channels (phase-2 gate, timeboxed to 2 hours, not blocking v1)

**Files:**
- Create: `$HZ/docs/spikes/S2-claude-channels.md`

- [ ] **Step 1: Capability check**
  - Run `claude --help | grep -i -A3 channel` and `claude mcp --help`.
  - Ask the `claude-code-guide` agent: "How do Claude Code channels (research preview) push messages into a live interactive session, what's the MCP server contract, and does it work under subscription (non-API-key) auth?"

- [ ] **Step 2: Minimal channel server**
  - If the contract is documented, write `spikes/s2/channel.py` (an MCP stdio server that emits one channel message: "Reply only with CHANNEL-OK"). Register it in a scratch `CLAUDE_CONFIG_DIR`, and start an interactive `claude` in tmux.
  - **Pass** if `CHANNEL-OK` appears without send-keys.

- [ ] **Step 3: Write the findings and commit**
  - Record PASS/FAIL/UNAVAILABLE and the consequences for phase 2. v1 uses `tmux send-keys` for the Claude reviewer regardless (§12).

```bash
cd "$HZ" && git add docs/spikes/S2-claude-channels.md spikes/s2 2>/dev/null; git commit -qm "Spike S2: Claude channels findings"
```

---

### Task 6: Spike gate

**Files:**
- Create: `$HZ/docs/spikes/GATE.md`

- [ ] **Step 1: Summarise**
  - Make one table: spike | result | ADR impact (none / amendment needed) | plan affected.
  - List every finding that contradicts the ADR at `e36f6d0`. S3's credential-write question is the likely candidate.

- [ ] **Step 2: Branch on the result**
  - **No ADR impact:** GATE.md says so. Plans 2–4 may be written.
  - **ADR impact:**
    1. Write the amendment in `$DESIGN_REPO`.
    2. Run the cross-model review rounds until APPROVE.
    3. Create a §5.9 approval bead (a full `metadata.ask`, pinned refs, `context_digest`) and stop until the operator approves it with `approve-bead`.
    4. Record the new approval bead ID and revision in GATE.md.

- [ ] **Step 3: Commit**

```bash
cd "$HZ" && git add docs/spikes/GATE.md && git commit -qm "Spike gate: results and ADR impact"
```

---

### Task B1: btq — the `wsd` identity and configurable locations

**Files:**
- Modify: `$BTQ_REPO/bin/btq` (constants block near the top; `load_policy`; `Queue.__init__`; `Queue.bd`)
- Create: `$BTQ_REPO/deploy/add-agent.py`
- Modify: `$BTQ_REPO/tests/test_queue.py`, `$BTQ_REPO/docs/PICKUP.md`

**Interfaces:**
- Produces:
  - `btq.locations(env: Mapping[str, str] = os.environ, **overrides) -> dict[str, str]`, with keys `config_dir`, `repo`, `dolt_host`, `dolt_port`, `dolt_database`.
  - `btq.Queue(agent, ws, session, **overrides)` accepts the same override keywords.
  - `'wsd' in btq.AGENTS`.
  - Plan 3 constructs `Queue('wsd', ws, uuid5(NS, f'{ws}:{bead}'))`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_queue.py`, before `if __name__`)

```python
class LocationTests(unittest.TestCase):
    def test_defaults_are_previous_hardcoded_values(self):
        loc = btq.locations({})
        self.assertEqual(loc['config_dir'], str(btq.CONFIG))
        self.assertEqual(loc['repo'], str(btq.REPO))
        self.assertEqual((loc['dolt_host'], loc['dolt_port'], loc['dolt_database']),
                         ('127.0.0.1', '3307', 'tasks'))

    def test_precedence_argument_then_env_then_default(self):
        env = {'BTQ_DOLT_PORT': '4000', 'BTQ_CONFIG_DIR': '/tmp/btq-cfg'}
        self.assertEqual(btq.locations(env)['dolt_port'], '4000')
        self.assertEqual(btq.locations(env, dolt_port=5000)['dolt_port'], '5000')
        self.assertEqual(btq.locations(env)['config_dir'], '/tmp/btq-cfg')

    def test_unknown_location_rejected(self):
        with self.assertRaises(TypeError):
            btq.locations({}, dolt_prot=1)

    def test_wsd_is_a_queue_agent(self):
        self.assertIn('wsd', btq.AGENTS)


class WsdPerBeadWorkerTests(unittest.TestCase):
    """ADR 0001 §4.3: one btq worker per bead lets wsd hold several claims at once."""
    def setUp(self):
        self.ws = 'test-' + uuid.uuid4().hex[:12]
        self.lister = btq.Queue('wsd', self.ws, uuid.uuid5(uuid.NAMESPACE_URL, self.ws).hex)
        self.ids = []

    def tearDown(self):
        for issue in self.ids:
            self.lister.bd('close', issue, '--reason', 'Integration fixture complete')

    def test_two_parked_claims_do_not_block_pickup(self):
        for _ in range(3):
            issue = self.lister.bd('create', 'wsd fixture', '--labels', f'agent:wsd,ws:{self.ws},kind:task',
                                   '--description', 'Isolated test fixture; no external work authorized.')
            self.ids.append(issue['id'])
        ready = {i['id'] for i in self.lister.ready()}
        self.assertEqual(ready, set(self.ids))
        for issue in self.ids:
            worker = btq.Queue('wsd', self.ws, uuid.uuid5(uuid.NAMESPACE_URL, f'{self.ws}:{issue}').hex)
            self.assertEqual(worker.claim(issue)['assignee'], worker.worker)
        self.assertEqual(self.lister.ready(), [])
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `cd "$BTQ_REPO" && python3 -m unittest tests.test_queue.LocationTests -v`
Expected: FAIL/ERROR with `AttributeError: module 'btq_test' has no attribute 'locations'`.

- [ ] **Step 3: Implement in `bin/btq`**

Replace the `AGENTS` and `POLICY` lines, and `load_policy`, with:

```python
AGENTS = ('bel', 'claude', 'codex', 'anubis', 'wsd')
# Locations default to today's values; BTQ_* env vars and Queue(...) keywords override them.
LOCATION_DEFAULTS = {'config_dir': str(CONFIG), 'repo': str(REPO), 'dolt_host': '127.0.0.1',
                     'dolt_port': '3307', 'dolt_database': 'tasks'}


def locations(env=os.environ, **overrides):
    unknown = set(overrides) - set(LOCATION_DEFAULTS)
    if unknown:
        raise TypeError(f'Unknown location(s): {", ".join(sorted(unknown))}')
    return {name: str(overrides.get(name) or env.get(f'BTQ_{name.upper()}') or default)
            for name, default in LOCATION_DEFAULTS.items()}


def load_policy(config_dir=CONFIG):
    # Approvers and the design-gate cutoff are operator policy, not code.
    path = Path(os.environ.get('BTQ_POLICY', Path(config_dir) / 'policy.json'))
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}  # Fail closed: no approvers, design gate applies to every task.
```

Replace the body of `Queue.__init__`, from `self.policy = load_policy()` through `self.env.pop(...)`, with:

```python
        loc = locations(**overrides)
        self.config, self.repo = Path(loc['config_dir']), Path(loc['repo'])
        self.policy = load_policy(self.config)
        secrets = json.loads((self.config / 'credentials.json').read_text())
        self.env = dict(os.environ, BEADS_DOLT_PASSWORD=secrets[agent],
                        BEADS_DOLT_SERVER_USER=agent, BEADS_DOLT_SERVER_TLS='true',
                        SSL_CERT_FILE=str(self.config / 'server.crt'), BEADS_ACTOR=self.worker,
                        BEADS_DIR=str(self.repo / '.beads'),
                        BEADS_DOLT_SERVER_HOST=loc['dolt_host'], BEADS_DOLT_SERVER_PORT=loc['dolt_port'],
                        BEADS_DOLT_SERVER_DATABASE=loc['dolt_database'])
        self.env.pop('BEADS_DOLT_CREDENTIAL_COMMAND', None)
```

Change the signature to `def __init__(self, agent, ws, session, **overrides):`. In `Queue.bd`, replace `str(REPO)` with `str(self.repo)`. Delete the old module-level `POLICY = ...` line.

- [ ] **Step 4: Run the unit tests and check they pass**

Run: `python3 -m unittest tests.test_queue.LocationTests -v`
Expected: 4 tests OK.

- [ ] **Step 5: `deploy/add-agent.py`, the operator step that creates the Dolt user and credentials**

```python
#!/usr/bin/env python3
"""Add one queue agent identity: a Dolt user plus a credentials entry. Never overwrites.

Run: uv run --with pymysql deploy/add-agent.py <name>
"""
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys

import pymysql

name = sys.argv[1] if len(sys.argv) == 2 else sys.exit(__doc__)
if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name):
    sys.exit('Invalid agent name')
root = Path(os.environ.get('BTQ_CONFIG_DIR', Path.home() / '.config/beads-task-queue'))
path = root / 'credentials.json'
creds = json.loads(path.read_text())
if name in creds:
    sys.exit(f'{name} already has credentials; rotate via OPS.md instead.')
creds[name] = secrets.token_hex(32)
os.umask(0o077)
tmp = path.with_suffix('.tmp')
tmp.write_text(json.dumps(creds, indent=2) + '\n')
os.replace(tmp, path)  # Saved before mutation, as bootstrap-db.py does.
tls = ssl.create_default_context(cafile=str(root / 'server.crt'))
with pymysql.connect(host=os.environ.get('BTQ_DOLT_HOST', '127.0.0.1'),
                     port=int(os.environ.get('BTQ_DOLT_PORT', '3307')), user='root',
                     password=creds['root'], ssl=tls, autocommit=True) as conn:
    with conn.cursor() as cur:
        cur.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s', (name, '%', creds[name]))
        cur.execute(f"GRANT ALL PRIVILEGES ON tasks.* TO '{name}'@'%'")
print(f'Added queue agent {name}')
```

Run: `cd "$BTQ_REPO" && uv run --with pymysql deploy/add-agent.py wsd`
Expected: `Added queue agent wsd`. Running it a second time gives `wsd already has credentials…`.

- [ ] **Step 6: Run the integration test and the full suite**

Run: `python3 -m unittest tests.test_queue.WsdPerBeadWorkerTests -v && python3 -m unittest discover -s tests -v`
Expected: all OK. There are three concurrent `wsd` claims, one per bead worker.

- [ ] **Step 7: PICKUP.md note** (append under "Approval representation reserved for Step 5")

```markdown
## `agent:wsd` beads

Beads labelled `agent:wsd` belong to heterodyne-metaharness (ADR 0001). The
`wsd` daemon lists work with its workstream session and claims each bead with
its own per-bead worker session, so no interactive agent should pick them up.
Locations can be overridden with `BTQ_CONFIG_DIR`, `BTQ_REPO`, `BTQ_DOLT_HOST`,
`BTQ_DOLT_PORT` and `BTQ_DOLT_DATABASE` (defaults unchanged).
```

- [ ] **Step 8: Commit, then cross-model review (Global Constraints), then fix and commit**

```bash
git add bin/btq deploy/add-agent.py tests/test_queue.py docs/PICKUP.md
git commit -qm "btq: add wsd agent identity and configurable locations (ADR 0001 §4.3)"
```

---

### Task B2: btq — the shared `context_digest` gate, and `approve-bead` in btq

**Files:**
- Modify: `$BTQ_REPO/bin/btq` (new functions after `review_valid`; `Queue.approval_valid`)
- Create: `$BTQ_REPO/bin/approve-bead` (moved from the operator's `~/.local/bin/approve-bead`, importing from btq)
- Modify: `$BTQ_REPO/tests/test_queue.py` (new `DigestTests`; update `test_design_gate_requires_two_llm_review_and_human_approval`)

**Interfaces:**
- Produces:
  - `btq.ask_digest(issue: dict, show: Callable[[str], dict]) -> tuple[str | None, list[str]]` returns `(sha256 hex, gaps)` over exactly the §5.9 canonical fields.
  - `Queue.approval_valid(approval, revision)` now also requires no gaps, a `file` ref pinned at `revision`, and `approved_digest` or `decided_digest` equal to the recomputed digest.
  - Plan 5's decision queue calls `btq.ask_digest` rather than reimplementing it.

- [ ] **Step 1: Write the failing tests** (append `DigestTests`; they need no database)

```python
class DigestTests(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', self.repo], check=True)
        Path(self.repo, 'adr.md').write_text('design v1\n')
        subprocess.run(['git', '-C', self.repo, 'add', '.'], check=True)
        subprocess.run(['git', '-C', self.repo, '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                        'commit', '-qm', 'v1'], check=True)
        self.rev = subprocess.check_output(['git', '-C', self.repo, 'rev-parse', 'HEAD'], text=True).strip()

    def issue(self, **meta):
        ask = {'effect': ['e'], 'excludes': ['x'], 'why': 'w', 'risks': ['r'],
               'refs': [{'kind': 'file', 'repo': self.repo, 'id': self.rev, 'path': 'adr.md'}]}
        return {'title': 'Approve design', 'description': 'd' * 300, 'status': 'closed',
                'metadata': {'ask': ask, 'adr_revision': self.rev, 'design_review': DESIGN, **meta}}

    def test_digest_ignores_decision_and_workflow_fields(self):
        base, gaps = btq.ask_digest(self.issue(), lambda _: {})
        self.assertEqual(gaps, [])
        noisy = self.issue(approved_by='Liam', decision='approve', context_digest='x', action_state='done')
        noisy['labels'] = ['needs-human']
        self.assertEqual(btq.ask_digest(noisy, lambda _: {})[0], base)

    def test_digest_changes_with_content(self):
        base = btq.ask_digest(self.issue(), lambda _: {})[0]
        changed = self.issue()
        changed['description'] += ' extra scope'
        self.assertNotEqual(btq.ask_digest(changed, lambda _: {})[0], base)

    def test_gaps_for_missing_ask_stub_and_floating_ref(self):
        self.assertTrue(btq.ask_digest({'title': 't', 'description': '', 'metadata': {}}, lambda _: {})[1])
        stub = self.issue(); stub['description'] = 'short'
        self.assertTrue(any('stub' in g for g in btq.ask_digest(stub, lambda _: {})[1]))
        floating = self.issue(); floating['metadata']['ask']['refs'][0]['id'] = 'main'
        self.assertTrue(any('floating' in g for g in btq.ask_digest(floating, lambda _: {})[1]))

    def test_approval_valid_requires_matching_digest_and_pinned_revision(self):
        q = btq.Queue('codex', 'test-digest', 'test-digest')
        q.policy = GATED
        digest = btq.ask_digest(self.issue(), q.show)[0]
        good = self.issue(approved_by='Liam', approved_at='T', approved_digest=digest)
        self.assertTrue(q.approval_valid(good, self.rev))
        self.assertFalse(q.approval_valid(self.issue(approved_by='Liam', approved_at='T'), self.rev))
        tampered = self.issue(approved_by='Liam', approved_at='T', approved_digest=digest)
        tampered['description'] += '!'
        self.assertFalse(q.approval_valid(tampered, self.rev))
        unpinned = self.issue(approved_by='Liam', approved_at='T')
        unpinned['metadata']['ask']['refs'] = [{'kind': 'commit', 'repo': self.repo, 'id': self.rev}]
        unpinned['metadata']['approved_digest'] = btq.ask_digest(unpinned, q.show)[0]
        self.assertFalse(q.approval_valid(unpinned, self.rev))
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `python3 -m unittest tests.test_queue.DigestTests -v`
Expected: ERROR `module 'btq_test' has no attribute 'ask_digest'`.

- [ ] **Step 3: Implement in `bin/btq`** (after `review_valid`; add `import hashlib` if it isn't already imported, which it is)

```python
# ADR 0001 §5.9: an approval is evidence only for pinned content that was shown in full.
ASK_FIELDS = ('effect', 'excludes', 'why', 'risks', 'refs')
REF_KINDS = ('commit', 'file', 'range', 'bead')
FULL_SHA = re.compile(r'[0-9a-f]{40}')
MIN_DESCRIPTION = 300


def _git(repo, *argv):
    result = subprocess.run(['git', '-C', os.path.expanduser(repo), *argv], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _resolve(ref, show):
    kind, repo, ref_id = ref.get('kind'), ref.get('repo', ''), ref.get('id', '')
    if kind not in REF_KINDS:
        return None, f'unsupported ref kind {kind!r}'
    if kind == 'bead':
        digest = (show(ref_id).get('metadata') or {}).get('context_digest')
        return (digest, None) if digest else (None, f'bead {ref_id} has no context_digest')
    ids = ref_id.split('..') if kind == 'range' else [ref_id]
    if not all(FULL_SHA.fullmatch(i) for i in ids):
        return None, f'{ref_id!r} is not a full commit SHA (floating refs are not allowed)'
    if kind == 'file':
        blob = _git(repo, 'rev-parse', f"{ref_id}:{ref.get('path', '')}")
        return (blob, None) if blob else (None, f"{ref.get('path')} not found at {ref_id[:12]}")
    found = [_git(repo, 'rev-parse', '--verify', f'{i}^{{commit}}') for i in ids]
    return ('..'.join(found), None) if all(found) else (None, f'{ref_id} not found in {repo}')


def ask_digest(issue, show):
    """Return (sha256 hex or None, gaps) over title, description, ask and resolved refs only."""
    ask = (issue.get('metadata') or {}).get('ask')
    if not isinstance(ask, dict):
        return None, ['metadata.ask is missing: effect, excludes, why, risks and pinned refs are required']
    gaps = []
    if len(issue.get('description', '').strip()) < MIN_DESCRIPTION:
        gaps.append(f'description is a stub (under {MIN_DESCRIPTION} characters of context)')
    gaps += [f'ask.{name} is empty' for name in ASK_FIELDS if not ask.get(name)]
    resolved = []
    for ref in ask.get('refs') or []:
        content, problem = _resolve(ref, show)
        if problem:
            gaps.append(f'ref: {problem}')
        resolved.append(content)
    canonical = json.dumps({'title': issue.get('title', ''), 'description': issue.get('description', ''),
                            'ask': ask, 'resolved': resolved},
                           sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest(), gaps
```

Replace `Queue.approval_valid` with:

```python
    def approval_valid(self, approval, revision):
        proof = approval.get('metadata') or {}
        digest, gaps = ask_digest(approval, self.show)
        pinned = any(ref.get('kind') == 'file' and ref.get('id') == revision
                     for ref in (proof.get('ask') or {}).get('refs') or [])
        return (approval.get('status') == 'closed'
                and proof.get('approved_by') in self.policy.get('approvers', [])
                and bool(proof.get('approved_at')) and bool(revision)
                and proof.get('adr_revision') == revision
                and review_valid(proof.get('design_review', ''))
                and not gaps and pinned
                and (proof.get('approved_digest') or proof.get('decided_digest')) == digest)
```

- [ ] **Step 4: Update the existing design-gate integration test for the new evidence**

In `test_design_gate_requires_two_llm_review_and_human_approval`, replace the lines from `approval = self.q.bd('create', ...` to just before the `for key, value in` loop with:

```python
            repo = tempfile.mkdtemp()
            subprocess.run(['git', 'init', '-q', repo], check=True)
            Path(repo, 'adr.md').write_text('fixture design\n')
            subprocess.run(['git', '-C', repo, 'add', '.'], check=True)
            subprocess.run(['git', '-C', repo, '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                            'commit', '-qm', 'fixture'], check=True)
            rev = subprocess.check_output(['git', '-C', repo, 'rev-parse', 'HEAD'], text=True).strip()
            approval = self.q.bd('create', 'Synthetic approval fixture', '--type', 'decision',
                                 '--description', 'Synthetic test only, not a real approval. ' * 10)
            self.ids.append(approval['id'])
            ask = {'effect': ['test'], 'excludes': ['everything real'], 'why': 'fixture', 'risks': ['none'],
                   'refs': [{'kind': 'file', 'repo': repo, 'id': rev, 'path': 'adr.md'}]}
            self.q.bd('update', approval['id'], '--metadata', json.dumps({'ask': ask}))
            digest = btq.ask_digest(self.q.show(approval['id']), self.q.show)[0]
            proof = {'ask': ask, 'approved_by': 'Liam', 'approved_at': 'TEST ONLY', 'adr_revision': rev,
                     'design_review': DESIGN, 'approved_digest': digest}
            self.q.bd('update', approval['id'], '--metadata', json.dumps(proof))
            self.q.bd('update', issue, '--metadata', json.dumps(
                {'design_approval': approval['id'], 'adr_revision': rev}))
            self.assertEqual(queue.ready(), [])
            self.q.bd('close', approval['id'], '--reason', 'Synthetic test only, not real approval')
            self.assertEqual([i['id'] for i in queue.ready()], [issue])
```

Add `('approved_digest', 'f' * 64)` to the tuple of mutations in the existing `for key, value in (...)` loop.

- [ ] **Step 5: Run everything**

Run: `python3 -m unittest discover -s tests -v`
Expected: all OK.

- [ ] **Step 6: Move `approve-bead` into btq and use the shared function**

```bash
cp ~/.local/bin/approve-bead "$BTQ_REPO/bin/approve-bead"
```

Then edit `$BTQ_REPO/bin/approve-bead`:
- Set `BTQ = Path(__file__).resolve().parent / 'btq'`, so it is repo-relative with no home path.
- Delete its local `ASK_FIELDS`, `REF_KINDS`, `SHA`, `MIN_DESCRIPTION`, `git`, `resolve` and `check_ask` definitions.
- Replace both calls to `check_ask(...)` with `btq.ask_digest(...)`.
- In the `--doc` branch, replace `git(ref.get('repo', ''), 'show', ...)` with `btq._git(ref.get('repo', ''), 'show', ...)`.

Finally, link it: `ln -sf "$BTQ_REPO/bin/approve-bead" ~/.local/bin/approve-bead`.

Run: `approve-bead btq-96hm --dry-run --yes | tail -2`
Expected: `Already closed; nothing to do.`

Run: `python3 -c "import importlib.machinery as m; b=m.SourceFileLoader('b','$BTQ_REPO/bin/btq').load_module(); q=b.Queue('claude','x','x'); print(q.approval_valid(q.show('btq-96hm'), 'e36f6d09563897198cb7641cad8c9e33bb02e6d3'))"`
Expected: `True`. The real approval passes the new gate.

- [ ] **Step 7: Commit, then cross-model review, then fix and commit**

```bash
git add bin/btq bin/approve-bead tests/test_queue.py
git commit -qm "btq: shared context_digest gate for approvals; move approve-bead into btq (ADR 0001 §5.9)"
```

---

### Task 7: Package skeleton, platform seam, locations

**Files:**
- Create: `$HZ/pyproject.toml`, `$HZ/.gitignore`, `$HZ/src/heterodyne/__init__.py`, `$HZ/src/heterodyne/platform.py`, `$HZ/src/heterodyne/config/paths.py`, `$HZ/src/heterodyne/cli.py`
- Test: `$HZ/tests/test_platform.py`, `$HZ/tests/test_paths.py`

**Interfaces:**
- Produces:
  - `heterodyne.platform.detect(platform: str = sys.platform) -> Literal['linux','macos']` (raises `UnsupportedPlatform`).
  - `heterodyne.platform.backends(os_name) -> dict[str, str]`, with keys `service_manager` and `sandbox`.
  - `heterodyne.platform.boot_id(os_name) -> str`.
  - `heterodyne.config.paths.config_dir(env: Mapping[str,str]) -> Path` and `state_dir(env) -> Path`.
  - The console script `heterodyne` (`heterodyne platform`).

- [ ] **Step 1: `pyproject.toml` and `.gitignore`**

```toml
[project]
name = "heterodyne-metaharness"
version = "0.1.0"
description = "Deterministic control plane for sandboxed, externally run coding agents, steered over Marmot"
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
heterodyne = "heterodyne.cli:main"

[dependency-groups]
dev = ["pytest>=8", "hypothesis>=6.100", "ruff>=0.6", "pyright>=1.1.380"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/heterodyne"]

[tool.ruff]
line-length = 110
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "PTH"]
ignore = ["S603", "S607"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]
"spikes/**" = ["S", "PTH", "B"]

[tool.pyright]
include = ["src", "tests"]
strict = ["src"]
pythonVersion = "3.12"
```

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
dist/
.pytest_cache/
.ruff_cache/
# Host layers and state never live in the repo (ADR 0001 §15).
/config.toml
/policy.toml
/workstreams/
/state/
*.local.toml
leakcheck.txt
```

- [ ] **Step 2: Write the failing tests**

`tests/test_platform.py`:

```python
import pytest

from heterodyne import platform


def test_detect_maps_supported_platforms() -> None:
    assert platform.detect("linux") == "linux"
    assert platform.detect("darwin") == "macos"


def test_detect_rejects_unsupported() -> None:
    with pytest.raises(platform.UnsupportedPlatform):
        platform.detect("win32")


def test_backends_per_platform() -> None:
    assert platform.backends("linux") == {"service_manager": "systemd", "sandbox": "bubblewrap"}
    assert platform.backends("macos") == {"service_manager": "launchd", "sandbox": "seatbelt"}


def test_boot_id_is_stable_within_a_boot() -> None:
    os_name = platform.detect()
    assert platform.boot_id(os_name) == platform.boot_id(os_name) != ""
```

`tests/test_paths.py`:

```python
from pathlib import Path

from heterodyne.config import paths


def test_explicit_env_wins() -> None:
    env = {"HETERODYNE_CONFIG_DIR": "/x/cfg", "XDG_CONFIG_HOME": "/y", "HOME": "/h"}
    assert paths.config_dir(env) == Path("/x/cfg")


def test_xdg_then_home_default() -> None:
    assert paths.config_dir({"XDG_CONFIG_HOME": "/y", "HOME": "/h"}) == Path("/y/heterodyne")
    assert paths.config_dir({"HOME": "/h"}) == Path("/h/.config/heterodyne")
    assert paths.state_dir({"HOME": "/h"}) == Path("/h/.local/state/heterodyne")
    assert paths.state_dir({"XDG_STATE_HOME": "/s", "HOME": "/h"}) == Path("/s/heterodyne")
```

- [ ] **Step 3: Run the tests and check they fail**

Run: `cd "$HZ" && uv sync && uv run pytest -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'heterodyne'`.

- [ ] **Step 4: Implement**

`src/heterodyne/__init__.py`:

```python
"""heterodyne-metaharness: deterministic control plane for sandboxed coding agents (ADR 0001)."""

__version__ = "0.1.0"
```

`src/heterodyne/platform.py`:

```python
"""Platform seam (ADR 0001 §3.2). The only module allowed to read sys.platform."""

import subprocess
import sys
from pathlib import Path
from typing import Literal

OsName = Literal["linux", "macos"]

_BACKENDS: dict[OsName, dict[str, str]] = {
    "linux": {"service_manager": "systemd", "sandbox": "bubblewrap"},
    "macos": {"service_manager": "launchd", "sandbox": "seatbelt"},
}


class UnsupportedPlatform(RuntimeError):
    pass


def detect(platform: str = sys.platform) -> OsName:
    if platform.startswith("linux"):
        return "linux"
    if platform == "darwin":
        return "macos"
    raise UnsupportedPlatform(f"{platform} is not supported (Linux and macOS only)")


def backends(os_name: OsName) -> dict[str, str]:
    return dict(_BACKENDS[os_name])


def boot_id(os_name: OsName) -> str:
    if os_name == "linux":
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                          check=True).stdout.strip()
```

`src/heterodyne/config/__init__.py` (a temporary stub; Task 8 replaces it):

```python
"""Configuration layering (ADR 0001 §15)."""
```

`src/heterodyne/config/paths.py`:

```python
"""Host config and state locations (ADR 0001 §15). Environment overrides cover locations only."""

from collections.abc import Mapping
from pathlib import Path


def _home(env: Mapping[str, str]) -> Path:
    return Path(env["HOME"]) if env.get("HOME") else Path.home()


def config_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_CONFIG_DIR"):
        return Path(env["HETERODYNE_CONFIG_DIR"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else _home(env) / ".config"
    return base / "heterodyne"


def state_dir(env: Mapping[str, str]) -> Path:
    if env.get("HETERODYNE_STATE_DIR"):
        return Path(env["HETERODYNE_STATE_DIR"]).expanduser()
    base = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else _home(env) / ".local" / "state"
    return base / "heterodyne"
```

`src/heterodyne/cli.py`:

```python
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
    sub.add_parser("platform", help="show the detected platform and its backends").set_defaults(func=cmd_platform)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests and lint**

Run: `uv run pytest -q && uv run ruff check && uv run pyright && uv run heterodyne platform`
Expected: 6 passed; ruff and pyright clean; JSON output with `"os": "linux"`, `"sandbox": "bubblewrap"`.

- [ ] **Step 6: Commit, then cross-model review, then fix and commit**

```bash
git add pyproject.toml uv.lock .gitignore src tests && git commit -qm "Package skeleton with platform seam and host locations (ADR 0001 §3.2, §15)"
```

---

### Task 8: Config layering, host-only policy, `[restrict]`, `heterodyne setup` / `config check`

**Files:**
- Create: `$HZ/src/heterodyne/config/layers.py`, `$HZ/src/heterodyne/config/policy.py`, `$HZ/src/heterodyne/defaults/defaults.toml`, `$HZ/examples/config.toml`, `$HZ/examples/policy.toml`, `$HZ/examples/workstreams/example.toml`
- Modify: `$HZ/src/heterodyne/config/__init__.py`, `$HZ/src/heterodyne/cli.py`
- Test: `$HZ/tests/test_config.py`, `$HZ/tests/test_policy.py`

**Interfaces:**
- Consumes: `paths.config_dir`, `paths.state_dir`, and `platform.detect`/`backends` (Task 7).
- Produces:
  - `heterodyne.config.load(workstream: str | None = None, env: Mapping[str, str] = os.environ) -> Config`.
  - `Config.values: dict[str, Any]`, `Config.sources: dict[str, str]` (dotted key → `"defaults"` / `"host:config.toml"` / `"workstream:<ws>.toml"` / `"env:<VAR>"`), `Config.policy: Policy`, and `Config.get(dotted: str, default=None)`.
  - `ConfigError(ValueError)`.
  - `heterodyne.config.policy.Policy` (frozen dataclass): `approvers: tuple[str, ...]`, `identities: dict[str, dict[str, str]]`, `tiers: dict[str, str]` (action class → `auto_approve|escalate|hard_deny`).
  - `effective_tiers(default_tiers, host_overrides, restrict) -> dict[str, str]`.
  - Plan 5's policy engine reads `Config.policy.tiers`. Later plans add typed schemas for their own keys; this task enforces the **layer rules** only.

- [ ] **Step 1: Defaults and examples**

`src/heterodyne/defaults/defaults.toml`:

```toml
# Built-in defaults (ADR 0001 §15 layer 1). Adapters are defined; no models are chosen.

[adapters]
known = ["claude-code", "codex"]

[review]
mode_when_same_model = "adversarial"   # or "block"

[timeouts]
gatekeeper_seconds = 60
hook_wait_seconds = 5
reminder_hours = 4
delivery_alert_minutes = 10

# Action classes by tier (§5.3). Host policy may re-tier classes, except `locked` ones,
# which can never be lowered. Workstreams may only tighten, via [restrict].
[tiers]
auto_approve = ["worktree_edit", "run_tests", "local_git", "dependency_install"]
escalate = ["push_branch", "open_pr", "merge_pr", "deploy", "notify", "new_egress_host"]
hard_deny = ["modify_harness_config", "modify_policy", "modify_sandbox"]
locked = ["modify_harness_config", "modify_policy", "modify_sandbox"]
```

`examples/config.toml`:

```toml
# Example host config. `heterodyne setup` copies this to $HETERODYNE_CONFIG_DIR/config.toml.
# Replace every <placeholder>. Policy (approvers, identities, tiers) goes in policy.toml, not here.

[platform]
os = "<linux-or-macos>"          # written by `heterodyne setup`

# Profiles: adapter + model + launch options (§4.1). One profile is enough; two different
# models are recommended so reviews are cross-model rather than adversarial (§11.1).
[profiles.coder]
adapter = "codex"
model = "<model-name>"

[profiles.reviewer]
adapter = "claude-code"
model = "<model-name>"

[roles]
coder = "coder"
reviewer = "reviewer"

[sandbox]
egress_approved = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]

[integrations.beads]
btq = "<path-to-btq>"

[integrations.marmot]
socket = "<path-to-wn-agent-socket>"
auth_token = { command = "<command-that-prints-the-token>" }
```

`examples/policy.toml`:

```toml
# Example host policy. Host-only: no other layer may set these keys (ADR 0001 §15).
approvers = ["<approver-name>"]

[identities."<approver-name>"]    # quoted: <> is not valid in a bare TOML key
marmot_npub = "<npub>"
github = "<github-login>"
radicle_did = "<did>"

# Optional re-tiering of action classes (locked classes cannot be lowered).
[tiers]
# new_egress_host = "hard_deny"
```

`examples/workstreams/example.toml`:

```toml
# Example workstream config. Allowed tables: roles, repos, sandbox, cron, render, timeouts, restrict.

[roles]
coder = "coder"                   # must name a host profile

[repos]
main = "<path-to-repo>"

[sandbox]
extra_ro_mounts = []
extra_egress = ["pypi.org"]       # must be listed in the host's sandbox.egress_approved

[restrict]                        # tightening only
escalate = ["dependency_install"]
hard_deny = []
```

- [ ] **Step 2: Write the failing tests**

`tests/test_policy.py`:

```python
import pytest
from hypothesis import given
from hypothesis import strategies as st

from heterodyne.config import ConfigError
from heterodyne.config.policy import RANK, effective_tiers

DEFAULTS = {
    "auto_approve": ["worktree_edit", "run_tests"],
    "escalate": ["push_branch"],
    "hard_deny": ["modify_policy"],
    "locked": ["modify_policy"],
}
CLASSES = ["worktree_edit", "run_tests", "push_branch", "modify_policy"]


def test_defaults_only() -> None:
    assert effective_tiers(DEFAULTS, {}, {})["run_tests"] == "auto_approve"


def test_host_may_retier_but_not_lower_locked() -> None:
    assert effective_tiers(DEFAULTS, {"push_branch": "auto_approve"}, {})["push_branch"] == "auto_approve"
    with pytest.raises(ConfigError, match="locked"):
        effective_tiers(DEFAULTS, {"modify_policy": "escalate"}, {})


def test_restrict_tightens() -> None:
    tiers = effective_tiers(DEFAULTS, {}, {"escalate": ["run_tests"], "hard_deny": ["push_branch"]})
    assert (tiers["run_tests"], tiers["push_branch"]) == ("escalate", "hard_deny")


def test_restrict_cannot_relax_or_name_unknown_classes() -> None:
    with pytest.raises(ConfigError, match="relax"):
        effective_tiers(DEFAULTS, {}, {"escalate": ["modify_policy"]})
    with pytest.raises(ConfigError, match="unknown"):
        effective_tiers(DEFAULTS, {}, {"hard_deny": ["typo_class"]})
    with pytest.raises(ConfigError, match="restrict"):
        effective_tiers(DEFAULTS, {}, {"allow": ["push_branch"]})


@given(
    esc=st.lists(st.sampled_from(CLASSES)),
    deny=st.lists(st.sampled_from(CLASSES)),
)
def test_restrict_never_less_strict_than_host(esc: list[str], deny: list[str]) -> None:
    host = effective_tiers(DEFAULTS, {}, {})
    try:
        tightened = effective_tiers(DEFAULTS, {}, {"escalate": esc, "hard_deny": deny})
    except ConfigError:
        return
    assert all(RANK[tightened[c]] >= RANK[host[c]] for c in CLASSES)
```

`tests/test_config.py`:

```python
import shutil
from pathlib import Path

import pytest

from heterodyne.config import ConfigError, load

ROOT = Path(__file__).resolve().parent.parent


def write(d: Path, name: str, text: str) -> None:
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


@pytest.fixture
def cfg(tmp_path: Path) -> Path:
    write(tmp_path, "config.toml", """
[profiles.a]
adapter = "codex"
model = "m1"
[profiles.b]
adapter = "claude-code"
model = "m2"
[roles]
coder = "a"
[sandbox]
egress_approved = ["pypi.org"]
[timeouts]
reminder_hours = 8
""")
    write(tmp_path, "policy.toml", 'approvers = ["op"]\n')
    return tmp_path


def env(d: Path, **extra: str) -> dict[str, str]:
    return {"HETERODYNE_CONFIG_DIR": str(d), "HOME": str(d), **extra}


def test_precedence_and_sources(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[roles]\ncoder = "b"\n[timeouts]\nhook_wait_seconds = 9\n')
    c = load("w", env(cfg, HETERODYNE_LOG_LEVEL="debug"))
    assert c.get("timeouts.gatekeeper_seconds") == 60 and c.sources["timeouts.gatekeeper_seconds"] == "defaults"
    assert c.get("timeouts.reminder_hours") == 8 and c.sources["timeouts.reminder_hours"] == "host:config.toml"
    assert c.get("roles.coder") == "b" and c.sources["roles.coder"] == "workstream:w.toml"
    assert c.get("debug.log_level") == "debug" and c.sources["debug.log_level"] == "env:HETERODYNE_LOG_LEVEL"
    assert c.policy.approvers == ("op",)


def test_policy_keys_rejected_outside_policy_toml(cfg: Path) -> None:
    write(cfg, "config.toml", (cfg / "config.toml").read_text() + '\napprovers = ["x"]\n')
    with pytest.raises(ConfigError, match="policy.toml"):
        load(None, env(cfg))


def test_workstream_rejects_policy_and_unknown_keys(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[tiers]\npush_branch = "auto_approve"\n')
    with pytest.raises(ConfigError, match="not allowed"):
        load("w", env(cfg))
    write(cfg, "workstreams/w.toml", 'approvers = ["x"]\n')
    with pytest.raises(ConfigError, match="not allowed"):
        load("w", env(cfg))


def test_workstream_role_must_name_host_profile(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[roles]\ncoder = "nope"\n')
    with pytest.raises(ConfigError, match="profile"):
        load("w", env(cfg))


def test_profile_adapter_must_be_known(cfg: Path) -> None:
    write(cfg, "config.toml", '[profiles.a]\nadapter = "mystery"\n')
    with pytest.raises(ConfigError, match="adapter"):
        load(None, env(cfg))


def test_extra_egress_must_be_host_approved(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[sandbox]\nextra_egress = ["evil.example"]\n')
    with pytest.raises(ConfigError, match="egress_approved"):
        load("w", env(cfg))


def test_inline_secret_rejected_reference_accepted(cfg: Path) -> None:
    base = (cfg / "config.toml").read_text()
    write(cfg, "config.toml", base + '[integrations.marmot]\nauth_token = "abc123"\n')
    with pytest.raises(ConfigError, match="secret"):
        load(None, env(cfg))
    write(cfg, "config.toml", base + '[integrations.marmot]\nauth_token = { command = "pass show x" }\n')
    assert load(None, env(cfg)).get("integrations.marmot.auth_token") == {"command": "pass show x"}


def test_unknown_env_var_rejected(cfg: Path) -> None:
    with pytest.raises(ConfigError, match="HETERODYNE_ROLES"):
        load(None, env(cfg, HETERODYNE_ROLES="x"))


def test_restrict_applies_to_effective_policy(cfg: Path) -> None:
    write(cfg, "workstreams/w.toml", '[restrict]\nescalate = ["run_tests"]\n')
    assert load("w", env(cfg)).policy.tiers["run_tests"] == "escalate"
    assert load(None, env(cfg)).policy.tiers["run_tests"] == "auto_approve"


def test_missing_policy_fails_closed(cfg: Path) -> None:
    (cfg / "policy.toml").unlink()
    assert load(None, env(cfg)).policy.approvers == ()


def test_examples_load(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "examples", tmp_path, dirs_exist_ok=True)
    text = (tmp_path / "config.toml").read_text().replace("<linux-or-macos>", "linux")
    (tmp_path / "config.toml").write_text(text)
    assert load("example", env(tmp_path)).get("roles.coder") == "coder"
```

- [ ] **Step 3: Run the tests and check they fail**

Run: `uv run pytest -q tests/test_config.py tests/test_policy.py`
Expected: `ImportError: cannot import name 'ConfigError'`.

- [ ] **Step 4: Implement `config/policy.py`**

```python
"""Host-only policy and effective action tiers (ADR 0001 §5.3, §15)."""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RANK = {"auto_approve": 0, "escalate": 1, "hard_deny": 2}
POLICY_KEYS = frozenset({"approvers", "identities", "operators", "tiers", "hard_deny_rules",
                         "action_registry", "tier_floor", "policy"})
RESTRICT_KEYS = frozenset({"escalate", "hard_deny"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    approvers: tuple[str, ...] = ()
    identities: dict[str, dict[str, str]] = field(default_factory=dict[str, dict[str, str]])
    tiers: dict[str, str] = field(default_factory=dict[str, str])


def effective_tiers(default_tiers: Mapping[str, Sequence[str]], host_overrides: Mapping[str, str],
                    restrict: Mapping[str, Sequence[str]]) -> dict[str, str]:
    tier = {c: t for t in RANK for c in default_tiers.get(t, [])}
    locked = set(default_tiers.get("locked", []))
    for cls, target in host_overrides.items():
        if cls not in tier or target not in RANK:
            raise ConfigError(f"policy.toml [tiers]: unknown class or tier {cls} = {target!r}")
        if cls in locked and RANK[target] < RANK[tier[cls]]:
            raise ConfigError(f"policy.toml [tiers]: {cls} is locked and cannot be lowered")
        tier[cls] = target
    unknown = set(restrict) - RESTRICT_KEYS
    if unknown:
        raise ConfigError(f"[restrict] allows only {sorted(RESTRICT_KEYS)}, not {sorted(unknown)}")
    for key in ("escalate", "hard_deny"):
        for cls in restrict.get(key, []):
            if cls not in tier:
                raise ConfigError(f"[restrict].{key}: unknown class {cls}")
            if RANK[key] < RANK[tier[cls]]:
                raise ConfigError(f"[restrict].{key} would relax {cls} (currently {tier[cls]})")
            tier[cls] = key
    return tier


def load_policy(path: Path, default_tiers: Mapping[str, Sequence[str]],
                restrict: Mapping[str, Sequence[str]]) -> Policy:
    raw: dict[str, Any] = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(raw) - POLICY_KEYS
    if unknown:
        raise ConfigError(f"policy.toml: unknown keys {sorted(unknown)}")
    return Policy(approvers=tuple(raw.get("approvers", ())),
                  identities=dict(raw.get("identities", {})),
                  tiers=effective_tiers(default_tiers, raw.get("tiers", {}), restrict))
```

- [ ] **Step 5: Implement `config/layers.py`**

```python
"""Read and merge config layers with provenance, enforcing the §15 layer rules."""

import re
import tomllib
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from heterodyne.config.policy import POLICY_KEYS, ConfigError

WORKSTREAM_KEYS = frozenset({"roles", "repos", "sandbox", "cron", "render", "timeouts", "restrict"})
WORKSTREAM_SANDBOX_KEYS = frozenset({"extra_ro_mounts", "extra_egress"})
ENV_KEYS = {"HETERODYNE_CONFIG_DIR": ("paths", "config_dir"),
            "HETERODYNE_STATE_DIR": ("paths", "state_dir"),
            "HETERODYNE_LOG_LEVEL": ("debug", "log_level")}
SECRET_NAME = re.compile(r"password|passwd|token|secret|nsec|private_key|api_key", re.IGNORECASE)


def read_defaults() -> dict[str, Any]:
    text = resources.files("heterodyne").joinpath("defaults/defaults.toml").read_text()
    return tomllib.loads(text)


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text()) if path.exists() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc


def merge(base: dict[str, Any], overlay: Mapping[str, Any], label: str, sources: dict[str, str],
          prefix: str = "") -> None:
    for key, value in overlay.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping) and isinstance(base.get(key), dict) and not _is_secret_ref(value):
            merge(base[key], value, label, sources, dotted + ".")
        elif isinstance(value, Mapping) and not _is_secret_ref(value):
            base[key] = {}
            merge(base[key], value, label, sources, dotted + ".")
        else:
            base[key] = value
            sources[dotted] = label


def env_layer(env: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    layer: dict[str, Any] = {}
    labels: dict[str, str] = {}
    for var, value in env.items():
        if not var.startswith("HETERODYNE_"):
            continue
        if var not in ENV_KEYS:
            raise ConfigError(f"{var}: environment overrides are limited to {sorted(ENV_KEYS)}")
        table, key = ENV_KEYS[var]
        layer.setdefault(table, {})[key] = value
        labels[f"{table}.{key}"] = f"env:{var}"
    return layer, labels


def check_host(host: Mapping[str, Any]) -> None:
    misplaced = set(host) & POLICY_KEYS
    if misplaced:
        raise ConfigError(f"config.toml: {sorted(misplaced)} belong in policy.toml")


def check_workstream(name: str, ws: Mapping[str, Any], merged_host: Mapping[str, Any]) -> None:
    bad = set(ws) - WORKSTREAM_KEYS
    if bad:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(bad)} not allowed in a workstream "
                          f"(allowed: {sorted(WORKSTREAM_KEYS)})")
    profiles = merged_host.get("profiles", {})
    for role, profile in ws.get("roles", {}).items():
        if profile not in profiles:
            raise ConfigError(f"workstreams/{name}.toml: role {role} names unknown profile {profile!r}")
    sandbox = ws.get("sandbox", {})
    bad_sandbox = set(sandbox) - WORKSTREAM_SANDBOX_KEYS
    if bad_sandbox:
        raise ConfigError(f"workstreams/{name}.toml: [sandbox] {sorted(bad_sandbox)} not allowed")
    approved = set(merged_host.get("sandbox", {}).get("egress_approved", []))
    extra = set(sandbox.get("extra_egress", [])) - approved
    if extra:
        raise ConfigError(f"workstreams/{name}.toml: {sorted(extra)} not in host sandbox.egress_approved")


def check_profiles(merged: Mapping[str, Any]) -> None:
    known = set(merged.get("adapters", {}).get("known", []))
    for name, profile in merged.get("profiles", {}).items():
        if profile.get("adapter") not in known:
            raise ConfigError(f"profiles.{name}: adapter {profile.get('adapter')!r} is not one of {sorted(known)}")


def _is_secret_ref(value: Mapping[str, Any]) -> bool:
    return len(value) == 1 and next(iter(value)) in ("file", "command")


def check_secrets(tree: Mapping[str, Any], prefix: str = "") -> None:
    for key, value in tree.items():
        dotted = f"{prefix}{key}"
        if SECRET_NAME.search(key) and not (isinstance(value, Mapping) and _is_secret_ref(value)):
            raise ConfigError(f"{dotted}: secrets must be a reference, {{ file = ... }} or {{ command = ... }}")
        if isinstance(value, Mapping) and not _is_secret_ref(value):
            check_secrets(value, dotted + ".")
```

- [ ] **Step 6: Implement `config/__init__.py`**

```python
"""Configuration layering (ADR 0001 §15): defaults -> host -> workstream -> env, with host-only policy."""

import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from heterodyne.config import layers, paths
from heterodyne.config.policy import ConfigError, Policy, load_policy

__all__ = ["Config", "ConfigError", "Policy", "load"]


@dataclass(frozen=True)
class Config:
    values: dict[str, Any]
    sources: dict[str, str]
    policy: Policy

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.values
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load(workstream: str | None = None, env: Mapping[str, str] = os.environ) -> Config:
    config_dir = paths.config_dir(env)
    defaults = layers.read_defaults()
    host = layers.read_toml(config_dir / "config.toml")
    layers.check_host(host)
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    layers.merge(values, copy.deepcopy(defaults), "defaults", sources)
    layers.merge(values, host, "host:config.toml", sources)
    restrict: dict[str, Any] = {}
    if workstream:
        ws = layers.read_toml(config_dir / "workstreams" / f"{workstream}.toml")
        layers.check_workstream(workstream, ws, values)
        restrict = ws.pop("restrict", {})
        layers.merge(values, ws, f"workstream:{workstream}.toml", sources)
    env_values, env_labels = layers.env_layer(env)
    for table, entries in env_values.items():
        values.setdefault(table, {}).update(entries)
    sources.update(env_labels)
    layers.check_profiles(values)
    layers.check_secrets(values)
    policy = load_policy(config_dir / "policy.toml", defaults.get("tiers", {}), restrict)
    return Config(values=values, sources=sources, policy=policy)
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q`
Expected: all pass. Hypothesis runs its default 100 examples.

- [ ] **Step 8: CLI: `heterodyne config check` and a minimal `heterodyne setup`**

Add to `src/heterodyne/cli.py` (imports at the top, then the functions, then register them in `build_parser`):

```python
import os
import shutil
import tomllib
from importlib import resources
from pathlib import Path

from heterodyne import config as hconfig
from heterodyne.config import paths


def _flatten(tree: dict[str, object], prefix: str = "") -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for key, value in tree.items():
        if isinstance(value, dict) and not ({"file", "command"} >= set(value) and len(value) == 1):
            out += _flatten(value, f"{prefix}{key}.")  # type: ignore[arg-type]
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


def cmd_setup(_: argparse.Namespace) -> int:
    target = paths.config_dir(os.environ)
    target.mkdir(parents=True, exist_ok=True)
    examples = Path(str(resources.files("heterodyne"))).parent.parent / "examples"
    os_name = platform.detect()
    for name in ("config.toml", "policy.toml"):
        dest = target / name
        if dest.exists():
            print(f"kept existing {dest}")
            continue
        text = (examples / name).read_text().replace("<linux-or-macos>", os_name)
        dest.write_text(text)
        dest.chmod(0o600)
        print(f"wrote {dest} (edit the <placeholders>)")
    tomllib.loads((target / "config.toml").read_text())
    print(f"platform: {os_name} {platform.backends(os_name)}")
    return 0
```

In `build_parser`, after the `platform` subcommand:

```python
    cfg = sub.add_parser("config", help="inspect configuration")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True)
    check = cfg_sub.add_parser("check", help="validate the merged config and show each value's source")
    check.add_argument("--workstream")
    check.set_defaults(func=cmd_config_check)
    sub.add_parser("setup", help="create the host config from examples (never overwrites)").set_defaults(
        func=cmd_setup)
```

Remove the now-unused `shutil` import if ruff flags it. Plan 8 extends `setup` with the interactive import.

Run: `export HETERODYNE_CONFIG_DIR=$(mktemp -d) && uv run heterodyne setup && uv run heterodyne config check; echo "exit=$?"; unset HETERODYNE_CONFIG_DIR`
Expected: `setup` writes both files. `config check` prints values with sources such as `timeouts.gatekeeper_seconds = 60    (defaults)`, the single-model note (both example profiles use `<model-name>`), and `exit=0`.

Note: `setup` locates `examples/` relative to the source checkout, which is the supported install for v1. Plan 8 packages the examples as package data when it builds the full installer.

- [ ] **Step 9: Lint, types and tests**

Run: `uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all clean.

- [ ] **Step 10: Commit, then cross-model review (brief: §15 layer rules and §5.3 tiers), then fix and commit**

```bash
git add src examples tests && git commit -qm "Config layering with provenance, host-only policy, [restrict] tightening, config check/setup (ADR 0001 §15)"
```

---

### Task 9: Install-agnostic checker, pre-commit and CI

**Files:**
- Create: `$HZ/scripts/check_install_agnostic.py`, `$HZ/tests/test_check_install_agnostic.py`, `$HZ/.pre-commit-config.yaml`, `$HZ/.github/workflows/ci.yml`

**Interfaces:**
- Produces:
  - `scripts/check_install_agnostic.py [--extra FILE] [paths...]`. It exits 1 and prints `path:line: rule: excerpt` for each hit.
  - The default scan covers `git ls-files`, excluding `examples/`, `tests/fixtures/` and `LICENSE`.
  - The `--extra` file (default `$HETERODYNE_CONFIG_DIR/leakcheck.txt`, host-local and never committed) adds literal strings such as a username, hostname or group IDs.
  - The model-name rule applies to `src/` only (§4.1).

- [ ] **Step 1: Write the failing tests**

```python
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_install_agnostic.py"


def run(tmp: Path, files: dict[str, str], extra: str | None = None) -> subprocess.CompletedProcess[str]:
    for name, text in files.items():
        (tmp / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / name).write_text(text)
    args = [sys.executable, str(SCRIPT), "--root", str(tmp)]
    if extra is not None:
        (tmp / "leak.txt").write_text(extra)
        args += ["--extra", str(tmp / "leak.txt")]
    return subprocess.run([*args, *files], capture_output=True, text=True)


def test_clean_file_passes(tmp_path: Path) -> None:
    assert run(tmp_path, {"docs/a.md": "Config lives in ${XDG_CONFIG_HOME:-~/.config}/heterodyne\n"}).returncode == 0


def test_flags_install_specific_values(tmp_path: Path) -> None:
    text = ("see /home/alice/repos/x\nserver 10.1.2.3:3307\nmail alice@corp.io\n"  # install-agnostic: allow=home-path,ip-port,email (fake samples in the checker's own test)
            "npub1" + "q" * 58 + "\n")
    r = run(tmp_path, {"docs/a.md": text})
    assert r.returncode == 1
    for rule in ("home-path", "ip-port", "email", "npub"):
        assert rule in r.stdout


def test_model_names_only_flagged_in_src(tmp_path: Path) -> None:
    assert run(tmp_path, {"docs/a.md": "we use gpt-6-sol\n"}).returncode == 0
    r = run(tmp_path, {"src/heterodyne/x.py": 'MODEL = "gpt-6-sol"\n'})
    assert r.returncode == 1 and "model-name" in r.stdout


def test_examples_and_fixtures_exempt(tmp_path: Path) -> None:
    assert run(tmp_path, {"examples/c.toml": "/home/alice\n", "tests/fixtures/f.txt": "10.0.0.1:22\n"}).returncode == 0  # install-agnostic: allow=home-path,ip-port (fake samples in the checker's own test)


def test_extra_local_denylist(tmp_path: Path) -> None:
    r = run(tmp_path, {"docs/a.md": "runs on myhostname\n"}, extra="myhostname\n")
    assert r.returncode == 1 and "local-denylist" in r.stdout
```

- [ ] **Step 2: Run the tests and check they fail**

Run: `uv run pytest -q tests/test_check_install_agnostic.py`
Expected: FAIL (the script is missing).

- [ ] **Step 3: Implement `scripts/check_install_agnostic.py`**

```python
#!/usr/bin/env python3
"""Fail if tracked files contain install-specific values (ADR 0001 §15) or model names in src (§4.1)."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

RULES = {
    "home-path": re.compile(r"(?<![\w$])/(?:home|Users)/[A-Za-z0-9._-]+|(?<![\w$])/root/"),  # install-agnostic: allow=home-path (the checker's own pattern source)
    "ip-port": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b"),
    "email": re.compile(r"\b[\w.+-]+@(?!example\.(?:com|org|net)\b)[\w-]+\.[\w.-]+\b"),
    "npub": re.compile(r"\bnpub1[02-9ac-hj-np-z]{58}\b"),
    "nsec": re.compile(r"\bnsec1[02-9ac-hj-np-z]{58}\b"),
    "did": re.compile(r"\bdid:key:z6Mk[1-9A-HJ-NP-Za-km-z]{40,}"),
    "radicle-id": re.compile(r"\brad:z[1-9A-HJ-NP-Za-km-z]{20,}"),
}
SRC_RULES = {"model-name": re.compile(r"\b(?:gpt-\d[\w.-]*|claude-(?:opus|sonnet|haiku|fable)[\w.-]*|o\d-[\w.-]+)\b")}
EXEMPT = ("examples/", "tests/fixtures/", "LICENSE")


def tracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.split()


def default_extra() -> Path | None:
    base = os.environ.get("HETERODYNE_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "heterodyne")
    path = Path(base) / "leakcheck.txt"
    return path if path.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--extra", type=Path, default=None)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    root = Path(args.root)
    extra_file = args.extra or default_extra()
    literals = [s.strip() for s in extra_file.read_text().splitlines() if s.strip()] if extra_file else []
    hits = 0
    for rel in args.paths or tracked(root):
        if rel.startswith(EXEMPT) or rel.endswith("check_install_agnostic.py"):
            continue
        try:
            lines = (root / rel).read_text().splitlines()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        rules = {**RULES, **(SRC_RULES if rel.startswith("src/") else {})}
        for n, line in enumerate(lines, 1):
            found = [name for name, rx in rules.items() if rx.search(line)]
            found += ["local-denylist" for lit in literals if lit in line]
            for rule in found:
                print(f"{rel}:{n}: {rule}: {line.strip()[:100]}")
                hits += 1
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests, then the checker on the repo**

Run: `uv run pytest -q tests/test_check_install_agnostic.py && python3 scripts/check_install_agnostic.py; echo "exit=$?"`
Expected: 5 passed.
- The repo scan should print `exit=0`.
- If it flags anything in `docs/` (for example an email in a review record), sanitise that file the same way as Task 1 Step 3 and rerun.
- The ADR's own model names are fine, because the model-name rule covers `src/` only.

- [ ] **Step 5: The operator's local deny-list (host-local, never committed)**

Run: `printf '%s\n' "$(id -un)" "$(hostname)" >> "${HETERODYNE_CONFIG_DIR:-$HOME/.config/heterodyne}/leakcheck.txt" && python3 scripts/check_install_agnostic.py; echo "exit=$?"`
Expected: `exit=0`. If there are hits, sanitise and rerun.

- [ ] **Step 6: `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: local
    hooks:
      - id: install-agnostic
        name: install-agnostic check (ADR 0001 §15)
        entry: python3 scripts/check_install_agnostic.py
        language: system
        pass_filenames: true
      - id: ruff
        name: ruff
        entry: uv run ruff check
        language: system
        types: [python]
```

Run: `uvx pre-commit install && uvx pre-commit run --all-files`
Expected: both hooks pass.

- [ ] **Step 7: `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]   # platform seam matrix (ADR 0001 §11)
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run ruff check
      - run: uv run pyright
      - run: uv run pytest -q
      - run: python3 scripts/check_install_agnostic.py
  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:v8.18.4 detect --source /repo --no-banner -v
```

- [ ] **Step 8: Commit, then cross-model review, then fix and commit**

```bash
git add scripts tests .pre-commit-config.yaml .github && git commit -qm "Install-agnostic checker, pre-commit and CI matrix (ADR 0001 §15, §11)"
```

---

### Task 10: README and docs

**Files:**
- Create: `$HZ/README.md`, `$HZ/docs/install.md`, `$HZ/docs/configuration.md`, `$HZ/docs/security-model.md`

These describe **only what exists after this plan**, plus a clearly labelled status section. Later plans extend them.

- [ ] **Step 1: `README.md`**, with these sections, each written in full:
  1. **What it is:** one paragraph, taken from ADR §2 (deterministic `wsd`, sandboxed agents, Marmot surface, beads as the audit trail).
  2. **Status:** "v1 in development. Implemented: platform seam, configuration layering, policy tiers. Next: admind, then the wsd core (see `docs/superpowers/plans/`)."
  3. **Architecture:** the §3 diagram, copied verbatim.
  4. **Quick start (development):** `uv sync`, `uv run pytest`, `uv run heterodyne platform`, `HETERODYNE_CONFIG_DIR=$(mktemp -d) uv run heterodyne setup && uv run heterodyne config check`.
  5. **Configuration in one minute:** the §15 precedence table, and "host config and policy never go in git".
  6. **Design record:** links to `docs/adr/0001-workstreams-v2.md`, `docs/reviews/` and `docs/spikes/`.
  7. **License:** Apache-2.0, see LICENSE.

- [ ] **Step 2: `docs/configuration.md`**
  - The layer table, the host-only policy, `[restrict]` with an example, the allowed workstream keys, secret references (`{ file = }` / `{ command = }`), the env vars allowed (the three `ENV_KEYS`), and `config check` output with its provenance.
  - Every rule must match `layers.py` and `policy.py` exactly: re-read them while writing.

- [ ] **Step 3: `docs/install.md`**
  - Linux (supported in v1) and macOS (phase 2, marked as such).
  - Prerequisites: Python 3.12+, uv, bubblewrap on Linux, btq, `wn-agent`.
  - `heterodyne setup` as it exists today, and the leak-check deny-list.

- [ ] **Step 4: `docs/security-model.md`**
  - Summarise §5.3 (the sandbox as the boundary, tiers), §5.9 (asks, digest, grooming), §7 (sandbox contents and self-test) and §8 (admind, with the accepted residual risk stated verbatim from the r1 response).
  - Link to the ADR for detail.

- [ ] **Step 5: Check and commit**

Run: `python3 scripts/check_install_agnostic.py && uvx pre-commit run --all-files`
Expected: pass.

```bash
git add README.md docs && git commit -qm "README and docs for foundations"
```

---

### Task 11: Open the replacement PR

**Files:** none (git and GitHub only).

- [ ] **Step 1: Final local verification**

Run: `cd "$HZ" && uv run ruff check && uv run pyright && uv run pytest -q && python3 scripts/check_install_agnostic.py && git ls-files | grep -v -E '^(LICENSE|README.md|pyproject.toml|uv.lock|\.gitignore|\.pre-commit-config.yaml|\.github/|src/|tests/|scripts/|examples/|docs/|spikes/)'; echo "unexpected files above (should be none)"`
Expected: all green, and no unexpected files.

- [ ] **Step 2: Push the branch and open the PR (the operator merges)**

```bash
git push -u origin v2-replace
gh pr create --repo Epiphytic/heterodyne-metaharness --base main --head v2-replace \
  --title "Replace v1 contents with heterodyne-metaharness v2 foundations" \
  --body "Implements plan 1 of ADR 0001 (rev e36f6d0, approval btq-96hm): removes the v1-era contents (kept in history) except LICENSE; adds the design record, spikes S1–S4, package skeleton (platform seam, config layering, host-only policy), install-agnostic checker and CI. Merging is the operator's decision."
```

Expected: a PR URL, with CI running on both OSes. Record the URL in the plan-1 bead's close evidence.

---

## Self-review notes (completed while writing)

- **Spec coverage for plan 1's scope:**

  | ADR | Covered by |
  |---|---|
  | §13 S1–S4 | Tasks 2–5 |
  | §4.3 (wsd identity and per-bead workers) | B1 |
  | btq prerequisite for §5.9 | B2 |
  | §3.2 | Task 7 |
  | §15 layers, policy, `[restrict]`, enforcement, setup (minimal) | Tasks 8–9 |
  | §15 README and docs | Task 10 |
  | §15 repo replacement | Tasks 1 and 11 |
  | §11 CI matrix | Task 9 |
  | §11.1 review | Global Constraints, and each code task's last step |
  | §16 tooling | Task 7 |

  Everything else is assigned in the roadmap.
- **Type and name consistency:**
  - `ConfigError` is defined in `policy.py` and re-exported by `config/__init__.py`; `layers.py` imports it from `policy.py`.
  - `effective_tiers`, `RANK` and `load_policy` are used consistently.
  - btq: `locations`, `ask_digest` and `_git` are used by `approve-bead` in B2.
- **Known iteration points** (called out in their steps rather than hidden): the S1 thread-ID extraction and the S3 bwrap mounts depend on what the spikes find.
- **Checked against btq:** `DESIGN`, `GATED`, `tempfile`, `subprocess`, `Path` and `bd update --metadata` already exist in `tests/test_queue.py`.
