Skill "code-review" completed (forked execution).

Result:
**Verdict: REVISE.** This is the second full high round on the source proof-of-concept. It covers the 20 pinned files, with the patch read against the frozen worktree. I stayed read-only throughout: no edits, nothing executed, no subagents spawned, Bash used only to read source. The first-round findings marked OPEN (legacy readiness migration, the dead three-launch ceiling, legacy login-drain) are not repeated below; finding 2 overlaps HIGH1 #3 but comes from a different cause.

The default-deny core holds up. Automatic callers refuse before kill or spawn, and a crash after `!new` is held, not replayed. The most serious new problem is that recovery now trusts tmux probes too much. `Tmux.has_session` returns False on any tmux error, so one transient failure is read as a dead agent. That writes a lasting HOLD, and the only way out is `!new`, which throws away the healthy original conversation.

These findings apply only to the source proof-of-concept. Activation and deployment qualification is separate and still open: legacy migration, a real resume producer, live checks, and a clean full-suite run before merge.

```json
[
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2272,
    "summary": "check_agent treats a single false `alive()` as pane death and writes a lasting launch HOLD, but `Tmux.has_session` returns False on ANY tmux failure, so one transient probe error permanently ends a healthy conversation.",
    "failure_scenario": "The agent is acknowledged, ready and mid-conversation. One 5-second poll's `tmux has-session` exits non-zero (server briefly busy, EAGAIN on fork, socket hiccup), so has_session returns False and alive=False. check_agent runs hold_launch('resume-eligibility-unqualified'), sets stuck and posts the notice. The hold survives restarts and only `!new` clears it, which kills the live pane and loses the original conversation. The old code would have resumed the same session. The same false negative in ensure_running's adoption probe at daemon restart (agent.py:140) holds a live, acknowledged pane. A sticky HOLD needs a confirmed death (e.g. `session_absent` or a repeated probe), not one has_session result."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2210,
    "summary": "start_agent persists `hold_launch('startup-failed')` for any exception from ensure_running, turning transient failures into a hold that survives restarts and that only `!new` can clear.",
    "failure_scenario": "On daemon restart, or on the `_flush` TmuxError path (line 3200), ensure_running's adoption probe hits `pane_dead`. That probe raises TmuxError or subprocess.TimeoutExpired (15 s), or a SQLite read raises sqlite3.OperationalError('database is locked'). Before this change that was in-memory `stuck`, and the next restart would adopt or resume. Now `launch_hold` is committed, so every later restart refuses adoption of the still-healthy, acknowledged pane. The operator must `!new` and lose the conversation, even though nothing about the agent was uncertain."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2201,
    "summary": "Removing the startup login-change replacement leaves a live pane running on the de-configured login. Its in-flight turn is not abandoned and its hooks are still processed, and the operator never gets the login-changed notice.",
    "failure_scenario": "The operator switches agent_account (e.g. revoking a compromised or expired login) and restarts admind. ensure_running refuses adoption (login mismatch) but never kills the pane. `classify` checks only session and nonce, not login, so the old-login pane's UserPromptSubmit/Stop events stay 'current'. Its turn keeps running unsandboxed with permissions bypassed under the old credentials, and its reply is posted. The only notice is the generic 'recovery held (resume-eligibility-unqualified)' text; LOGIN_CHANGED_NOTICE is never posted (au11 test now asserts notices == [])."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3517,
    "summary": "on_session_start's refusal persists launch_hold but leaves the daemon's in-memory `stuck` unset and posts no notice. The two hold flags diverge, and the operator is told messages are queued when they can never be dispatched.",
    "failure_scenario": "A fresh `!new` launch reports SessionStart with source='resume'/'clear'/unknown, or any `compact` before acknowledgement. launch_hold is set and ready cleared, but stuck stays None. For up to READY_TIMEOUT (120 s) `_flush` sends NOT_READY ('your message is queued') and `!status` shows HOLD while the dispatcher acts as merely not-ready. The stuck notice only appears when check_agent's timeout finally fires. Root fix: derive `stuck` from the persisted hold (one source of truth) and notify at the point the hold is written."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3145,
    "summary": "While stuck, held prompts are kept in memory but can never be delivered: `stuck` is cleared only by `!new` (which settles them as not-delivered) or by a restart (which recover() drops with RESTARTED_NOTICE). Every flush re-sends a reply for every held message.",
    "failure_scenario": "During a HOLD the operator sends 20 messages over hours. Each gets 'Your message is held', which implies later delivery, but no path ever delivers them, and each later gets a second 'not delivered' or 'restarted' notice. Every `_flush` (each hook idle edge, each new message) walks the whole growing `held` list, calling reply_target, chunking and enqueue for each; the outbox de-duplicates by key, but that is still O(N) store work per flush. Settling the message once when it is queued (finish 'dropped') would be both honest and cheaper."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3523,
    "summary": "A matching 'startup' SessionStart during a busy turn no longer abandons the turn. If the pane's claude genuinely restarted, busy/in_flight/anchor stay set forever and the queue never drains.",
    "failure_scenario": "The claude process in the current pane restarts with the same settings file, nonce and session (e.g. an in-pane restart or self-update) while a turn is reserved. The old code treated a startup of a busy session as a restart and abandoned the turn. Now started() re-acknowledges, mark_ready runs, and busy/in_flight are kept. That turn's Stop never arrives, check_agent sees the agent ready and alive, and dispatch is blocked until the operator notices and sends !interrupt or !new. No notice beyond the long-turn/unconfirmed messages is posted."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2213,
    "summary": "start_agent's notify_stuck uses a fresh `stuck_seq` key every time, so each daemon restart with a hold already persisted posts a new Marmot notice. The notice text also nests admind-internal wording.",
    "failure_scenario": "admind crash-loops for an unrelated reason (a guarded loop failing, systemd Restart=always) while launch_hold is set. Each start refuses and posts a new 'agent-stuck:N' notice, flooding Liam's phone. The text reads 'The admin agent is not running (admin agent recovery held (resume-eligibility-unqualified); use !tail, then !new). Use !tail, then !new.' That repeats the instruction, exposes internal reason tokens, and says 'not running' even when the pane is alive (timeout case). A key fixed per hold episode would let the outbox de-duplicate it."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3111,
    "summary": "!new settles every held message as 'the original conversation was replaced by !new' BEFORE agent.new() runs. If the replacement fails, the operator is told something false, and held messages from revoked senders get a reply instead of the deny path.",
    "failure_scenario": "The operator sends !new; tmux kill/new_session raises inside `_request_startup`, execute returns ok=False and the daemon becomes stuck. Every held message has already been marked done with 'replaced by !new. Resend with the context…', but no fresh conversation exists. A held message whose sender was revoked while it waited is also answered and marked 'done', not dropped/UNVERIFIED as `_flush`'s sender_current/deny path does."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 143,
    "summary": "Much of the new launch state is redundant or write-only. launch_new_intent/launch_attempt_intent are never consumed or cleared, launch_kind is always 'startup', launch_session always equals agent_session, launch_ack_session repeats the checked session, and launch_resume_eligibility is the constant 'unqualified'.",
    "failure_scenario": "After any successful !new, launch_new_intent stays set forever, so the 'pending == new and intent' branch cannot tell a current pending !new from a stale one. A crash after the !new handler's abandon_in_flight(replace='new') but before new()'s transaction is mislabelled 'launch-unacknowledged' even though nothing was launched. Both branches refuse anyway, so the intent check only changes the reason string. Six extra keys must be kept in sync in `recorded`, `startup_acknowledged` and `started`; launch_nonce plus launch_ack_nonce plus replace_pending would carry the same binding."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 493,
    "summary": "is_ready() and mark_ready() now call startup_acknowledged() on every call, about nine SQLite point reads, on hot paths (every _flush, twice per check_agent poll).",
    "failure_scenario": "`_flush` runs after every hook event, inbound message and idle edge, and calls is_ready() each time. check_agent calls it twice every 5 s. Each call reads launch_nonce, agent_account, launch_kind, launch_session, agent_session (twice), launch_ack_nonce, launch_ack_session and launch_hold. ready is only ever set through mark_ready, which already verified acknowledgement, so clearing `ready` inside hold_launch (or caching the acknowledgement next to ready_nonce) gives the same guarantee without repeated store reads."
  }
]
```

There are three lower-priority items I left out to stay within the 10-finding cap:
- **`relaunch` is never True in production.** No production caller passes `relaunch=True` to `start_agent` or `ensure_running` any more.
- **Dead branch in the worker.** In `fulfillment_projection.worker`, the `except Exception: break` is now unreachable, because `asyncio.wait` never raises the future's exception.
- **Stale operator docs.** `docs/admind.md` lines 179 and 263 still describe automatic relaunch and resume. That file is outside the pinned 20.
