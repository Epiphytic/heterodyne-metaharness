**Verdict: APPROVE as a bounded PoC.** I found no critical flaws in `597a1d4..ec4ffb1` against approved design `e613fd9`.

I checked each R2 repair against the code:
- **Withdrawn untouched/refused settlements:** Store now turns a reopen into `cancelled`. This holds on the ordinary, restart, preflight, issue-fence and maintenance paths.
- **Outbox projection:** it includes `askr:` keys through indexed prefix ranges.
- **Initial post:** it adopts the whole legacy history. It creates a current card only through `prepare_approval`, which re-runs `approval_post_refusal`, so the latch, capacity and rate gates are not bypassed.
- **Legacy posts:** every legacy post kind on a source-owned bead is refused.
- **Withdrawal transaction:** cancellation and the transition record commit together; the external audit write is best-effort.
- **Layering:** the tombstone and schema now live in Store.

The framing limitation stays UNWAIVED and is documented as you described it. The findings below are not blocking. Ranked first is the one item worth tracking before a real client integration. This was read-only: no tests, probes or edits.

8 findings, most severe first:

1. `src/heterodyne/admind/operations.py:339`: withdraw() runs view() before it sets the tombstone, so overflowing the reply-projection bound (MAX_PARTS 4096 or MAX_REPLY) blocks withdrawal entirely, not just the report.

   Failure scenario: A long-lived open card on a bound bead builds up more than 4096 answer, attempt and outbox references (for example, many askr repeat-card parts). Every withdraw then returns refused/lineage-parts-capacity before the tombstone is written, and the open card stays answerable. The design says overflow holds, but here the safety fence depends on the size of the status projection.

2. `src/heterodyne/admind/daemon.py:1800`: The withdrawn branch of retry_maintenance calls read_and_settle (which asserts approve_bead is not None) before any approvals-unconfigured check.

   Failure scenario: With approve_bead removed (a rollback) and a withdrawn pending-maintenance attempt, the assert raises AssertionError on every cycle. It lands in the loop's generic except as maintenance-retry-failed, with no durable hold notice. The non-withdrawn path would post an 'approvals-unconfigured' hold instead.

3. `src/heterodyne/admind/daemon.py:1604`: The withdrawn branch of refresh still uses self.audit.write, not audit_quietly, after commit, which goes against R2 disposition 4's best-effort claim.

   Failure scenario: An audit OSError (disk full) after a withdrawn stale attempt commits as cancelled propagates into the worker's exception handling. That handler then attempts a deciding CAS, which fails, producing a spurious conflict and audit noise. The cancellation itself is not undone.

4. `src/heterodyne/admind/operations.py:344`: withdraw on an unknown key with a bead and no lineage permanently binds that bead to a tombstone, with no context check and no retention path.

   Failure scenario: A same-UID client withdraws key K with a typo'd or not-yet-posted bead B. B is then source-owned forever. Every legacy AskPost and every other operation key for B is refused, and no recovery exists in this version.

5. `src/heterodyne/admind/store.py:164`: The revision triggers fire only on asks INSERT/UPDATE, so new answers, attempts and outbox parts in the lineage do not advance the documented 'observational CAS generation'.

   Failure scenario: A second answer row or an askr repeat part is added without an asks update. status then returns the same revision with a different lineage, and a client that uses revision to detect change misses it.

6. `src/heterodyne/admind/daemon.py:1808`: On withdrawal, the maintenance path cancels an untouched readback through close_refused, which records settled='refused' although the evidence was an untouched native readback.

   Failure scenario: Attempt provenance for a withdrawn maintenance retry shows a preflight refusal instead of untouched native evidence. That misleads later audit or recovery reasoning about why no decision was issued.

7. `src/heterodyne/admind/operations.py:327`: post() collapses every prepare_approval refusal (latched, too many, too often, redaction, too long) into one 'post-gate' code.

   Failure scenario: A source client cannot tell a transient refusal (rate or latch) from a permanent one (redaction or card too long), so it either retries a permanent refusal forever or gives up on a transient one.

8. `src/heterodyne/admind/ctl.py:182`: Every ask-socket request is now JSON-decoded twice: an untyped envelope decode, then the typed decode.

   Failure scenario: This doubles the parse cost for every legacy ask request, up to the stream limit, before the length check. A cheaper option is to try the typed union decode first and only classify the envelope on failure.
