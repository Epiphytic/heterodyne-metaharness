# Opus HIGH design review: `ee3cb01` host admin Codex migration

**Inputs:** `docs/designs/2026-11-03-host-admin-codex-migration.md` at `ee3cb01`, reviewed against the source at `dcb6df07`, plus the operator directive `admin-codex-yolo-directive-20261103.md`.

**What I did:** I read the source only. The one external step was generating the local Codex 0.166.0 app-server JSON schema offline into a temp directory, which I then deleted. I made no edits, model calls, or service/queue/config changes, and used no subagents.

**Verdict:** The design is not ready for the human decision yet. It has four critical flaws, all fixable in one incorporation pass, and none of them reopens the operator's choice. This review clears no code or deployment.

## Fixed operator choice: kept, not reopened
The choices stand as given: Codex `--dangerously-bypass-approvals-and-sandbox`, gpt-6.1-sol, medium reasoning, approval `never`, `danger-full-access`, for the admin executor only, with Claude as reviewer and summarizer. Nothing below asks Liam to choose again. PR301 (owner-deadline recovery) and PR302 (Ask bridge) are untouched by every correction.

## Already sound (keep)
- Configuration alone can't switch the executor: `settings.py:28,163` and the use of Claude's `interactive_argv` in `agent.py:232`.
- Policy is checked from runtime responses, not argv. No synthetic Claude hooks, no screen scraping, no transcript fallback.
- `turn/start` is never resent; an RPC error counts as "no effect" only with explicit proof of rejection.
- Native IDs and Marmot message IDs stay separate; no Claude fallback; the live completion gate is concrete.
- Reusing the `fake_agent_cli` app-server path rather than writing a second fake.

## Critical flaws

**C1. A stdio child of admind dies with admind, and that breaks the unit contract.**
`unit.py:19-21` deliberately keeps the admin agent outside admind's cgroup (`KillMode=control-group`, `Restart=always`), and `agent.py:28` `tmux_launcher` exists for that reason. A stdio app-server child would be SIGTERMed on every admind stop, restart, crash or deployment. The admin's job is host installs and repairs, which can include restarting admind, so it would kill itself mid-command and leave the channel held after every restart.
- **Correction:** launch `codex --dangerously-bypass-approvals-and-sandbox --no-daemon app-server --listen unix://<state_dir>/codex/app.sock` inside the existing private tmux session `admin`, through the existing `tmux_launcher` scope and `TmuxLike.new_session(..., set=account.set, unset=account.unset)`. The socket goes in a 0700 directory and must fit `MAX_SOCKET_PATH`. Local 0.166.0 accepts `--listen unix://PATH`.
- admind connects as a client and reuses `alive`, `pane_dead`, `kill` and `tail`.
- After an admind restart, reconnecting plus `thread/resume` is not yet qualified. Until it is, use the existing `hold_adopted` behaviour: dispatch is held until a current turn completes, `!interrupt`, or `!new`. Never relaunch automatically.
- Qualify that `--no-daemon` with a unix listener never binds or attaches to `/tmp/codex-daemon-<uid>`. `agents/codex.py` (the `facts` comment) documents that 0.160 did this, and the unrelated host controller may own that daemon.

**C2. The migration order doesn't work with the restart that deployment forces.**
The migration operation exists only in the new release, so it can only run after admind restarts. On that start, `recover()` (`daemon.py:751-778`, called at `:806`) runs first:
- it abandons `in_flight`;
- it marks every `received` or `executing` inbound message `dropped` and posts `RESTARTED_NOTICE`;
- the held queue is in memory only (`self.held`).

So the fence and snapshot in step 2 and the "never-issued queued inputs become eligible for the new executor" in step 6 can't happen.
- **Correction:** state that deployment applies the existing `recover()` semantics. Each message is answered once and never replayed, which already satisfies "do not replay uncertain input". Drop the new quarantine-and-requeue state and the new fence; the existing `stuck` / `launch_hold` gate in `_flush` already freezes dispatch while asks, decisions, ingress and the outbox keep working.
- Make a precondition explicit: deploy only after read-only evidence shows `busy`, `in_flight`, `hook_lost` and `launch_hold` are clear. Once config selects Codex, `classify` returns `stale` for the old Claude session (`daemon.py:4305-4315`), so its Stop can never clear `busy` again.
- Asks, maintenance-recovery attempts, fulfillment rows and held turns are durable and unaffected. Verify them as unchanged; don't migrate them.

**C3. The migration op's authority can be forged.**
`ctl.py`'s module docstring says anything that can reach the socket "can already act as admind". The caller here is a same-uid Codex `--yolo` controller, so a caller-supplied "pinned deployment decision reference" is self-asserted. In effect it is an unauthenticated `!new`.
- **Correction:** the op must check and consume, in the same Store transaction that writes `launch_new_intent`, an admind-store `AskRow` (`store.py:244`) of kind `approval`. That row must be approved, `decided_by` an operator, unconsumed, and have a `digest` equal to the exact migration descriptor: target release, config digest, adapter/model/effort/policy tuple, and old session reference.
- Tie it to the one deployment-authorization approval, so Liam isn't asked twice and the authority is used once.
- Do the replacement through the existing `Admind.new_session` / `AdminAgent.new()` steps (`daemon.py:3948-3994`): retire the old session ID, `invalidate_launch`, finish held messages as not delivered, `abandon_in_flight(replace="new")`. Don't build a parallel kill/launch path. Authenticated `!new` remains a valid alternative.

