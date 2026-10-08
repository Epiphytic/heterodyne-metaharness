# Spike S8: Marmot-only admin-agent capabilities

Status: **client side done for both adapters; one blocking gap for Codex.**
- Claude Code shows all four minimum capabilities (ADR 0001 r14 §13 S8) client side.
- Codex shows 1, 2 and 4. It does **not** show 3 as specified: Codex fires `SessionStart` only when the first prompt is submitted, never at launch, so no preflight can reach `SessionStart` with no terminal input. An alternative is proposed below for the ADR to accept or reject.
- The host-free end-to-end acceptance ran on both real CLIs with admind's `FakeWnAgent`. Every case it covers passed: Claude 26/26, Codex 24/24 with `--no-daemon`.
- Without `--no-daemon`, Codex failed the relaunch case. Its shared app-server daemon outlives the tmux session and keeps running the old launch's hooks.
- ADR §17 item 8 (`remove_reaction`): partly answered; see "Other findings".

Bead btq-brhid. It gates §8.1.

CLIs tested: Claude Code 2.1.286 and codex-cli 0.160.0 as installed, the same as S7. The ADR §4.2 table names 2.1.283 and 0.157.0, and neither CLI was upgraded. Each finding is per version, so any later run must record the version it used. tmux 3.4.

Method (scripts in `spikes/s8/`, evidence in `spikes/s8/evidence/`; `$R` is a run's scratch directory under `/tmp/s8`):
- Every run used `sandbox.sh`, a bubblewrap sandbox:
  - private network, pid, ipc and uts namespaces; it refuses to run (rc 2) unless `/proc/net/dev` lists only `lo`;
  - `HOME` and `/tmp` are tmpfs, so no real login, `~/.claude`, `~/.codex`, `~/.hermes` or Marmot home exists inside;
  - only `/usr`, `/etc`, the two CLIs' install directories, `spikes/`, and (for `e2e.py`) `src/`, `tests/fakes/` and `.venv/` are visible, read-only, plus the run directory read-write;
  - the environment is rebuilt by `env -i` from an allowlist.
- Each CLI ran in a fresh workdir `$R/work` with its own `CLAUDE_CONFIG_DIR` or `CODEX_HOME` under `$R`, on a private tmux server (`tmux -L s8spike`) that dies with the sandbox. No global setting was edited.
- Claude ran against `clstub.py`, an Anthropic Messages stand-in reached through `ANTHROPIC_BASE_URL`, with a dummy `.credentials.json`.
- Codex ran against `cxstub.py`, a Responses API stand-in configured as a custom model provider (`wire_api = "responses"`), with a dummy key in `STUB_API_KEY`.
- Each stub is scripted by the newest user text:
  - `PICK` calls the question tool (`AskUserQuestion` for Claude, `request_user_input` for Codex);
  - `PICKSECRET` puts a 64-hex value in the question;
  - `BASH` runs a shell command and `EDITOUT` writes a file outside the workdir;
  - after a tool result it answers in text, after the delay in `$R/delay`.
- The launch shape is admind's: `claude --session-id|--resume SID --permission-mode bypassPermissions --settings FILE --name admin-agent`, and `codex [resume --last] --dangerously-bypass-approvals-and-sandbox`. Text goes in through admind's paste: tmux `load-buffer`, `paste-buffer -p -d`, 0.3 s, then Enter.

Scripts:

| Script | What it does |
|---|---|
| `run-claude.sh` | `MODE=main`: picker, answer turn, Bash, Write outside the workdir, an interrupt, then a restart with `--resume`. `MODE=prompts [ALLOW=1]`: `--permission-mode default` plus a `PermissionRequest` hook (`ALLOW=1` returns allow). `MODE=dialog DROP=onboarding\|trust\|bypass`: one pre-accepted key left out. |
| `run-codex.sh` | Codex counterparts. `MODE=look\|main\|prompts`. `HOOKTRUST=setup\|flag\|none` sets how hooks get trusted. `TRUST=0` leaves out workspace trust. `RETOUCH=1` changes a hook command after it was trusted. |
| `cx-trust-hooks.py` | The setup step that pre-accepts Codex's hook review through config (see capability 3). |
| `probe-codex-rehook.sh` | Do changed hook commands take effect on relaunch, with and without the daemon (`RESUME`, `NODAEMON`)? |
| `probe-tmux-attach.sh` | tmux client attach and detach hooks. |
| `e2e.py`, `e2ehook.py` | The host-free end-to-end acceptance (see below). |
| `collect-evidence.sh` | Copies the runs' outputs into `evidence/`. It masks 64-hex values, trust hashes and paths, and fails closed on anything left unmasked. |

Reading the result column:
- **demonstrated:** shown on the real CLI, at its pinned version, through this harness. Every result here is client side: a stub stood in for the model, and a dummy login stood in for the account. Nothing in S8 depends on what the provider's server does, except possibly which dialogs a real login shows (see the open items).
- **not demonstrated:** the run showed it doesn't work as specified.
- **unknown:** not tested.

## Result summary: Claude Code 2.1.286

| Capability | Result | Evidence (trimmed) |
|---|---|---|
| **1. Pre-tool hook on the question tool; question and options; denial with a reason, at once** | **Demonstrated** | `PreToolUse` with matcher `AskUserQuestion` receives `tool_input.questions[]` (`question`, `header`, `options[]` with `label` and `description`, `multiSelect`) and a `tool_use_id` ([`c-main/hooks.jsonl`](../../spikes/s8/evidence/c-main/hooks.jsonl)). Returning `hookSpecificOutput.permissionDecision = "deny"` with a `permissionDecisionReason` ends the call at once. The model gets the tool result `PreToolUse:AskUserQuestion hook error: <reason>` with `is_error: true`. No picker is drawn ([`c-main/1-picker.pane.txt`](../../spikes/s8/evidence/c-main/1-picker.pane.txt)). |
| **2. `UserPromptSubmit` and `Stop` every turn, including the turn right after a denied picker** | **Demonstrated** | `Stop` fired at the end of the denied-picker turn. The next paste gave `UserPromptSubmit` whose `prompt` is the pasted attributed answer, then `Stop`. Every turn of every run gave exactly one of each ([`c-main/result.txt`](../../spikes/s8/evidence/c-main/result.txt)). Esc during a turn (admind's `!interrupt`) gives **no** `Stop`. The pane shows `Interrupted · What should Claude do instead?`. |
| **3. Startup dialogs pre-accepted through config; preflight reaches `SessionStart` with no input, fresh workdir and after a restart** | **Demonstrated** | `$CLAUDE_CONFIG_DIR/.claude.json` with `hasCompletedOnboarding: true`, `theme`, `bypassPermissionsModeAccepted: true` and `projects["<workdir>"].hasTrustDialogAccepted: true`. That gives `SessionStart` (`source: startup`) with no terminal input in a fresh `/tmp` workdir. After the session was killed, `--resume SID` gave `SessionStart` (`source: resume`), again with no input. With any one key left out, there's no `SessionStart` within 30 s, and the pane shows that key's dialog ([`c-drop-*/0-start.pane.txt`](../../spikes/s8/evidence/)): onboarding gives the welcome/login screen, trust gives `Yes, I trust this folder`, and bypass gives `WARNING: Claude Code running in Bypass Permissions mode … Yes, I accept`. |
| **4. No permission prompt in bypass mode, or a hook that reports one** | **Demonstrated** (no prompt in bypass mode). Reporting hook: also shown, and it can return the decision. | In `bypassPermissions` mode, a Bash command and a Write outside the workdir both ran with no prompt and no `Notification` event. In `--permission-mode default`, the prompt `Do you want to proceed? 1. Yes …` appears, and `PermissionRequest` fires at once with `tool_name`, `tool_input` and `permission_suggestions` ([`c-prompts/hooks.jsonl`](../../spikes/s8/evidence/c-prompts/hooks.jsonl)). `Notification` follows about 6 s later, with only `{"message": "Claude needs your permission", "notification_type": "permission_prompt"}` and no prompt text. A `PermissionRequest` hook returning `{"decision": {"behavior": "allow"}}` ran the command (`c-prompts-allow`). |

Notes:
- If no `--permission-mode` is given, this version starts in **auto mode**, whose classifier makes its own model request. That probably explains the "auto mode" seen in the admind-acceptance doc. admind's explicit `bypassPermissions` avoids it.
- For a §8.1 permission card, `PermissionRequest` is the hook to use: it fires at once and carries the tool and its input, while `Notification` carries no text.

## Result summary: codex-cli 0.160.0

| Capability | Result | Evidence (trimmed) |
|---|---|---|
| **1. Pre-tool hook on the question tool; question and options; denial with a reason, at once** | **Demonstrated** | The model is offered `request_user_input` in Default mode too, even though its description says "only available in Plan mode" and the feature `default_mode_request_user_input` is off. `PreToolUse` with matcher `request_user_input` receives `tool_input.questions[]` (`id`, `header`, `question`, `options[]`) and a `tool_use_id` ([`x-main/hooks.jsonl`](../../spikes/s8/evidence/x-main/hooks.jsonl)). The same `permissionDecision: "deny"` output shows `Blocked by hook` in the pane, and the model gets `Tool call blocked by PreToolUse hook: <reason>`. When a call was *not* denied (the stale-hook failure below), Codex itself answered `request_user_input is unavailable in Default mode`, so no picker was drawn then either. |
| **2. `UserPromptSubmit` and `Stop` every turn, including the turn right after a denied picker** | **Demonstrated** | `Stop` fired at the end of the denied turn. `UserPromptSubmit` and `Stop` fired once per turn ([`x-main/result.txt`](../../spikes/s8/evidence/x-main/result.txt)). Esc gives no `Stop`. Instead it fires Codex's own `Interrupt` hook event, which admind could use to see `!interrupt` take effect. |
| **3. Startup dialogs pre-accepted through config; preflight reaches `SessionStart` with no input, fresh workdir and after a restart** | **Not demonstrated** (the `SessionStart` half). Dialog pre-acceptance: demonstrated, with a per-launch setup step. | *Dialogs.* With hooks configured, startup stops at `Hooks need review: 5 hooks are new or changed` ([`x-retouch/0-start.pane.txt`](../../spikes/s8/evidence/x-retouch/0-start.pane.txt)). `cx-trust-hooks.py` pre-accepts it through config: it asks `codex app-server` (`hooks/list`) for each hook's key and current hash, then appends `[hooks.state."<key>"] trusted_hash = "<hash>"` to `$CODEX_HOME/config.toml`. The other way is `--dangerously-bypass-hook-trust`. **A trusted hash covers the exact command**, so changing a hook command (as admind's per-launch `--launch` nonce does, `RETOUCH=1`) brings the dialog back. With bypass flags, no workspace-trust dialog appeared, even with no `[projects]` trust entry (`x-notrust`). *SessionStart.* Codex reaches its ready prompt (`Ask Codex to do anything`) with no input, but **`SessionStart` doesn't fire until the first prompt is submitted**, just before that prompt's `UserPromptSubmit`. The same holds after a restart with `codex resume --last` (`source: resume`) ([`x-main/result.txt`](../../spikes/s8/evidence/x-main/result.txt): "SessionStart (first launch) with no terminal input: NO (30 s)"). |
| **4. No permission prompt in bypass mode, or a hook that reports one** | **Demonstrated** (no prompt in bypass mode). Reporting hook: also shown, and it can return the decision. | With `--dangerously-bypass-approvals-and-sandbox`, the shell command ran with no prompt. With `--ask-for-approval on-request --sandbox read-only` and an escalated command, the prompt `Would you like to run the following command? … Reason: …` appears, and `PermissionRequest` fires with `tool_name: "Bash"` and `tool_input: {command, description}`, where `description` is the model's justification ([`x-prompts/hooks.jsonl`](../../spikes/s8/evidence/x-prompts/hooks.jsonl)). An allow from the hook ran the command (`x-prompts-allow`). Codex has no `Notification` hook. Its events are `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Stop` and `Interrupt`. |

**Codex's shared app-server daemon (blocking for admind's relaunch).** By default (`daemon_auto_start`, stable, on), the Codex TUI connects to a background `codex app-server` that it installs and starts under `$CODEX_HOME/packages/app-server-daemon/`.
- The daemon outlives `tmux kill-session`. Two `codex` processes were left after every kill ([`x-rehook-1/ps-after-kill.txt`](../../spikes/s8/evidence/x-rehook-1/ps-after-kill.txt)).
- A turn that was running when its pane was killed carries on in the daemon.
- In the end-to-end run, the relaunched TUI (`codex resume --last`) attached to that live thread. The next prompt pasted into the **new** pane ran under the **old** launch's hook commands, so every one of its hook events carried the previous nonce. admind rightly ignored them as stale, so the new picker was never denied, and no `Stop` could count ([`e2e-codex.txt`](../../spikes/s8/evidence/e2e-codex.txt): 22/24, the two failures are "no CLI process outlives its tmux session" and "cycle works after relaunch").
- When the thread was idle at the kill, the new hooks did take effect after relaunch (`x-rehook-1`, `x-rehook-0`).
- With `--no-daemon`, no process survives the kill, and the full end-to-end run passes.

