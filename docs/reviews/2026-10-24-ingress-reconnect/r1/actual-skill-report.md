Skill "code-review" completed (forked execution).

Result:
**Verdict: REVISE.** This review does not clear the reconnect source. Two high findings should block it:
- A replacement reader that fails quickly still leaves the cutover holding all three critical locks for the full 125 s.
- Any unexpected exception in the reader supervisor permanently marks the daemon as shutting down.

The intake fence also drops events that were already decoded. That is the same lost-answer failure this feature was built to diagnose.

**What I covered:**
- The complete 13-path patch, read line by line. I read `ingress_reconnect.py`, `ingress.py` and the touched `daemon.py`, `ctl.py`, `control.py` and `cli.py` functions in full from the frozen source.
- `approved-design.md`, `context.md` and `composition.md`. I did not open `receipt.json`, `source-pins.json` or `freeze.json`.
- The reconnect tests, the race tests and the control tests.
- Callers of every lock (all of them go through `_ingress_lock`), `confirm_observing`, `subscribe` and `group_info`.

**What I did not cover:**
- I did not run tests or check the SHA256 pins.
- In `test_admind_ingress_status.py` and `test_admind_ingress_observation.py` I read only the diff hunks I reached, and I did not re-review the inherited status gate.
- I could not check the design's mutant-kill requirement or real wn-agent transport behaviour.
- No verify pass ran (this is the recall tier), so the findings are not independently confirmed.
- The CLAUDE.md files had no rules that apply to this Python code.

**Test gaps:**
- The failed-observation test only blocks `count_lock`. No test covers a replacement reader that fails fast.
- No test covers a supervisor exception or a natural teardown timeout.
- No test covers events decoded between admission and the reader being cancelled.
- `test_accepted_audit_failure_keeps_gates_closed_and_releases_owned_locks` accepts the false `failed-teardown` state described in finding 4.

