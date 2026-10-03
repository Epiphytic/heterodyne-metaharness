Reviewed `7eae5f9` against ADR `66b3aec` §8 and the prior reviews. Round-4’s three reported cases have fixes and regression tests: decoded-string redaction, mixed tool-result records, and premature EOF/unterminated records. Two related completeness problems remain.

1. **[BLOCKING] Task 9 — structured redaction silently deletes tool data.**  
   [_redacted, plan line 3258](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:3258) builds a dictionary using redacted keys. Different secrets or hex keys become identical markers, so later entries overwrite earlier ones. For example, `{"a"*64: "first result", "b"*64: "second result"}` becomes only `{"<redacted hex key>": "second result"}`. I reproduced this in memory. This is a round-4-fix regression and violates unabridged `!details full`. Preserve every entry when redacted keys collide, and test collisions in inputs and results.

2. **[BLOCKING] Tasks 8–9 — malformed complete records still produce successful partial extraction.**  
   [turn_records, lines 2719–2723](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2719) silently skips invalid JSON and non-object records. A valid assistant record followed by a newline-terminated corrupt assistant record returns the first answer successfully; I reproduced this with the plan’s functions. Corruption can therefore omit an error, question or tool result without invoking `EXTRACT_FAILED`. Round-4’s EOF fix does not cover this. Propagate malformed-record failures through both readers and add regression tests.

3. **[BLOCKING] Task 8 — batch ordering records turn creation, not backstop arrival.**  
   [batch_turns, line 2582](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2582) sorts by `turn_id`; [queue_backstop, line 2925](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2925) persists no joining sequence. An older long reply can still be summarizing when a newer verbatim reply fails delivery and opens a batch. If the older summarizer then fails, the batch reverses their affected-reply arrival order. Both rendering and `!details` inherit that reversal. Persist a batch-entry sequence transactionally and test overlapping summarizer and delivery failures.

4. **[NON-BLOCKING] Task 8 — batch membership can extend beyond its window.**  
   [open_batch, line 2569](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:2569) accepts any open batch without checking its deadline. Until the one-second batch poll closes it, a reply arriving after 60 seconds can join the expired batch. Enforce the deadline when joining, or explicitly document this timing tolerance. Add a controlled-clock boundary test.

5. **[NON-BLOCKING] Task 4 — lane-2 retry backoff delays urgent lane-1 delivery.**  
   [outbox_pass, lines 1072–1080](docs/superpowers/plans/2026-10-02-heterodyne-plan-2b-admind-r13.md:1072) releases `send_lock` but sleeps in the sole outbox loop. A command reply or alert arriving during a details retry waits up to 60 seconds; `wake.set()` cannot interrupt that sleep. Row selection preserves lane priority, but responsiveness falls short of the stated urgency goal. Make backoff interruptible by lane-1 arrivals or schedule retry eligibility per row, with a barrier test.

6. **[NON-BLOCKING] Tasks 1, 3, 7 and 10 — additional coverage decisions need explicit treatment.**  
   Task 1 stops when membership capability contradicts the ADR, but gives no implementation task for the ADR’s supported alternative: operator changes through a new `admind init` group only. Also, existing [ADMIN_ADAPTERS](src/heterodyne/admind/settings.py:26) remains Claude-only, and Task 7 preserves that restriction through `_profile`; §8 says any admin adapter can be configured. Record these as explicit deferrals or provide tasks. Finally, the question/error test at plan line 2013 checks prompt wording, not preservation in accepted summaries; the real spike provides limited behavioral evidence, not an automated guarantee.

7. **[NON-BLOCKING] Decisions B1–B22 / Task 10 — ADR deviation inventory is incomplete.**  
   The operator should decide on all of these differences, including ones already acknowledged in the plan:

   | Plan location | Difference from §8 |
   |---|---|
   | B1; Task 2 | Newline and tab remain literal rather than all control characters being escaped. Hex runs longer than 64 digits are also masked; unstable redaction replaces the entire text after ten passes. |
   | B2; Task 3 | Additional policy restrictions: names must be 1–128 characters without controls, duplicate keys are rejected, and at least one eligible operator is required. |
   | B6, B22; Tasks 5–6 | Rearm requires a running daemon reading events and can refuse on policy, reconciliation or concurrent-change conditions; the ADR describes trusting the current count and clearing pending state. |
   | Task 5, line 1459 | Removing the last operator is refused; §8 states no such restriction. |
   | B21; Tasks 3, 5 | Authorization requires policy eligibility **and** durable confirmed membership. A multi-operator plan-2 migration latches rather than simply authorizing all policy operators. |
   | B8; Task 7 | Summaries accept ten body lines, followed by a blank line and footer—up to twelve displayed lines—rather than approximately eight overall. Additional character/byte limits trigger backstop delivery. Profile `args` are ignored. |
   | B10; Tasks 7–8 | Batches exceeding the chosen transport limit have individual lines shortened, contrary to “sent whole” below the line threshold. Failed batches reopen under a fresh window. |
   | B13; Tasks 8–9 | Records over 64 MiB are omitted with a notice; image results become metadata and a digest. Structured results are reformatted as sorted JSON rather than preserved verbatim. |
   | B19–B20; Tasks 8–9 | Missing/unreliable spans, reader contention or timeout make full tool details unavailable. There is no automatic later completion. |
   | B9; Task 8 | Unrecoverable extraction batches a fixed notice instead of the affected reply. This is an explicit limitation of the “drops nothing that details cannot return” promise. |
   | B16; Task 2 | Audit identifiers use hashed references rather than replacement markers. |
   | B17; Task 4 | Upgrade re-keying can repeat previously delivered text; unverifiable continuations are replaced wholesale. |
   | Tasks 1, 3, 7 | Init-only capability fallback lacks an implementation path; admin and summarizer profiles remain Claude-only. |
   | Tasks 4, 8 | Retry latency, batch deadline tolerance and batch arrival-order defects described above are additional behavioral differences. |

The membership commit/abort rules, transition draining, subscription-generation checks, pending-record startup latch, durable reply recovery, and produced/consumed interfaces otherwise align with the existing machinery. The ctl socket intentionally trusts the service user, consistent with the ADR’s residual risk.

Planned automated tests respect the fake-service and no-network constraints; Task 1’s real-service research is separate. I ran isolated in-memory reproductions only, not an implementation test suite. No files were changed.

**Verdict: REVISE**