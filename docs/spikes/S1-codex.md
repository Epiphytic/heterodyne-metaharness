# Spike S1: Codex parity

Status: all four questions answered; **ADR amendment needed** (§5.3, §10 — see
below). Two capabilities (pre-tool deny, live queue delivery) only work with a
specific launch shape that is worth calling out for plan 4's adapter, and the
queue-delivery shape has a sandbox-boundary consequence of its own.

CLI tested: codex-cli 0.157.0, OAuth (ChatGPT) subscription login, via a scratch
`CODEX_HOME` (`$S1/codex`, and a second fresh scratch home `$S2/codex` for the
fix-round Q2 control) copied from the real one. The real `~/.codex/auth.json`
was never written by codex: same sha256 and mtime before and after the spike,
and again after the fix-round re-run.

## Result summary

| Question | Result | Evidence (trimmed) | Consequence for plan 4 |
|---|---|---|---|
| **Q1** — pre-tool hook that can deny with a reason under `--yolo` (`--dangerously-bypass-approvals-and-sandbox`)? | **PASS**, interactive session only. **FAIL** under `codex exec`. | Interactive TUI, `run: touch denied2.txt`: `• Blocked by hook └ S1-DENY` / `` `touch denied2.txt` was blocked by the PreToolUse hook (`S1-DENY`). The command did not run.`` — `denied2.txt` absent. Same hook + same flags under `codex exec --json`: `{"type":"item.completed","item":{"type":"command_execution","command":"/bin/bash -lc 'touch denied.txt'","exit_code":0}}` — `denied.txt` created, no deny event, tried with and without `--dangerously-bypass-hook-trust` and with `danger-full-access` and `workspace-write` sandboxes. | Use the interactive/managed session model (the same one `codex queue` needs, see Q4) for anything that must be deny-hookable. `codex exec` (used by the cross-model review invocation, constraints §11.1) does **not** enforce `PreToolUse` deny — that path already uses `-s read-only`, so it doesn't need to. |
| **Q2** — does `--dangerously-bypass-hook-trust` make generated hooks run without per-hook trust hashes? | **PASS** — direct marker control, fresh scratch home | A `[[hooks.SessionStart]]` command hook that touches a marker file was run interactively in `tmux`, one trivial prompt each, same scratch `CODEX_HOME`, same config, only the flag varied. **Without** `--dangerously-bypass-hook-trust`: the startup dialog reads "Hooks need review … 1 hook is new or changed"; choosing "3. Continue without trusting (hooks won't run)" and sending `Reply only with OK` leaves the marker file **absent**. **With** `--dangerously-bypass-hook-trust`: no trust dialog, the same prompt/response, and the marker file **is created**. `codex exec` was also checked both ways as a cheap extra: the marker never appears under `codex exec`, flag or no flag (consistent with Q1 — `codex exec` doesn't run custom command hooks at all, so the flag has nothing to bypass there). | `wsd` can generate hooks into a runner's `config.toml` and launch interactively with `--dangerously-bypass-hook-trust` instead of driving the TUI's interactive trust-review UI. No interactive `t`-to-trust step is needed. This only matters for interactive/managed sessions — `codex exec` never runs these hooks regardless of the flag (see Q1). |
| **Q3** — can the session ID or name be set at launch? | **PASS**, qualified: no launch-time flag exists; a post-launch rename is addressable by both `queue` and `resume`, including a real `uuid5` name. | `codex --help`/`codex exec --help` have no `--session-id`/`--name` flag; thread IDs are server-assigned UUIDs at creation, and `session_index.jsonl`'s `thread_name` starts as an LLM-generated title. Tested with a real `uuid5`: `python3 -c "import uuid; print('ws-' + str(uuid.uuid5(uuid.NAMESPACE_DNS, 'ws:S1-spike:role')))"` → `ws-a05bc0d3-7de4-58ed-a5a3-556cd0afde6d`. After telling the session "Remember the secret word: FLAMINGO. Reply only with OK" and `/rename`-ing it (TUI action, not a CLI subcommand) to that exact string, the tmux pane was killed (the TUI process exited; the saved thread remained resumable) and, in a fresh pane, `codex resume ws-a05bc0d3-7de4-58ed-a5a3-556cd0afde6d --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust` resumed the same thread — prior transcript shown, and asking "What was the secret word?" correctly answered `FLAMINGO`. `codex queue --thread <that name>` was also confirmed separately (original run). | Matches the ADR's own fallback (§4.2: "record the assigned ID on the bead"). `wsd` should read the real UUID right after launch (`session_index.jsonl` or the `thread-writer-locks/<uuid>.lock` filename) rather than trying to pre-set one. A deterministic `uuid5(...)`-style label can be applied post-launch via `/rename` (a TUI action, e.g. `tmux send-keys "/rename" Enter`, wait for the dialog, then type the name and `Enter` again — not a single CLI flag) and is then addressable by both `codex resume <name>` and `codex queue --thread <name>` — budget for that extra round-trip if a human-legible deterministic label is wanted. |
| **Q4** — does `codex queue` deliver into a live interactive TUI? | **PASS**, with a specific launch shape. | Plain `CODEX_HOME=$S1/codex codex ...` (no `--remote`) plus `codex queue --thread <uuid-or-name>` failed: `Error: failed to queue session message: ... no rollout found for thread id <uuid> (code -32603)` and, by exact name, `Error: No active session found matching '<name>'`. Root cause: `codex app-server daemon start` reported `alreadyRunning` against the **real** `~/.codex/app-server-control/app-server-control.sock` — the shared control daemon is bound to the real default home, not to an arbitrary `CODEX_HOME`. Running a scratch-scoped daemon instead (`codex app-server --listen unix://$S1/appserver.sock`, launched with `CODEX_HOME=$S1/codex` and cwd `$S1/work`) and connecting **both** the TUI (`codex --remote unix://$S1/appserver.sock ...`) and the queue call (`codex queue --remote unix://$S1/appserver.sock --thread <uuid> --message "Reply only with PONG"`) to that socket worked: `Queued message <id> for thread <uuid>.` and `PONG` appeared in the pane with no `send-keys`. | If plan 4 gives each workstream/session its own isolated `CODEX_HOME` (likely, for credential/config isolation), `wsd` must also run and track a per-`CODEX_HOME` `codex app-server --listen unix://<path>` and pass `--remote unix://<path>` to every launch **and** every `codex queue` call for that session. Relying on the default shared daemon only works if every Codex session shares one real `CODEX_HOME` — worth deciding explicitly rather than discovering it in production. **Sandbox-boundary consequence (§7):** the app-server process determines the session's working directory (observed: it inherited the launcher's cwd, not the TUI client's `-C`/`--cd`, in an early misconfigured run of this spike), and it is inferred, but not yet proven, to execute the tool calls. Plan 4's sandbox launch self-test must confirm this. So the app-server — not just the thin `--remote` TUI client — must run **inside** the outer bwrap sandbox, and its socket must be the thing exposed at the sandbox boundary. The §3-launch self-test (reading the real-home canary, reaching a non-allowlisted host, calling a control op on the session socket) must be run against a sandbox launch that starts the app-server this way, not against a bare `codex` process. The queue socket can plausibly live under the same `/run/hz` bridge directory S3 already uses for the hook session socket, but with a different sharing mode: S3's session socket is created by the host and the sandboxed agent only connects in, so a read-only bind of `/run/hz` is enough; here the app-server itself calls `bind()` on the queue socket path *from inside* the sandbox, so at least that subpath must be writable from inside, not read-only. |

## ADR impact

**Amendment needed:** ADR §5.3 says `PreToolUse` fires before every tool call,
and §10's fail-closed behavior assumes the hook runs. For Codex, this holds
only for interactive sessions; under `codex exec` the deny hook never fires.
§5.3 and §10 must scope hook enforcement to interactive sessions, and require
any headless Codex run to be read-only (`-s read-only`) or enforced by the
outer sandbox alone.

Supporting detail, section by section:
- §13's Q1 fallback ("catching the sandbox failure after the fact and parking
  the bead") is not needed for the interactive/managed session model, which is
  what `codex queue` (§4.2) already requires. It *is* needed for `codex exec`,
  which today has no hook-based deny path at all — the sandbox (and, for the
  one in-scope headless case, `-s read-only`) is the entire enforcement there.
- §13's Q2 assumption ("hooks are generated by `wsd`, which vets their source")
  is confirmed for interactive sessions: `--dangerously-bypass-hook-trust` is
  sufficient and no interactive trust step is required. It does not extend
  `codex exec`'s capabilities, since `codex exec` does not run these hooks
  regardless of the flag.
- §4.2's Q3 row already anticipated no fixed-ID launch flag ("fallback: record
  the assigned ID on the bead"); this spike confirms that fallback works and
  adds that a deterministic rename is also possible post-launch, addressable
  by both `queue` and `resume`.
- §4.2's Q4 row ("`codex queue --thread <id|name> --message`") is confirmed to
  work; the daemon/socket wiring needed to make it work under an isolated
  `CODEX_HOME`, and the consequence that the app-server process itself must
  run inside the outer sandbox (§7), are new, actionable detail for whoever
  writes the Codex adapter (plan 4) and whoever extends the §7/S3 sandbox
  launch self-test to cover it.

**Open question on Q2 (unresolved, not yet re-adjudicated):** a second,
independent re-run of the Q2 marker control (see `task-2-report.md`, "Fix
round 1 (independent re-verification)") could not reproduce marker creation
under `--dangerously-bypass-hook-trust`, in four separate launches across two
scratch homes, including one with a scratch-scoped `codex app-server` +
`--remote` to rule out missing embedded mode. No "hooks need review" trust
dialog appeared in any of those launches, contrary to what's described above.
Both this spike's hook config and the flat `[[hooks.SessionStart]]` /
`[[hooks.PreToolUse]]` `config.toml` tables used throughout Q1/Q2 differ in
shape from the real, harness-installed `$CODEX_HOME/hooks.json` (JSON, with
required `matcher` and nested `hooks` fields) — untested as the cause, but a
plausible one, since a malformed entry could be silently dropped rather than
rejected. Until someone re-tests against that schema, **do not treat Q1 or Q2
as settled** even though both are marked PASS above.

## Credential safety

- Before any model call, in both the original run and the fix-round re-run:
  the access-token JWT `exp` claim decoded to well over the 2-hour stop
  threshold (~140 hours and ~139 hours remaining respectively). No token value
  was printed at any point — only decoded `exp`/`iat` and a sha256/mtime of the
  file.
- After the spike, and again after the fix round: `~/.codex/auth.json` sha256
  and mtime are **identical** to the pre-spike values. The real file was not
  touched. All model calls ran against a copy under a scratch `CODEX_HOME`
  (`$S1/codex`, then a fresh `$S2/codex` for the fix round), never the real one.
- `codex app-server daemon start` (used while investigating Q4) reported the
  real control socket's path (`alreadyRunning`, pre-existing, started days
  earlier by the operator's own long-running sessions) but did not start,
  stop, or send any message through it. The spike's own traffic went only
  through a separate scratch-scoped socket (`$S1/appserver.sock`).
- `tmux kill-session` and `rm -rf` on the scratch home were run at the end of
  both the original spike and the fix round, including killing the
  scratch-scoped `codex app-server` background process first.
