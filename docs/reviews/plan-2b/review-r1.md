Reviewed the plan against ADR `66b3aec` §8 and the existing code on `plan-2b-admind`. References below use line numbers in the [plan](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md).

1. **[BLOCKING] Task 5 — the membership hold does not serialize with side effects already running.**  
   Lines 871–872 and 888–925 introduce `guard_lock` and `changing`, but dispatch holds a different lock while awaiting `agent.send` ([daemon.py:620](src/heterodyne/admind/daemon.py:620)), and outbound posting awaits `send_final` without that lock ([daemon.py:1116](src/heterodyne/admind/daemon.py:1116)). Either operation can complete during the membership change. The flag prevents new operations; it does not drain existing ones. Also, the preflight `group_info` awaits before `changing` is set, and a latch arriving during that await is not rechecked before changing membership. Establish the hold before preflight, recheck authorization after awaits, and serialize or drain dispatch, commands and sends before journaling the transition.

2. **[BLOCKING] Tasks 5–6 — membership events are not guaranteed to remain processed throughout a transition.**  
   `change_membership` checks neither `observing` nor subscription generation (lines 888–923). The ctl server starts before the inbound task group (lines 1200–1205), permitting a transition before observation begins. During reconnection, `subscribe` awaits `confirm_observing`, which awaits `check_group` before reading events ([control.py:265](src/heterodyne/marmot/control.py:265)). Task 5 makes that check wait on the transition’s `guard_lock`, so membership events can remain buffered until after settlement. Require a verified live subscription and invalidate settlement when observation is lost or replaced; keep event consumption independent of the locked count check.

3. **[BLOCKING] Task 5 — exceptions can release the hold with an unresolved pending change.**  
   Lines 912–926 clear `changing` unconditionally but do not latch on unexpected exceptions. For example, an audit failure immediately after persisting `membership_pending` leaves the pending record present while dispatch and posting resume. Startup latching does not protect the still-running daemon. Likewise, `load_operators()` can fail after commitment at line 952, leaving stale authorization state and an incomplete result. Add exception/cancellation settlement that retains the pending record and fails closed until host recovery.

4. **[BLOCKING] Tasks 2, 3 and 6 — the audit is not comprehensively redacted, and ctl permits direct secret leakage.**  
   Task 2 changes selected daemon fields but leaves [Audit.write](src/heterodyne/admind/audit.py:22) serializing fields unchanged. Existing `message_id`/`reply_to` fields remain raw 64-hex values, and Task 3 writes the policy operator name directly. More seriously, Task 6 logs `req.name` unchanged at line 1155: an allowed name can itself be an npub or token, and `rearm` bypasses the name check entirely, allowing arbitrary text there. Redact all audit string fields centrally, reject unexpected rearm arguments, and test decoded audit records for leakage.

5. **[BLOCKING] Task 2 — `redact` is not idempotent.**  
   Lines 320–326 redact hex before escaping controls. With input `"\x1b" + "f" * 63`, the escape introduces two additional hex digits, producing a 65-digit run on the first pass. The second pass changes it to `\x<redacted hex key>`. I reproduced this using the plan’s exact function and current scanner patterns. This violates both B1 and the proposed properties, and causes audit and repeatedly redacted chat text to differ. Make control escaping and hex recognition compatible and add this explicit regression case.

6. **[BLOCKING] Tasks 2 and 4 — upgrading can still send unredacted queued replies.**  
   Redaction occurs at enqueue time, while `outbox_pass` sends stored text directly ([daemon.py:1116](src/heterodyne/admind/daemon.py:1116)). A plan-2 database can contain pending unredacted replies. Neither task migrates those rows nor redacts them at delivery. Include an upgrade path that handles secrets spanning old chunk boundaries, and test a pre-existing pending outbox containing sensitive text.

7. **[BLOCKING] Tasks 8–9 — transcript end offsets can belong to a different turn.**  
   Task 8 records the current transcript size after reply extraction and outside the turn lock (lines 1744 and 1771–1775), then stores that offset even for a stale reply. Existing `on_stop` deliberately permits an old Stop’s own text while newer turn state remains untouched ([daemon.py:1039](src/heterodyne/admind/daemon.py:1039)). Consequently, `!details full` can pair the old reply with the newer turn’s tool calls. Persist reliable turn boundaries or mark transcript details unavailable whenever the boundary cannot be tied to that Stop. Test stale Stops and turn changes during extraction.

