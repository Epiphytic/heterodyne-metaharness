Skill "code-review" completed (forked execution).

Result:
**Verdict: APPROVE for the source-only PoC, provided the 102-module broad run finishes clean.** It is not approved for activation or deployment.

I found no critical flaw in the pinned source. Automatic recovery is refused before any kill or spawn. A launch that was issued or interrupted by a crash is held rather than replayed. Hooks from an old login are treated as stale, and replies from them are posted with "unknown" ownership, which the source documents as held. A startup during a busy turn, or a startup that can't be qualified, now posts a hold notice straight away, keyed so it isn't repeated. `alive()` now treats a tmux failure as unknown liveness instead of reading it as a dead pane.

The worst finding is outside the 22 pinned files: the live harness's fake tmux (`StubTmux`) lacks the new `session_absent` method. The legacy-store upgrade is the main reason this can't be activated yet. The review stayed read-only: nothing was run, edited or spawned. I couldn't invoke the code-review skill as the README asked, because this review is already that skill's forked run. I followed its recipe directly instead.

Separating the two scopes:
- **Source PoC:** items 3, 5 and 7–10 below. Prior findings 5, 9 and 10 are still open, as the dispositions say.
- **Activation and deployment (still open):** items 1, 2, 4 and 6, the missing real resume producer, live checks, and the terminal broad result.

```json
[
  {
    "file": "tests/live/stub_admind.py",
    "line": 25,
    "summary": "StubTmux (the live harness's TmuxLike) was not given the new session_absent method, but AdminAgent.alive() now calls tmux.session_absent, so the live harness breaks.",
    "failure_scenario": "At live-harness startup the store is fresh, so ensure_running's adoption check evaluates self.alive() and gets AttributeError. start_agent catches it as a generic exception and leaves the daemon stuck, so the stub agent never launches. If a launch somehow happened, check_agent would call agent.alive and the AttributeError would escape its (TmuxError, OSError, TimeoutExpired) handler and kill agent_loop. Only the two pinned fakes (test_admind_agent.py, test_admind_r1.py) were updated."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 112,
    "summary": "startup_acknowledged() needs launch_kind, launch_session, launch_ack_nonce and launch_ack_session, which no pre-upgrade store has, so on the first deploy the adopted live pane is never ready and is held 120 s later.",
    "failure_scenario": "An existing deployment upgrades with a healthy, idle, acknowledged pane. ensure_running returns 'adopted', but startup_acknowledged() is False, so mark_ready never runs. The pane can't be acknowledged again because no new SessionStart will come. After ready_timeout, check_agent writes launch_hold('launch-unacknowledged'), and only !new clears it, which throws away the conversation. Every upgraded install ends up here. The legacy migration is still OPEN, so this blocks activation."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2689,
    "summary": "A transient error during the adoption probe (TmuxError or timeout from session_absent or pane_dead) sets in-memory `stuck` that nothing retries. check_agent returns early while stuck, and the notice tells the operator the agent is 'temporarily unavailable'.",
    "failure_scenario": "At daemon start, or on _flush's TmuxError path which calls start_agent(), tmux has-session briefly fails with a non-'gone' error, and session_absent raises TmuxError. start_agent sets stuck and posts 'temporarily unavailable' under a fresh agent-stuck:{seq} key (a new notice every time). check_agent then returns at `if self.stuck is not None` on every poll, so the healthy pane is never adopted again. The only ways out are a daemon restart or !new, and !new discards the healthy conversation. The disposition says the probe is 'retryable on restart', which is true only for a process restart."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 165,
    "summary": "Adoption does not require startup_acknowledged, so a pane whose SessionStart was missed during a daemon restart is adopted and then always held after ready_timeout.",
    "failure_scenario": "admind restarts after new_session but before the fresh pane's SessionStart hook connects, or the hook fails while the daemon is down. On restart, ensure_running adopts the pane (launch_issued is already cleared), but no SessionStart will ever come again. 120 s later check_agent holds with 'launch-unacknowledged'. This is conservative, but routine restart timing forces a !new with no real fault."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3809,
    "summary": "While stuck, every _flush loops over the whole held list and calls reply() for each message (finding 5, still OPEN). Held inputs are never settled, so the work grows with the backlog.",
    "failure_scenario": "During a long HOLD the operator sends N messages. Each hook idle edge or new inbound message runs reply_target, chunking, redaction and enqueue N times. The outbox de-duplicates the keys, but the store work is still O(N) per flush, under dispatch_lock."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 2690,
    "summary": "The login-change notice condition `session_id is not None and not login_matches()` is also true for a pre-AU-11 store where agent_account is missing. In that case the notice states a login change that never happened.",
    "failure_scenario": "A legacy store has agent_session but no agent_account. Any start_agent exception (an AgentStuck hold, or a transient TmuxError) posts 'The admin agent login changed. Replies from the old login are blocked', even though the login is unknown, not changed. classify() also marks every hook from that pane stale."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 199,
    "summary": "The MAX_LAUNCHES_WITHOUT_START budget is dead code. _request_startup(fresh=False) only runs when nothing is recorded (so launches is 0), and fresh=True sets launches to 0, so 'launch-budget-exhausted' can never fire. No production caller passes relaunch=True either.",
    "failure_scenario": "Readers and docs (the module docstring says !new 'resets the outer three-launch ceiling') suggest a crash-loop limit that does not exist. Repeated !new commands each launch without limit, and the launches_without_start bookkeeping is write-only. Either remove the budget, the relaunch parameter and launches_without_start, or make them reachable."
  },
  {
    "file": "src/heterodyne/admind/agent.py",
    "line": 143,
    "summary": "Redundant and write-only launch state (finding 9, still OPEN). launch_new_intent and launch_attempt_intent are never cleared, launch_kind and launch_resume_eligibility are constants, and launch_session duplicates agent_session.",
    "failure_scenario": "After any successful !new, launch_new_intent stays set forever, so the `pending == 'new' and intent` branch can't tell a current !new from a stale one. Both branches refuse anyway, so the check only changes the hold reason string. 13 keys must stay consistent across `recorded`, startup_acknowledged and started()."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 553,
    "summary": "is_ready() and mark_ready() call startup_acknowledged() on every call, about 8 SQLite point reads each, on hot paths (finding 10, still OPEN).",
    "failure_scenario": "Every _flush (each hook event, inbound message and idle edge) and each 5 s check_agent poll repeats the reads, although ready is only ever set after the same check passed. Clearing ready inside hold_launch would give the same guarantee."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3880,
    "summary": "paste_uncertain's docstring still says a SessionStart releases the queue, but a SessionStart while busy is set now writes a persistent HOLD.",
    "failure_scenario": "After a paste-uncertain, busy stays set by design. A maintainer who trusts the docstring would expect a later startup SessionStart to free dispatch, but it actually triggers launch_hold('startup-during-turn') and needs !new. The check_agent comment about 'the dispatcher's own relaunch' is stale in the same way."
  }
]
```