admind must launch Codex with `--no-daemon`, or with `daemon_auto_start` turned off in its `CODEX_HOME` (untested). Otherwise its relaunch and `!new` don't stop the old turn, and its nonce scheme breaks.

**Proposed alternative for capability 3 (Codex).** The ADR says an adapter that falls short is excluded unless S8 demonstrates an alternative. The candidate is below. It would need an ADR decision, since the harness pastes a prompt, and that is terminal input, though not from a person.
- The setup step trusts the hooks through config (`cx-trust-hooks.py`) right before each launch, as `e2e.py` does. Alternatively, the nonce is kept out of the hook command (for example in a per-launch file the hook reads), so the trusted hash stays valid.
- The preflight launches in the workdir, waits for the CLI to be ready, pastes one probe prompt and requires `SessionStart`, `UserPromptSubmit` and `Stop` from the current launch.
- The ready signal is the open question. The only one seen is the screen, which §2 rules out for decisions. A structured one would be the app-server's thread state, which is not tested here.

If this is accepted, it still leaves Codex's startup undetectable until admind sends something. If it isn't accepted, Codex is excluded as the admin agent under r14.

## End-to-end acceptance, host-free

`e2e.py` runs one real CLI on a private tmux server against its stub, with the Marmot side played by admind's `FakeWnAgent` (`tests/fakes/`), reached through admind's own `ControlClient`. Operators are scripted inbound frames: `alice` and `bob` are operators, `mallory` is a group member who isn't one. The hook command is `e2ehook.py <socket> <launch nonce>`. It is written fresh for every launch, and it forwards each payload with its nonce to the harness.