8. **[BLOCKING] Task 9 — `!details full` silently omits tool calls and results.**  
   Lines 1949–1956 read only the final `MAX_TRANSCRIPT` bytes; the existing limit is 8 MiB ([hook.py:47](src/heterodyne/admind/hook.py:47)). A larger turn loses its earlier calls, and a single oversized JSONL result can disappear entirely when the partial line is discarded. The output still claims to contain that turn’s tool calls. Use bounded incremental reads over the complete turn interval, with explicit handling of oversized records. Add tests exceeding the limit and containing a record larger than one read window.

9. **[BLOCKING] Task 8 — failure recovery covers only part of the reply pipeline.**  
   Lines 1791–1805 catch summary-processing failures, but extraction, initial turn recording and verbatim posting are outside that backstop path. A terminal outbox failure also leaves a summary marked `summarized` without routing its reply to the backstop. Additionally, if `to_backstop` raises once, `summary_loop` has already removed the job from its queue; supervision restarts the loop, but persisted `summarizing` rows are requeued only at process startup (line 1742). Define durable retry/backstop handling for each stage and test transient failures without restarting the daemon.

10. **[BLOCKING] B10 / Task 8 — chunked backstop batches contradict the ADR.**  
    B10 and lines 1824–1828 send a batch as multiple messages when it exceeds `chunk_chars`. ADR r13 §8 explicitly requires the batch to be **one unthreaded message**. The plan’s preference for accuracy does not authorize changing that requirement. Follow the ADR or obtain an ADR revision before implementation.

11. **[BLOCKING] Task 8 — “latest” details can select an unsent or failed message.**  
    `details_target(None)` at lines 1697–1699 orders all summary/batch outbox records by enqueue sequence without requiring successful delivery. A pending or permanently failed summary can therefore replace the latest message actually shown to the operator. Define latest using delivered records and test pending and failed rows alongside a previously delivered summary.

12. **[BLOCKING] Tasks 5, 8 and 9 — the tests omit essential concurrency and restart guarantees.**  
    Task 5’s flag assertions and delayed add do not test an already-running paste/send, subscription replacement, or exceptions after journaling. Task 8 tests restart of a `summarizing` row, but not an open batch, transactional rollback, or delivery recovery. Task 9 does not test `!details` lookup after reopening the database. Add deterministic barrier tests for these races and restart tests for both summary and batch records. The listed fake-based tests respect the no-network/no-real-service constraints; Task 1’s live research must remain separate from the automated gate.

13. **[NON-BLOCKING] Tasks 3 and 6 — accepted operator names and ctl names have different contracts.**  
    Task 3 accepts policy names unchanged, while Task 6 restricts them to a 64-character ASCII pattern (line 1100). Existing policy validation accepts arbitrary string names ([policy.py:61](src/heterodyne/config/policy.py:61)). An operator accepted by `init` can therefore be impossible to remove through ctl. Align validation or permit exact policy-name lookup with redacted output.

14. **[NON-BLOCKING] Task 7 — summary bounds and isolation need stronger validation.**  
    The runner accepts 16 lines plus its footer (lines 1407 and 1459), despite the approximately eight-line requirement. Tests verify argv construction, but do not capture the complete redacted stdin or exercise summaries containing required questions and errors. Also, `communicate()` buffers unlimited output before enforcing the character limit. Tighten the accepted size, bound collection, and extend the fake-process tests; verify the actual no-tools/no-hooks/no-MCP behavior during Task 1.

15. **[NON-BLOCKING] Task 9 — timed-out transcript readers can accumulate.**  
    Each `tool_calls` invocation starts a new `to_thread` operation (lines 2048–2051); timeout does not stop its underlying thread. Existing extraction deliberately allows only one outstanding reader ([daemon.py:153](src/heterodyne/admind/daemon.py:153)). Preserve a bounded reader-concurrency invariant for both new transcript operations.

**Verdict: REVISE**