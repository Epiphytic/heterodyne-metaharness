Skill "code-review" completed (forked execution).

Result:
**Verdict: REVISE / NOT CLEARED.** I found one high finding. It means the R1 #5 disposition (natural teardown uncertainty is "bounded/audited advisory escalation", not a silent permanent halt) is not actually met. The other findings are medium or low.

I ran the review using the code-review recipe I was given, in this context, with no subagents and no verify pass. That was the recall tier, so the findings are unverified. I made no edits, ran no tests, used no network and posted no comments.

**Critical/high:** one finding (#1 below). Relative to R1:
- **Fixed in source:** R1 #1 (terminal-reader lock release), #2 (shutdown flag), #4, #6, #8 and #10. R1 #3 and #9 also look fixed.
- **Partly fixed:** R1 #5, because the advisory notice cannot be delivered (finding 1). R1 #7, because decode-level malformed requests are still not audited (finding 6).
- **New gap next to R1 #1:** a reader that stays alive but never observes still holds the locks for the full 125 s (finding 2).

**Coverage, and what I did not cover:**
- **Read in full (current source):** `ingress_reconnect.py`, `ingress.py` and `test_admind_reconnect_r1_repairs.py`, plus `test_admind_reconnect_independent_regressions.py`.
- **Read in part (relevant sections of current source):**
  - `daemon.py`: supervised, run/TaskGroup, the snapshot and reconnect handlers, `_ingress_lock`, custody/recovery, `check_group`, `read_subscription`, `confirm_observing`, `_enqueue_ingress`, `on_event`, `worker_loop`, `may_post`/`authorised`/`post`, `outbox_pass`, alerts, `audit_quietly`/`contained`.
  - `control.py`: `Subscription` and `subscribe`.
  - `ctl.py`: `JsonSocketServer` and `request`.
  - `cli.py`: `ingress_command`.
- **Patches and documents:** I read the brief, `context.md`, the disposition, the actual R1 report, and these parts of `repair.patch`: the docs, daemon and test hunks.
- **Not done:**
  - I had no Bash/git, so I did not produce a git diff. I did not read `implementation.patch` line by line; I reviewed the current files instead.
  - I did not open `approved-design.md`, the receipts, pins or freeze files, and did not check any SHA.
  - I did not fully read `test_control_reconnect.py`, `test_admind_reconnect_races.py`, `test_admind_ingress_status.py`, `test_admind_ingress_observation.py` or `test_admind_gateway_custody_control.py`.
  - I did not establish real wn-agent behaviour or run anything at runtime. The 321-pass result, Ruff, Pyright and the negative-control logs are author evidence I did not observe.
- **Conventions:** no rule in the applicable CLAUDE.md files covers this Python code.

**Test gaps:**
- The teardown notice test replaces `d.post` with a Mock, so nothing checks that the notice can actually be delivered.
- No test covers an explicit reconnect while latched with a failing group check.
- No test covers an in-flight cutover during an unexpected supervisor fault.
- No test covers malformed reconnect requests that fail at decode time.

```json
[
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 277,
    "summary": "The teardown-unknown 'escalation' notice cannot be delivered while the episode lasts: _unknown_teardown calls invalidate() (observing=False) and then queues the notice, but outbox_pass returns at once whenever may_post() is false, and may_post() requires self.observing. So the R1 #5 'silent permanent halt' remains in practice.",
    "failure_scenario": "wn-agent hangs while closing, so Subscription.close returns False within 2 s. run() sets teardown_unknown, writes one audit line, queues 'ingress-teardown:<inst>:<gen>' and then waits on `changed` forever. outbox_pass sees may_post()==False (observing is False and nothing can make it true until an explicit reconnect succeeds), so nothing is sent and no operator is told. The notice is only sent after someone has already recovered ingress, and then it falsely says 'Approval ingress is paused'. If the episode began in run()'s finally during shutdown, the stale notice is sent by the next daemon process and names the old instance. test_unknown_teardown_notice_once_and_exact_original_retained mocks d.post, so this is untested. Raising the alarm needs a channel that does not depend on observation (for example a host alert or status file), or the disposition/docs must stop claiming an escalation."
  },
  {
    "file": "src/heterodyne/admind/daemon.py",
    "line": 3376,
    "summary": "When latched and the group check fails, confirm_observing returns without raising and without setting group_ok, so observed() never sets ready. acknowledged() then waits on reconnect.released, which the cutover clears. The replacement reader is alive but never observing, so _wait_observing holds work/transition/custody locks and intent until the full 125 s deadline.",
    "failure_scenario": "The daemon is latched and group_info raises ControlError (wn-agent is degraded). The operator runs `admind ingress reconnect`. The reader ACKs, check_group returns False, latched skips the 'unverified' raise, and the reader blocks in acknowledged(). For about 125 s, rearm (the action that clears the latch), operator changes, custody control, recovery, autobump and maintenance are all refused or blocked. Then the cutover reports failed-observation. The R1 #1 fix only covers readers that end quickly, not readers that are alive but not observing."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 413,
    "summary": "On an unexpected (non-cancellation) exception, run()'s finally cancels any in-flight explicit cutover, so its record and audit say reason='shutdown' although the daemon is not shutting down. It also closes the reader and transport, which contradicts the new docs line 'Unexpected loop faults retain transport custody'.",
    "failure_scenario": "During an accepted cutover, the supervisor loop raises (for example the 'subscription custody missing' RuntimeError, or an error from d._sleep in _backoff). The finally cancels self.operation, and _cutover records failed/shutdown and audits 'shutdown'. supervised restarts run() and the daemon keeps running. The operator polling `ingress status` sees reconnect state=failed reason=shutdown, which is false, and the docs describe custody retention that the code does not perform."
  },
  {
    "file": "src/heterodyne/admind/ingress.py",
    "line": 355,
    "summary": "The R1 #7 audit fix covers only reconnect requests that parse. A reconnect request that fails msgspec decoding (wrong field type, unknown field, oversize) goes to self.refused without calling validate(), and audit_requests=False also suppresses JsonSocketServer's 'failed'/'reply-too-large' audits for mutation connections.",
    "failure_scenario": "A same-UID process repeatedly sends {\"schema\":\"admind.ingress.request.v1\",\"op\":\"reconnect\",\"generation\":\"5\"} or adds an extra field. Each attempt is refused as malformed with no audit line. A drain failure after an accepted cutover is also not audited, so some refused or broken attempts on the privileged control socket still leave no trace."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 169,
    "summary": "The invariant comment 'No suspension is possible acquiring an asyncio.Lock known free' is false on Python 3.12+. In the handoff window after release(), locked() is False while a woken waiter is still queued, so `await lock.acquire()` suspends admit() while it holds intent and possibly earlier locks.",
    "failure_scenario": "The worker releases work_lock and a queued _ingress_lock waiter has been woken but has not run yet. admit() sees every lock free, sets intent and suspends in acquire(). Correctness now depends only on every waiter going through _ingress_lock's re-check of intent and releasing. Any future direct `async with d.work_lock` user, or a waiter that holds the lock across awaits, would block the socket handler, and the client's 5 s timeout fires while admission is half done. The safety argument in the comment is wrong and fragile."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 229,
    "summary": "_last_audited_reader keeps a strong reference to the last finished reader Task, including its exception and traceback frames (subscribe/read_subscription locals such as the last decoded event and its payload), until another natural reader ends.",
    "failure_scenario": "A reader fails with a ControlError after decoding a large operator message. The done Task, its traceback and the frame locals (event text, StreamReader buffer) stay alive indefinitely while the next subscription is healthy. Memory is held and message plaintext stays reachable. Deduplicating with a non-retaining marker (an id or generation integer, or a flag cleared in _start_reader) would avoid this."
  },
  {
    "file": "src/heterodyne/admind/ingress.py",
    "line": 317,
    "summary": "mutation_audit duplicates Admind.audit_quietly (contextlib.suppress around audit.write), and the reconnect path now has two parallel best-effort audit helpers with the same semantics.",
    "failure_scenario": "Future changes to suppression or redaction policy must be made in both places. The server could take a quiet-audit callable (d.audit_quietly) as it already takes snapshot/reconnect callables, instead of reimplementing it locally."
  },
  {
    "file": "src/heterodyne/admind/ingress_reconnect.py",
    "line": 62,
    "summary": "RetainedQueue.put_nowait re-encodes every decoded event with msgspec.json.encode only to measure its size, although the reader already has the exact frame bytes in decode_event.",
    "failure_scenario": "Every inbound message or reaction (up to an 8 MiB budget) is serialized a second time on the daemon event loop in the hot intake path, which costs CPU and a temporary allocation per event. Passing the decoded line length (or a size computed once in read_subscription) into _enqueue_ingress would give a bounded size without the extra encode."
  }
]
```