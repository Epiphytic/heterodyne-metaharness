# admind live acceptance (plan 2, Task 10)

This was run on the reference host (Linux, systemd user units) on 2026-10-01 and 2026-10-02. The operator used the White Noise phone client. Install values (npubs, relays, paths, tokens) are deliberately left out.

**Status: complete (revision 13 run, 2026-10-03 and 2026-10-04).** The first run was partial: the operator held steps 6 and 7 and the post-restart message in step 8 until admind supported more than one operator. The revision 13 run below completes them, driven from a generated test key added as a second operator while the operator watched. Its results are in "Revision 13 run" at the end. The table that follows is the first run, unchanged.

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
- **Workspace trust is not kept for `~`.** With the default `workdir = "~"`, a relaunch stopped at Claude Code's workspace-trust dialog. Three 120-second ready timeouts then led to `AgentStuck`, which only `!new` clears. The operator set the admin agent's root to a dedicated directory (`<admin-workdir>`). The docs and example config now recommend a dedicated workdir.
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

## Revision 13 run (2026-10-03 and 2026-10-04)

Deployed at 19158c4 (PR #15). A throwaway `wn-agent` home acted as the test operator `llctest`, with its invite policy restricted to admind's identity. It sent exact UTF-8 payloads through the control socket. The audit log was compared against the bytes sent. The summarizer was a `claude-code` profile.

| Step | What | Result |
|---|---|---|
| Deploy | Store migration to r13 | PASS: `migrated` (expected members 2) and `migrated-operators` (1); the running agent was adopted and the notice sent. |
| Add operator | `admind operators add llctest` | PASS after a runbook fix (operators must also be approvers, see below). Journal `pending` 2→3, then `committed` at 3; the notice reached both members. |
| 2 | Byte-exact passthrough | PASS: a 63-line paste (1993 characters, with tabs and trailing spaces) and a 3-line echo (curly and straight quotes, backtick, emoji with skin tone, flag, ZWJ sequence, non-breaking hyphen, literal backslashes). The audit `text` equalled the bytes sent for both, attributed to `llctest`. In the agent's echo, which is outbound, the tab came back as spaces; the cause was not investigated. |
| 3 | Commands | PASS: `!ps`; `!restart heterodyne-admind-selftest.service` ran; `!restart hermes-gateway.service` was refused with the allowlist named, and the gateway's PID and start time were unchanged; `!restrat` got the unknown-command reply. `!interrupt` during a 180-second `sleep` sent Esc, and the interrupted message got "No reply to this message: interrupted by !interrupt.". `!new` started a fresh session, which then replied and had no memory of the previous conversation. |
| 4 | Long reply | PASS: a 1286-character reply came as a summary with the `!details` footer, threaded to the message. |
| 5 | Summarizer failure | PASS: with the summarizer pointed at an invalid model, the `summary` record was `failed`, the reply was `queued` to the backstop and the batch was `posted` 61 seconds later, unthreaded, with the title and the origin line (operator, time, first words). The config was restored afterwards. |
| 6 | `!details` | PASS: `!details` in reply to a summary returned the full redacted reply under its origin line; `!details full` added the tool-call section ("no tool calls in this turn"). |
| 7 | Redaction | PASS: a planted fake `ghp_` token echoed by the agent came back as `<redacted GitHub token>`; the literal string appears in neither the audit log nor the test client's received messages. |
| 8 | Remove operator | PASS: `pending` 3→2, then `committed` at 2, notice sent, no latch. The removed key's next send was refused by its own `wn-agent`. |
| 9 | Latch and rearm | PASS: the test key was re-added (3 members) and then left the group by itself (`group_leave`). admind latched on `member_left` within seconds. After `policy.toml` was restored, `admind rearm` cleared the latch with the trusted count 2 and the operators confirmed, and the next operator message was answered. Not observed: an operator message dropped while latched (none was sent during the latch; covered by unit tests). |
| 10 | After a restart | PASS: after each `systemctl --user restart heterodyne-admind`, the running agent was adopted and held; the next message was answered after `!interrupt` or `!new`. |

### Findings (revision 13 run)

- **An agent waiting in a picker blocks admind invisibly.** The adopted agent had sat for about a day in an `AskUserQuestion` option picker. admind cannot relay a picker, so the operator saw only the generic adopt-hold notice, and their message got no reply until `!interrupt`. The adopt-hold notice also says the agent is finishing its current turn when it is idle or waiting on input. The operator's requirement (2026-10-03): normal operation must never need `tmux` or any channel other than Marmot. State must always be visible on Marmot. Questions, option pickers, permission prompts and first-run dialogs go through the Marmot approval flow. Tracked as an ADR 0001 amendment (`btq-xv48a`), together with:
  - **A latched admind is fully silent**, so the operator cannot tell from Marmot that it is latched.
  - **Progress reactions.** The operator expects reactions on their messages as the agent works (seen, thinking, tool use, done), as the Hermes gateway does. admind sends none, and ignores reactions it receives. Reaction removal is still untested (S4).
  - **The first-run dialogs in section 3 of the runbook are accepted over `tmux attach`.** Under the requirement above this is break-glass only, to be replaced by the relay.
- **Runbook gaps, fixed in this change:** an operator must also be listed in `approvers` (otherwise `operators add` refuses, and a restart in that state fails); `admind` is a console script of the package's environment, not a system command.