```json
[
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 269,
    "summary": "_cutover waits only on `ready` (with the timeout) after starting the replacement reader and never watches that reader, so a reader that fails quickly leaves the cutover holding work/transition/custody locks and `intent` until the full 125 s deadline, while run() stays parked on `changed` and cannot retry.",
    "failure_scenario": "wn-agent is restarting during an explicit reconnect, so `_open` raises ControlError at once (or the ACK has a protocol error, or check_group returns False without a latch, so confirm_observing raises 'unverified'). The reader task finishes in milliseconds, but nothing sets `ready`. For about 125 s every reconnect is busy, rearm and operators add/remove are refused, gateway-custody and recovery control are refused, autobump and maintenance block on work_lock, and no automatic resubscribe happens. Only then is failed-observation reported and backoff resumes. The design says to return ownership to the automatic supervisor promptly. The only failed-observation test blocks count_lock, so this path is untested."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 354,
    "summary": "run()'s finally sets `d.shutting_down = True` on every exit, including an exception that is not a cancellation. The `supervised` wrapper restarts the loop on exceptions, so the restarted run() exits at once (`while not shutting_down`) and the reader is never started again.",
    "failure_scenario": "Any unexpected exception in the supervisor loop (for example a non-OSError from `Subscription.close` → `closing.result()`, an error from `_backoff`/`d._sleep`, or the 'subscription custody missing' RuntimeError) runs the finally. It cancels the cutover and stops the reader, and sets shutting_down. `supervised` then restarts a loop that returns immediately, every 30 s, forever. The daemon stays up but deaf: confirm_observing returns early, admit replies busy, and paste/dispatch paths treat it as shutting down. The old inbound_loop caught everything and kept retrying."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3376,
    "summary": "_enqueue_ingress discards events that were already decoded and authenticated whenever `intent` or `paused` is set, instead of keeping them in order. The workers are already held back by worker_ready/_ingress_lock, so queueing these events would be safe.",
    "failure_scenario": "admit() sets intent=True and returns 'accepted'. Before the _cutover task runs and cancels the reader, the event loop runs the old reader, which decodes an operator's buffered `!approve` reply. on_event → _enqueue_ingress counts dropped_intake and returns. The reader is then replaced and upstream replay is unqualified, so the answer is lost with no claim, no drop audit and no reply. That is the 'delivered ask, no answer' failure this feature exists to diagnose. A latched daemon after a failed reconnect also stays paused, so operator messages lose their usual drop audit and are only counted."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 261,
    "summary": "The non-quiet `d.audit.write(... 'reconnect-accepted')` runs before any teardown. If it raises, the generic `except Exception` marks teardown_unknown=True and records 'failed-teardown', although the original reader was never cancelled or closed.",
    "failure_scenario": "The audit log write fails briefly (ENOSPC/EIO). sub_gen has already been invalidated and paused=True, but the original reader is still running. It returns at its next event (that event is silently lost). run() then waits on `changed` forever because teardown_unknown is set. Ingress stays halted until a person issues another explicit reconnect, and status falsely reports a teardown failure. The fix is to use audit_quietly, or to stop treating errors that are not teardown errors as teardown failures."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 324,
    "summary": "On a natural stream end, if the owned transport close takes longer than 2 s, run() sets teardown_unknown and paused and then waits for an explicit operator reconnect forever. It writes no audit record and sends no notice, which turns a self-healing automatic loop into a silent permanent halt.",
    "failure_scenario": "wn-agent hangs while closing a subscription connection, so wait_closed does not finish within 2 s. Before this patch the daemon kept backing off and resubscribing. Now automatic reconnect stops, queued normal and custody events are held indefinitely, and the only evidence is the reader phase in `ingress status`. Nobody is told an explicit reconnect is needed."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 329,
    "summary": "The rewrite removed the old `audit.write('subscribe', action='reconnect', code=...)` record for each ended or failed subscription cycle, and nothing replaces it. Only an in-memory `reconnects` counter remains, while last_error_category is reported as unavailable.",
    "failure_scenario": "The subscription keeps failing with ControlError socket_io / unverified / frame_too_large. Before, each cycle left an audit line with the error code. Now nothing is written, so a post-incident audit cannot show when or why the reader dropped. The design says reader lifecycle events stay audited."
  },
  {
    "file": "src/heterodyne/admind/ingress.py",
    "line": 337,
    "summary": "`audit_requests=False` switches off request and failure auditing for the whole ingress socket, which includes reconnect mutation requests. The design allows suppression only for status polls.",
    "failure_scenario": "A same-UID process sends repeated busy/stale/request-conflict reconnects, or admit() raises. JsonSocketServer writes no request, handler-failed or failed audit for any of them. Only accepted cutovers leave a 'reconnect-accepted' line, so refused or broken mutation attempts on a privileged control socket leave no trace."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3351,
    "summary": "read_subscription returns on a generation mismatch after an event was already decoded, without counting it in any counter (not decoded_any, not dropped_intake). That event disappears without trace.",
    "failure_scenario": "After a teardown failure or the audit-failure path, the old reader is still alive but invalidated. It reads an operator message, sees `generation != sub_gen` and returns. The message is gone, and status shows no decoded or dropped evidence of it, which undermines the honesty of the status schema."
  },
  {
    "file": "src/heterodyne/admind/cli.py",
    "line": 441,
    "summary": "The client-side check() rejects malformed reconnect arguments by raising CtlInvalidReply (a CtlUnavailable subclass). The CLI reports this as 'ingress status unavailable' with exit code 69, so a user input error looks like the daemon being down.",
    "failure_scenario": "`admind ingress reconnect --instance X --generation 5 --request-id <UPPERCASE-UUID>` (or a negative generation) prints 'admind: ingress status unavailable' and exits 69 without contacting the daemon. The operator concludes the daemon is down instead of fixing the argument."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 64,
    "summary": "RetainedQueue.put() is dead code (only put_nowait is ever called), yet task_done() sets and replaces `Capacity.changed` with a new asyncio.Event on every completed event just to wake it. A related redundancy: a natural reconnect cycle calls invalidate() three times (lines 315, 331, and 246 via _start_reader).",
    "failure_scenario": "Every completed normal or custody event allocates a throwaway Event that nothing waits on. The repeated invalidations advance sub_gen by 3 per natural cycle, which leaves an operator-copied generation stale sooner and adds unexplained state for maintainers. The simpler form is to drop put()/changed and invalidate once per cycle."
  }
]
```