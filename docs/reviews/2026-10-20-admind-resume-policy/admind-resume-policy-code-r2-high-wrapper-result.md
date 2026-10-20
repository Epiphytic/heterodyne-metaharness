**Verdict: REVISE.** This is the second full high round on the source proof-of-concept, run through the built-in `code-review` skill. It covered the 20 pinned files, reading `candidate.patch` against the frozen worktree. It stayed read-only: no edits, nothing executed, no subagents, Bash used only to read source. I'm passing on its results; I didn't re-check each finding against the source myself. The first round's OPEN findings are carried over unchanged and not repeated here: legacy readiness migration, the dead three-launch ceiling and legacy login-drain.

**What holds up:** the default-deny core. Automatic callers refuse before any kill or spawn, and a crash after `!new` puts the agent on hold rather than replaying the old prompt.

**What fails:** recovery now trusts a single tmux probe too much. One transient tmux error can lock a healthy agent, and the only way out is `!new`, which throws away the conversation.

## Findings (most severe first)

1. **High – `daemon.py:2272`.** `check_agent` writes a lasting hold after one false `alive()` result. `Tmux.has_session` returns False on *any* tmux error, so one transient probe failure (busy server, fork error, socket hiccup) locks a healthy, acknowledged agent. The hold survives restarts and only `!new` clears it. The same thing happens in the adoption probe at `agent.py:140`. A hold should need a confirmed death, such as `session_absent` or a repeated probe.
2. **High – `daemon.py:2210`.** `start_agent` writes a lasting `startup-failed` hold for *any* exception from `ensure_running`, including `TmuxError`, a 15-second timeout, or SQLite "database is locked". Before this change that was in-memory only and cleared on restart. This overlaps first-round finding #3 but has a different cause.
3. **High – `daemon.py:2201`.** With the startup login-change replacement removed, a pane on a de-configured login keeps running. `classify` doesn't check the login, so the old pane's turn keeps going under the revoked credentials and its reply still gets posted. The login-changed notice is never sent; the au11 test now asserts `notices == []`.
4. **Medium – `daemon.py:3517`.** When `on_session_start` refuses, it saves the hold but leaves the in-memory `stuck` flag unset and posts no notice. For up to 120 seconds the operator is told "your message is queued" while `!status` shows HOLD.
5. **Medium – `daemon.py:3145`.** While stuck, held messages can never be delivered, but each is told "Your message is held". Each later gets a second "not delivered" or "restarted" notice, and every flush re-walks the whole held list.
6. **Medium – `daemon.py:3523`.** A matching `startup` SessionStart during a busy turn no longer abandons that turn. If claude really restarted in the same pane, the turn stays marked busy forever and the queue never drains.
7. **Medium – `daemon.py:2213`.** Each restart with a hold already saved posts a new stuck notice, because the key changes every time, so a crash loop floods the phone. The text also nests and repeats itself, exposes internal reason codes, and says "not running" even when the pane is alive.
8. **Medium – `daemon.py:3111`.** `!new` tells every held message "replaced by !new" *before* `agent.new()` runs. If the replacement fails, that message is false. Held messages from senders who have since been revoked get a reply instead of going through the deny path.
9. **Low – `agent.py:143`.** Much of the new launch state is redundant or never read. `launch_new_intent` is never cleared, so a stale value can't be told apart from a current one, and its only effect is the reason text. Six extra keys have to be kept in sync.
10. **Low – `daemon.py:493`.** `is_ready()` and `mark_ready()` do about nine SQLite reads on every call, on hot paths (every flush, twice per 5-second poll).

**Left out to stay within the 10-finding cap (Low):**
- No production caller passes `relaunch=True` any more.
- `except Exception: break` in `fulfillment_projection.worker` can't be reached.
- `docs/admind.md` lines 179 and 263 still describe automatic relaunch/resume; that file is outside the pinned 20.

## Source PoC vs. activation/deployment
- **Source PoC: REVISE.** Findings 1–3 should be fixed before approval. 4–8 are correctness and operator-experience problems worth fixing in the same pass.
- **Activation/deployment qualification: not assessed, still open.** That includes legacy migration, a real resume producer and SessionStart qualification, live checks, and a clean full-suite run before merge (the earlier run had 6 failures and the full rerun isn't finished). No all-green claim is made. The 10 first-round findings and the 53 open quality gates stay as they were.
