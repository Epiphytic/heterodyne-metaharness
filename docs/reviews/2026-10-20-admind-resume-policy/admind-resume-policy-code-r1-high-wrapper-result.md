# Verdict: REVISE

I ran this review read-only using the builtin `code-review` skill at HIGH. I also checked three claims myself against the frozen source, marked "confirmed" below. No candidate code, tests or probes were run, and nothing was written.

The core policy logic is correct:
- **Default deny holds.** `ensure_running` holds a recorded identity, a `relaunch=True` call or any non-`!new` pending state before it kills or spawns anything (`agent.py:143-150`).
- **A startup ack is readiness only.** `started()` accepts only the exact nonce/session/startup ack for the current launch, and never grants resume eligibility (`agent.py:191-196`).
- **Issued state is persisted before external effects,** and any failure while issuing becomes a hold (`agent.py:156-178`).

The problems are around that core. One of them breaks the once-consumed `!new` guarantee.

## Source PoC: critical and high findings (must fix)

1. **The `!new` intent is not once-consumed.** (Confirmed, `agent.py:139-140, 209-214`.) `new()` commits pending=new, the intent and a budget of 0 in one transaction. `launch_attempt_intent` is only written in a later transaction inside `ensure_running`. If the process crashes or the store fails between the two, a restart or `check_agent` sees `fresh=True` and launches a new conversation with no operator action. That breaks the design's guarantee that an interrupted issuance can't be replayed automatically.
   - Fix: pass `fresh` explicitly from `new()`, or consume the intent in `new()`'s own transaction.
2. **Operators get no reply during a hold** (`daemon.py:3130`). While `stuck` is set, `_flush` returns before both the "Not delivered…" reply and the NOT_READY reply. Messages pile up in `held` with no answer, which is the hang `test_admind_t9r1.py:106` guards against. The design's "Persist HOLD and notify" is not met.
3. **A hold found at startup is silent** (`daemon.py:2209`). `start_agent(startup=True)` → AgentStuck sets `stuck` and writes only a `start-failed` audit line. No Marmot notice goes out, and `check_agent` returns early once `stuck` is set. Together with #2, the operator never learns that `!new` is needed.
4. **Deploying this forces a fresh conversation** (`daemon.py:2219`). A healthy pane launched before this change has no `launch_kind`/`launch_session`/`launch_ack_*`. It is adopted but `startup_acknowledged()` never passes, so after `ready_timeout` it gets a false `launch-unacknowledged` hold. The only way out is `!new`, which discards the original conversation this work is meant to protect. The same happens if a SessionStart is lost while the daemon restarts.
5. **The 269-PASS claim doesn't cover existing tests.** (Confirmed by grep.) `test_admind_t9r1/t9r2/t9r3/t9r5` (and per the skill, t9r9/t9r10) still call `start_agent(relaunch=True)`, expect "launch 2"/`source="resume"` and assert the "Not delivered" text. None of these files are in the pinned scope or were migrated. They will very likely fail against the candidate (inferred, not run), so the verification evidence is incomplete.

## Medium

6. **Stale prompts replay into the new conversation** (`daemon.py:3101`). Prompts held through a long hold are pasted automatically into the post-`!new` conversation, which runs unsandboxed with permission prompts bypassed. They may refer to the original conversation's context. Before this change they were dropped with a notice.
7. **The three-launch ceiling is dead code.** (Confirmed, `agent.py:151-153`.) A fresh launch always follows `new()` resetting the counter to 0. A non-fresh launch is only reached when no recorded keys exist, so the counter is unset. `launches >= 3` can never be true, and `test_crash_loop_stops_after_three_launches` no longer tests any ceiling.

## Low / cleanup

8. **`check_agent` makes a pointless call** (`daemon.py:2274`). It calls `start_agent(relaunch=True)` only to get a refusal that is guaranteed. That costs a thread hop, writes a misleading `start-failed` audit and resets `launched_at`/`not_ready_sent`.
9. **Login-change code is now unreachable** (`daemon.py:197`): `LOGIN_CHANGED_WHY`, `abandon_in_flight(notice=True)` and the `pending_notice` allocation. The au11 tests seed state by hand to exercise it.
10. **`recovery_status()` is incomplete** (`agent.py:108`). It doesn't say whether the conversation is original or was replaced by `!new` (design §4). It also reports "acknowledgement pending" when the daemon is stuck from a failure that wrote no hold.

## Live qualification gaps (out of scope for this PoC)

These are already listed in the report; none of the findings above depend on them:
- No positive resume producer exists, so resume is unqualified by design.
- No live tmux/Claude/hook qualification has been run.
- There is no deployment or migration plan for production store state. #4 is the source-level reason one is needed before activation.

`★ Insight ─────────────────────────────────────`
- In #1, "once-consumed" is a claim about what persists across a crash, not about the order of calls. The intent and its consumption marker must commit in the same store transaction, or a restart sees an accepted but unconsumed intent and treats it as live.
- In #4 and #7, adding new store keys silently splits deployments into old-key and new-key states. Every guard reading those keys needs a defined meaning for "key absent", or it becomes either dead (#7) or a trap for existing deployments (#4).
`─────────────────────────────────────────────────`
