# admind live acceptance (plan 2, Task 10)

This was run on the reference host (Linux, systemd user units) on 2026-10-01 and 2026-10-02. The operator used the White Noise phone client. Install values (npubs, relays, paths, tokens) are deliberately left out.

**Status: partial.** The operator held all operator-driven tests (steps 6 and 7, and the post-restart message in step 8) until admind supports more than one operator (ADR r13). The operator's phone can't type the exact payloads these steps need (tabs, long pastes). The operator wants a generated test key, added to the group as a second operator, to drive them while they watch. Under ADR r12 a third group member latches admind, so that has to wait for multi-operator support.

## Results

| Step | What | Result |
|---|---|---|
| 1 | Host config, self-test unit | PASS: `heterodyne config check` is clean. `restart_units` holds only the self-test unit. |
| 2 | `admind init` | PASS: identity and group created, and the operator accepted the invite. |
| 3 | Unit install and start | PASS: the audit log shows `start` (wn-agent, admind) and then `launched`. The operator accepted the first-run dialogs over `tmux attach`. |
| 4 | Held alert | PASS: after the operator joined, the outbox stayed empty and nothing was relayed until the operator wrote. |
| 5 | Join signal (D5) | PASS: on "Hi" the ready notice, the held alert and a reply threaded to "Hi" all arrived, and the operator confirmed seeing each one. The first operator message works as the join signal. |
| 6 | Passthrough | PARTIAL, then held. The real Claude Code fired `UserPromptSubmit` for the bracketed paste of "Hi", and `prompt-submitted` was recorded, so the reply was threaded. The long paste (over 50 lines) and the multi-line, emoji and quotes echo are held. |
| 7 | Commands | PARTIAL, then held. `!tail` answered while the agent was stuck. `!new` cleared `AgentStuck`, and the fresh session's `SessionStart` (source `startup`) arrived at once. Untested so far: `!ps`, both `!restart` variants, `!interrupt` during a long turn, a reply from the fresh session after `!new`, and `!restrat`. |
| 8 | Restart survival | FAIL, fixed, then PASS. See below. Still pending: "the next message works" (operator-driven, held). |

## Findings and fixes

- **Restart killed the agent (step 8, fixed in b80da46, 823a24f, 55847cf).** admind's private tmux server ran inside the admind unit's cgroup. With `KillMode=control-group`, every unit stop killed tmux and the agent, so a restart only ever relaunched with `--resume`, and the adoption path never ran. The tmux server now starts through `systemd-run --user --scope` with a fresh scope name per start. Live check after the fix:
  - the server's cgroup was `heterodyne-admind-tmux-<id>.scope`;
  - across `systemctl --user restart heterodyne-admind`, the tmux server and pane pids were unchanged;
  - the audit log showed `adopted` for the same session, then `adopted-hold`, and the adoption notice was sent.
- **Workspace trust is not kept for `~`.** With the default `workdir = "~"`, a relaunch stopped at Claude Code's workspace-trust dialog. Three 120-second ready timeouts then led to `AgentStuck`, which only `!new` clears. The operator set the admin agent's root to `~/.hermes`. The docs and example config now recommend a dedicated workdir.
- **Permission mode.** admind launches with `--permission-mode bypassPermissions`, but on this host Claude Code reports **auto mode**, presumably because of an Enterprise policy. The operator accepts auto mode on one condition: the agent's permission prompts and approvals must be relayed over Marmot to the operator and back to the Claude Code instance. That relay is ADR r13 work.
- **A hook error that isn't admind's.** A Node SessionStart hook from the user's global Claude settings fails, without blocking, in the admin pane. admind's own hooks work.
- **Audit records the operator's text.** `inbound` audit records carry the operator's text through the output policy: secrets, npubs and 64-hex values are redacted, control characters are escaped, and the text is truncated at 2,000 characters. Whether to keep recording it at all is the open ADR §8 logging-scope decision.

## Claude Code hook behaviour seen live

- `SessionStart` fires with `source` `startup` on a fresh launch, and after `!new`.
- `UserPromptSubmit` fires for a bracketed paste plus Enter, and its `prompt` equals the pasted text, for a short single-line message only so far.
- `Stop` delivered the reply, and the reply was threaded to the operator's message.
- Not yet observed live: whether hooks are synchronous under load, whether `Stop` stays silent on interrupt, how the hook `timeout` behaves, and the `resume` source. These need the held steps 6 and 7.

## S4 carry-forwards

- Reaction removal: admind doesn't use it, so it moves to plan 6.
- The `group_leave` self-event: admind never leaves its group, so it moves to plan 6.
- Latch: covered by the Task 8 integration tests. A live latch test needs a third identity. It's now planned together with the multi-operator test key (ADR r13).