**C4. Two sources of truth for which executor is selected.**
Step 5 "publishes" the Codex profile choice in the Store, but `settings.resolve` reads `[admind] profile` from `config.toml` on every start. Nothing says which wins after the next restart, so the channel could silently revert to Claude or diverge.
- **Correction:** `config.toml [admind] profile` stays the only selector. Add a Codex profile (`adapter = "codex"`, the model, `reasoning_effort = "medium"`; that key is already typed in `profile_args.py`) and switch to it under the deployment authorization. The Store records only the observed binding.
- The existing guard against adopting the old Claude pane is already sufficient: `credential_key` includes the adapter (`accounts.py:41-45`), so `login_matches()` fails under Codex config, and `start_agent` posts "login changed" and holds (`daemon.py:2835-2860`). Name this as the supported fence.

## Important (fix in the same pass)

**I1. The executor seam is in `daemon.py`, not `agent.py`.**
The turn state (`in_flight`, `anchor`, `busy`, `stopped_flight`, `turn_floor`, `generation`) and the `_flush` checks (`pending_hooks`, `hooks.empty`, `backlog_waiting`; `daemon.py:4032-4045`) are all driven by hooks. Specify a typed executor event (not a `HookEvent`) handled under `turn_lock`:
- **TurnAccepted** (launch, thread, turn, request ID): sets `anchor = in_flight` by the request it answers, replacing `same_prompt` text matching.
- **TurnCompleted**: enters `on_stop`'s `record()` with `need_fallback=False`.
- `_flush` must also wait while the transport holds pending or unattributed events.
- `send` maps "nothing written" to the `TmuxError` path (message re-held) and "written" to `paste_uncertain`.
- `daemon.interrupt` (`:3919-3946`) abandons immediately because Claude never sends Stop on interrupt. Codex does send `turn/completed` with status `interrupted`, so either wait for it or make the late completion stale by turn ID.
- `CommandRunner` runs synchronously in a thread (`commands.py:129-140`); the RPC client needs a thread-safe bridge.
- `!details full` becomes `NO_TOOL_CALLS` because there's no transcript path. Document that.

**I2. Session identity.**
`_request_startup` mints `agent_session = uuid` before spawning (`agent.py:218`), but Codex assigns the thread ID. Keep the launch ID until `thread/start` returns, then bind the thread ID in the same transaction as the acknowledgement. Update `started`, `startup_acknowledged` and `session_current` to match on launch nonce plus thread ID.

**I3. User-wide Codex config leaks in.**
The `default` Codex account unsets `CODEX_HOME`, so the executor uses `~/.codex`, the same home as the host controller. Its hooks, notify, `mcp_servers`, `profile`, `model_provider` and base URLs would all apply. The managed adapter strips these through `owned_keys`; this design doesn't.
- **Correction:** before `thread/start`, do a read-only check with `config/read` or `--print-startup-config`, and hold on any of those keys outside an expected set.
- Pin with `-c`: approval policy, sandbox, model, effort, and `features.daemon_auto_start=false`. Use `--strict-config`. Never use `--dangerously-bypass-hook-trust`.
- `LOGIN_OVERRIDES["codex"]` is marked provisional in `settings.py:34`; qualify it before relying on it.

**I4. Protocol facts from the local 0.166.0 schema.**
- `ThreadStartResponse` returns `approvalPolicy`, `sandbox`, `model`, `reasoningEffort` and `approvalsReviewer`; check all five.
- Pin `approvalsReviewer` explicitly to `user`. Its `auto_review` value means an LLM subagent makes approval decisions.
- `AskForApproval` also has a "granular" object form, so require the exact string `never`.
- Set `ephemeral = false` so the rollout is kept for audit.
- `TurnStartParams.clientUserMessageId` exists; set it, but it isn't echoed in any event, so it isn't proof of correlation.
- Forbid `turn/steer`.
- Server requests include `item/tool/requestUserInput`, `mcpServer/elicitation/request`, `account/chatgptAuthTokens/refresh`, `item/tool/call`, `attestation/generate` and the approval requests. For each, record a hold and also send a bounded error or decline, otherwise the turn hangs forever.
- `TurnStatus` includes `inProgress`.

**I5. Summarizer split, made concrete.**
Split the adapter list into an executor list (`claude-code`, `codex`) and a summarizer list (`claude-code` only), checked in `_profile`. `headless_argv` (`daemon.py:459`) stays Claude-only.

## PoC advisories (don't block)
- **Version pinning:** pin the host 0.166.0 binary digest alongside the managed adapter's declared 0.160.0, and keep the offline `generate-json-schema` output from that exact binary as the fixture the extended fake must match.
- **Fake coverage:** `fake_agent_cli`'s stdio server only answers `initialize`, `hooks/list` and rate limits. It needs `thread/start`, `turn/start`, notifications, server requests and fault cases.
- **Handoff:** pass the controller handoff through `developerInstructions` at `thread/start` so no tool turn runs before authorization. Workdir `~/.hermes` already loads its `AGENTS.md`.
- **Tests:** add an admind restart in the middle of a Codex turn; the late completion should be quarantined via `hold_untrusted_turn`.
- **Live verification:** add the `approvalsReviewer` value and the effective-config read to the evidence list.

## Handoff
The author incorporates C1–C4 and I1–I5 once, then publishes these findings with their dispositions, then makes one exact-artifact human design decision through admind. Code review and deployment authorization remain separate gates.