admind doesn't implement the §8.1 picker cycle yet, so the harness carries a minimal in-memory stand-in for it (`Relay`). That gives:
- one picker table;
- the denial with the picker ID, or the plain-text refusal when `redact()` would change the card;
- the card posted after the denial returns;
- answers by reply, `!answer` or a number-emoji reaction, operators only, first answer wins;
- the turn-ended mark, set only by a `Stop` carrying the current nonce;
- the progression rule P (answered AND mark);
- the delivery checks (the answerer is still an operator, the launch is still the picker's own);
- admind's paste, `UserPromptSubmit` marking delivery, the missing-`Stop` notice, `!asks cancel`, and relaunch settlement.

**What it demonstrates** is the CLI half of §8.1: each step that depends on the CLI behaves as the cycle needs, on the real CLI, with no host action at any step. That covers the denial, `Stop` after the denial, `UserPromptSubmit` on the pasted answer, no `Stop` on Esc, the hook command changing per launch, and resume. The stand-in's own logic is a sketch. admind's implementation still needs its own tests (the §15 list).

Runs ([`e2e-claude.txt`](../../spikes/s8/evidence/e2e-claude.txt), [`e2e-codex-nd.txt`](../../spikes/s8/evidence/e2e-codex-nd.txt), [`e2e-codex.txt`](../../spikes/s8/evidence/e2e-codex.txt), with each run's event log and posted messages in the matching `.json`):
- Claude Code: 26/26.
- Codex with `--no-daemon`: 24/24 (it has no launch-time `SessionStart` check, see capability 3).
- Codex default: 22/24 (the daemon finding above).

| S8 acceptance item | Result | How |
|---|---|---|
| Whole cycle: picker, denial, card, operator answer, `Stop`, answer pasted; received exactly once, attributed | **Shown**, both | Case 1. The stub log has exactly one request whose newest user text starts `[picker <id>, answered by alice by reply]`. |
| A non-operator's answer never reaching the agent | **Shown**, both | Case 3: mallory's reply and 1️⃣ reaction are dropped. Then bob's 1️⃣ reaction is delivered as `staging`. |
| An answer from a revoked operator never reaching the agent | **Shown**, both | Case 6 (revoked after selecting). |
| A second answer getting "already answered" | **Shown**, both | Case 2. bob's later reply gets `Picker <id> was already answered by alice.`, and it isn't pasted. |
| A picker with redactable content not posted, and its denial asking for plain text | **Shown**, both | Case 4 (`PICKSECRET`). No card and no picker row. The model received the plain-text denial. |
| An answer before `Stop`, and an answer after `Stop` | **Shown**, both. The "before the denial completes" ordering is **not in reach**. | Case 5: the answer arrives while the post-denial reply is still held. It is selected, not pasted, and then pasted after `Stop`. Case 1 is an answer after `Stop`. The card is posted only after the denial returns, so this harness can't answer earlier. |
| `!interrupt` instead of `Stop` not counting as completion | **Shown**, both | Case 7: Esc mid-turn gives no `Stop` (Codex fires `Interrupt`). The answer stays unpasted. |
| A missing `Stop` giving the notice and no paste | **Shown**, both | Case 7: exactly one missing-`Stop` notice after `picker_stop_seconds` (10 s here). Then `!asks cancel` closes the picker. |
| An operator revoked between selection and delivery, reopening the picker with the hold in force | **Shown**, both | Case 6: alice answers before `Stop` and is then revoked. The delivery check invalidates the answer and the picker reopens, keeping its turn-ended mark. bob's answer is then delivered at once. |
| A latch between selection and delivery | **Not in reach** | There's no latch in the stand-in. It is admind logic with no CLI dependency. |
| An admind crash before or after the paste, or before `UserPromptSubmit`; rebuild after restart | **Not in reach** | The stand-in has no store. These are admind store tests. The CLI-side fact they rely on (`UserPromptSubmit` fires once for the pasted text) is shown. |
| `!interrupt`, `!new` or a restarting `SessionStart` between reservation and acknowledgement | **Not in reach** | The window between paste and `UserPromptSubmit` is about 0.3 s here. Hitting it needs fault injection in admind. |
| A stale or below-floor `Stop`, or a `Stop` from another launch, not setting the mark | **Partly shown**, both | Case 8 injects a `Stop` with a foreign nonce, which is ignored. The Codex default run shows the real thing: a live old launch's hooks after relaunch are all ignored. The below-floor `Stop` is not in reach (no floor in the stand-in). |
| An uncertain paste giving `uncertain` and no resend | **Not in reach** | It needs a failing paste (admind fault injection). |
| `!asks cancel` and a superseding picker racing the reservation, and each arriving after it | **Partly shown** | `!asks cancel` before reservation (case 7). The races are not in reach. |
| A relaunch between answer and delivery abandoning the picker | **Shown**, both (Codex only with `--no-daemon`) | Case 9: alice answers, then the session is killed and relaunched with a new nonce. The picker is `abandoned` and never pasted, and bob's later answer gets "came from a previous agent session". Case 10: the next picker on the new launch completes by `!answer`. |
| **Startup failure path** (`not-started` hold, one `!new`, then break-glass) | **CLI side shown** (Claude). The hold and relaunch budget are **not in reach**. | With any one dialog not pre-accepted, Claude never reaches `SessionStart` (capability 3). The hold logic is admind's. For Codex, a missing `SessionStart` at launch is the normal case, so this path needs the alternative above. |

## Other findings

| Finding | Result | Evidence |
|---|---|---|
| **`wn-agent` sending and removing a reaction on an operator's message in admind's group** (ADR §17 item 8) | **Partly demonstrated, by earlier work; not rerun.** | The 2026-10-06 live harness ([`docs/reviews/2026-10-06-admind-live-harness.md`](../reviews/2026-10-06-admind-live-harness.md), "Reaction removed"; `tests/live/test_live_decisions.py::test_reaction_removed_is_ignored`) shows that wn-agent's `remove_reaction` works on real relays and that the group members receive `reaction_removed`. But there an *operator's* wn-agent removed its own 👍 from admind's card. admind's account adding and then removing a reaction on an *operator's* message was not shown. S4 lists `remove_reaction` as UNTESTED. S8 can't rerun it under its fakes-only rule, and `FakeWnAgent` has no reaction requests. **Answer to item 8:** the primitive works, but the progress-reaction shape is undemonstrated, so ship progress reactions **add-only**. Then add one live-harness case (admind reacts to an operator message, removes the reaction, and the other members see `reaction_removed`) before switching. |
| **Inbound number-emoji reactions on a picker card reaching admind** | **Shown with the fake only** | Case 3 decodes `reaction_added` with 1️⃣ through admind's `ControlClient`. S4 showed real `reaction_added` frames with `target_message_id_hex` matching the card, for 👍. A keycap emoji on real relays is not shown, which matters because it is a multi-codepoint sequence (digit, U+FE0F, U+20E3). |
| **tmux client attach and detach events on admind's tmux server** | **Demonstrated** (tmux 3.4) | Global `client-attached` and `client-detached` hooks fire on `attach` through a pseudo-terminal, and on both `detach-client` and a killed client: 2 and 2 events ([`t-attach/result.txt`](../../spikes/s8/evidence/t-attach/result.txt)). `#{client_pid}` and `#{client_name}` are set on attach. On detach, `#{client_name}` is empty. |

## Global configuration

No capability needed a change to global `~/.claude` or `~/.codex` settings. Everything was shown through per-run files:
- Claude: `.claude.json` and `.credentials.json` in `CLAUDE_CONFIG_DIR`, and the hook settings file passed with `--settings`.
- Codex: `config.toml` and `hooks.json` in `CODEX_HOME`.

Two Codex points affect admind's managed `CODEX_HOME`:
- it must write the hook-trust entries itself before each launch;
- it must disable the daemon or pass `--no-daemon`.

## Open items

1. **Codex capability 3**: accept or reject the probe-prompt preflight above (ADR decision), and look for a structured ready signal.
2. **Codex launch shape**: add `--no-daemon` (or turn off `daemon_auto_start` in admind's `CODEX_HOME`, untested), plus the per-launch hook-trust step or a nonce outside the hook command.
3. **Dialogs under a real login.** Both CLIs ran with dummy logins against stubs. A real subscription login might show more first-run screens (announcements, model or plan notices). The preflight on the reference host covers this, and it should run once per pinned version.
4. **Not in reach without admind's picker store**: latch, crash and restart cases, events between reservation and acknowledgement, uncertain paste, cancel and supersede races, below-floor `Stop`, and the startup-failure hold. These become admind tests once §8.1 is built, and `e2e.py`'s CLI half can be reused as their fixture.
5. **`remove_reaction` in admind's group, and keycap reactions on real relays**: one live-harness case each.
6. **Version drift**: tested on 2.1.286 and 0.160.0, while the ADR table names 2.1.283 and 0.157.0. Pin the versions the host preflight runs on.
