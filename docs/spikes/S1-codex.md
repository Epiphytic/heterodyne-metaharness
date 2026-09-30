# Spike S1: Codex parity

Status: all four questions answered; **no ADR change required**. Two capabilities
(pre-tool deny, live queue delivery) only work with a specific launch shape that
is worth calling out for plan 4's adapter.

CLI tested: codex-cli 0.157.0, OAuth (ChatGPT) subscription login, via a scratch
`CODEX_HOME` (`$S1/codex`) copied from the real one. The real `~/.codex/auth.json`
was never written by codex: same sha256 and mtime before and after the spike.

## Result summary

| Question | Result | Evidence (trimmed) | Consequence for plan 4 |
|---|---|---|---|
| **Q1** — pre-tool hook that can deny with a reason under `--yolo` (`--dangerously-bypass-approvals-and-sandbox`)? | **PASS**, interactive session only. **FAIL** under `codex exec`. | Interactive TUI, `run: touch denied2.txt`: `• Blocked by hook └ S1-DENY` / `` `touch denied2.txt` was blocked by the PreToolUse hook (`S1-DENY`). The command did not run.`` — `denied2.txt` absent. Same hook + same flags under `codex exec --json`: `{"type":"item.completed","item":{"type":"command_execution","command":"/bin/bash -lc 'touch denied.txt'","exit_code":0}}` — `denied.txt` created, no deny event, tried with and without `--dangerously-bypass-hook-trust` and with `danger-full-access` and `workspace-write` sandboxes. | Use the interactive/managed session model (the same one `codex queue` needs, see Q4) for anything that must be deny-hookable. `codex exec` (used by the cross-model review invocation, constraints §11.1) does **not** enforce `PreToolUse` deny — that path already uses `-s read-only`, so it doesn't need to. |
| **Q2** — does `--dangerously-bypass-hook-trust` make generated hooks run without per-hook trust hashes? | **PASS** | Without the flag: `codex exec --json` shows **zero** hook items in the event stream (hook silently skipped, untrusted). With the flag: two `` `--dangerously-bypass-hook-trust` is enabled `` warning items appear (one per configured hook family) and, in the interactive session, the `PreToolUse` hook actually fired and blocked the tool call (see Q1 evidence) — proof the flag causes real execution, not just a warning. | `wsd` can generate hooks into a runner's `config.toml` and launch with `--dangerously-bypass-hook-trust` instead of driving the TUI's interactive trust-review UI. No interactive `t`-to-trust step is needed. |
| **Q3** — can the session ID or name be set at launch? | **PASS**, via post-launch rename (no launch-time flag). | `codex --help`/`codex exec --help` have no `--session-id`/`--name` flag; thread IDs are server-assigned UUIDs at creation, and `session_index.jsonl`'s `thread_name` starts as an LLM-generated title. The TUI has a `/rename` action (not a top-level CLI subcommand); after `/rename S1-uuid5-deadbeef-cafe`, `session_index.jsonl` updated to `{"id":"<uuid>","thread_name":"S1-uuid5-deadbeef-cafe",...}`, and `codex queue --thread S1-uuid5-deadbeef-cafe --message "Reply only with NAMEOK"` delivered and the pane showed `NAMEOK`. | Matches the ADR's own fallback (§4.2: "record the assigned ID on the bead"). `wsd` should read the real UUID right after launch (`session_index.jsonl` or the `thread-writer-locks/<uuid>.lock` filename) rather than trying to pre-set one. Renaming to a deterministic `uuid5(...)` label is possible but only via a TUI action (e.g. `tmux send-keys "/rename <name>" Enter Enter`), not a CLI flag — budget for that extra round-trip if a human-legible deterministic label is wanted. |
| **Q4** — does `codex queue` deliver into a live interactive TUI? | **PASS**, with a specific launch shape. | Plain `CODEX_HOME=$S1/codex codex ...` (no `--remote`) plus `codex queue --thread <uuid-or-name>` failed: `Error: failed to queue session message: ... no rollout found for thread id <uuid> (code -32603)` and, by exact name, `Error: No active session found matching '<name>'`. Root cause: `codex app-server daemon start` reported `alreadyRunning` against the **real** `~/.codex/app-server-control/app-server-control.sock` — the shared control daemon is bound to the real default home, not to an arbitrary `CODEX_HOME`. Running a scratch-scoped daemon instead (`codex app-server --listen unix://$S1/appserver.sock`, launched with `CODEX_HOME=$S1/codex` and cwd `$S1/work`) and connecting **both** the TUI (`codex --remote unix://$S1/appserver.sock ...`) and the queue call (`codex queue --remote unix://$S1/appserver.sock --thread <uuid> --message "Reply only with PONG"`) to that socket worked: `Queued message <id> for thread <uuid>.` and `PONG` appeared in the pane with no `send-keys`. | If plan 4 gives each workstream/session its own isolated `CODEX_HOME` (likely, for credential/config isolation), `wsd` must also run and track a per-`CODEX_HOME` `codex app-server --listen unix://<path>` and pass `--remote unix://<path>` to every launch **and** every `codex queue` call for that session. Relying on the default shared daemon only works if every Codex session shares one real `CODEX_HOME` — worth deciding explicitly rather than discovering it in production. |

## ADR impact

None. All four results confirm or fill in existing placeholders rather than
contradicting them:
- §13's Q1 fallback ("catching the sandbox failure after the fact and parking
  the bead") is not needed for the interactive/managed session model, which is
  what `codex queue` (§4.2) already requires — so the two capabilities are
  consistent with each other. It *would* be needed for any headless `codex exec`
  path that wants deny-hook protection, but the one headless path in scope
  (cross-model review, constraints §11.1) doesn't need it.
- §13's Q2 assumption ("hooks are generated by `wsd`, which vets their source")
  is confirmed: `--dangerously-bypass-hook-trust` is sufficient and no
  interactive trust step is required.
- §4.2's Q3 row already anticipated no fixed-ID launch flag ("fallback: record
  the assigned ID on the bead"); this spike confirms that fallback works and
  adds that a deterministic rename is also possible post-launch.
- §4.2's Q4 row ("`codex queue --thread <id|name> --message`") is confirmed to
  work; the daemon/socket wiring needed to make it work under an isolated
  `CODEX_HOME` is new, actionable detail for whoever writes the Codex adapter
  (plan 4), not a contradiction of anything already decided.

## Credential safety

- Before any model call: `~/.codex/auth.json` `last_refresh` was
  `2026-09-25T23:04:20Z`; the access-token JWT `exp` claim decoded to
  ~140 hours remaining (well over the 2-hour stop threshold). No token value
  was printed at any point — only `last_refresh`, decoded `exp`/`iat`, and a
  sha256/mtime of the file.
- After the spike: `~/.codex/auth.json` sha256 and mtime are **identical** to
  the pre-spike values. The real file was not touched. All model calls ran
  against a copy under a scratch `CODEX_HOME` (`$S1/codex`), never the real one.
- `codex app-server daemon start` (used while investigating Q4) reported the
  real control socket's path (`alreadyRunning`, pre-existing, started days
  earlier by the operator's own long-running sessions) but did not start,
  stop, or send any message through it. The spike's own traffic went only
  through a separate scratch-scoped socket (`$S1/appserver.sock`).
- `tmux kill-session -t s1` and `rm -rf "$S1"` were run at the end, including
  killing the scratch-scoped `codex app-server` background process first.
