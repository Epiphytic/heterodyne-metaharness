Skill "code-review" completed (forked execution).

Result:
Verdict: **REVISE.** I followed the read-only restrictions: I only read the patch, the design, the report and the frozen source. Nothing was executed, written or edited.

The core default-deny logic in `ensure_running` holds up. Automatic callers refuse before kill or spawn, and a crash after issuance is held, not replayed. A startup acknowledgement no longer resets the launch counter. The main problems are around it:

- **Operator messages go unanswered.** While the agent is stuck, messages get no reply at all. A hold found at daemon startup posts no notice either. The design requires "Persist HOLD and notify".
- **Deploying this forces a fresh conversation.** A healthy live pane from before the change is adopted but can never become ready, so it is held after the ready timeout. That forces `!new` and discards the original conversation this work is meant to protect.
- **The 269-PASS verification is incomplete.** At least six existing test files still expect the removed relaunch and drop behaviour, and none of them was run or migrated.
- **The `!new` intent is not "once-consumed" as claimed.** A crash between `new()`'s two store transactions lets an automatic caller launch the fresh conversation.

None of this touches the separate gaps the report already lists: no positive resume producer and no live qualification.

```json
[
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3130,
    "summary": "While `stuck` is set, `_flush` now returns before both the old 'Not delivered ... use !tail, then !new' reply and the NOT_READY reply, so held operator messages get no response at all.",
    "failure_scenario": "The pane dies, check_agent holds and posts one agent-stuck notice. Every later operator message is appended to `held` and never answered. That is the hang that tests/test_admind_t9r1.py:106 says must not happen ('a message now gets the stuck reply, not a hang'). The design's 'Persist HOLD and notify' is not met for these messages."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2209,
    "summary": "A HOLD found at daemon startup (`start_agent(startup=True)` gets AgentStuck) sets `self.stuck` but never posts the agent-stuck notice, and check_agent returns early whenever `stuck` is already set.",
    "failure_scenario": "The daemon restarts with a persisted `launch_hold`, or after the pane died while the daemon was down. ensure_running refuses, `stuck` is set and only a 'start-failed' audit line is written. No Marmot notice is sent, and per the previous finding later messages get no reply either. The operator never learns that `!new` is needed."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2219,
    "summary": "Adopted panes become ready only if `startup_acknowledged()` passes, and that needs the new launch_kind/launch_session/launch_ack_* keys. A live, already-acknowledged pane launched before this change, or one whose SessionStart was lost during a daemon restart, can never become ready and is automatically held.",
    "failure_scenario": "Deploy onto production state (session_started, launch_nonce, agent_account; no launch_kind). The pane is adopted, `mark_ready` is skipped and `is_ready()` stays False. After ready_timeout, check_agent writes a sticky `launch-unacknowledged` hold. That label is false, and the only way out is `!new`, which discards the healthy original conversation."
  },
  {
    "file": "tests/test_admind_t9r1.py",
    "line": 107,
    "summary": "Existing tests that encode the removed relaunch/resume/drop behaviour were neither migrated nor run. The 269-PASS composed run covered only the agent, r1, au11 and commands test files.",
    "failure_scenario": "Files affected: test_admind_t9r1.py (expects 3 relaunches and the 'Not delivered' reply), t9r2.py:218 ('supervision resumes S1: launch 2', source=resume), t9r3/t9r5/t9r9/t9r10 (start_agent(relaunch=True) expecting launches) and possibly test_admind_daemon.py. They fail against the candidate or silently assert removed behaviour, so the claimed verification and the 'byte-exact unchanged' scope hide a red suite."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 140,
    "summary": "Any automatic caller infers an explicit fresh launch from store state (`pending == 'new'` with an unconsumed `launch_new_intent`). The intent is accepted in `new()`'s first transaction but consumed only in ensure_running's later issuance transaction, so the two are not atomic.",
    "failure_scenario": "The process dies, or the issuance transaction raises (sqlite busy, disk error), after new() commits replace_pending=new, intent X, hold cleared and budget 0, but before launch_attempt_intent=X is written. On restart, start_agent(startup=True) or check_agent calls ensure_running and gets fresh=True. It then automatically kills the pane and launches a new conversation with no operator action, contradicting 'interrupted issuance cannot be automatically replayed'. Fix: pass freshness explicitly from new(), or consume the intent in new()'s own transaction."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3101,
    "summary": "Held prompts are kept indefinitely during a HOLD, and `!new` abandons only the in-flight message. Every queued prompt is then automatically pasted into the brand-new conversation, which runs unsandboxed with permission prompts bypassed.",
    "failure_scenario": "Agent held for hours while the operator sends 5 prompts that refer to the original conversation's context. The operator issues !new, the fresh agent acknowledges startup, and _flush dispatches all 5 stale prompts one by one into a conversation with no context. Previously they were dropped with an explicit notice."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 152,
    "summary": "The `launch-budget-exhausted` branch and MAX_LAUNCHES_WITHOUT_START can never fire: every path that reaches the check has launches_without_start equal to 0, so the counter never goes above 1.",
    "failure_scenario": "Fresh launches come only from new(), which sets the counter to 0 in the same transaction. Non-fresh launches happen only when the store has no counter key. So `launches >= 3` is never true. The 'existing three-launch outer ceiling' kept by the design is dead code, and test_crash_loop_stops_after_three_launches no longer tests any ceiling."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2274,
    "summary": "check_agent calls `start_agent(relaunch=True)` only to get a refusal that is guaranteed (the hold was just written and relaunch always refuses). That costs a thread hop, resets launched_at and not_ready_sent, writes a misleading 'start-failed' audit and runs flush_pending_notice.",
    "failure_scenario": "Every death or timeout HOLD adds a pointless worker-thread round trip and a spurious start-failed audit row. Setting `self.stuck` from the hold reason directly would do the same job without hiding the policy behind a fake launch attempt."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 197,
    "summary": "The login-change replacement machinery is now dead: LOGIN_CHANGED_WHY is unused, nothing passes `notice=True` to abandon_in_flight, and `pending_notice` is never allocated, so flush_pending_notice only drains legacy state.",
    "failure_scenario": "The unused constant, the `notice` parameter with its pending_notice/login_seq allocation, and the flush run after every start remain as maintenance burden. The au11 tests now seed pending_notice by hand to keep exercising code no production path reaches."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 108,
    "summary": "`recovery_status()` does not say whether the conversation is the original or an explicitly replaced one, as design §4 requires. It also reports 'startup acknowledgement pending' when the daemon is stuck for a reason that wrote no hold (for example a store or settings failure before the try block).",
    "failure_scenario": "After `!new`, `!status` shows 'startup-ready; future resume unqualified' with no indication that the original conversation was replaced. If ensure_running raises before its try block, the daemon is stuck but `!status` shows 'acknowledgement pending' instead of HOLD."
  }
]
```
