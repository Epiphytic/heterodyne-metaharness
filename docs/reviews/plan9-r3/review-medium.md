Public presentation: one absolute local checkout path replaced with ${WORKTREE} for repository portability. Original observed raw SHA256 is recorded in provenance; findings are unchanged.

**Verdict: good enough as an offline-core PoC.** I found no critical flaw that blocks the claims in scope. The R2 repairs mostly hold up: the fresh fence `attempt_id`, abort checking everything before it writes, replaying the full event chain before commit and release, the persisted release program, and the stricter archive tail (`all(...) and len == 1` really does change behaviour, because the old `<= 1` let an empty tail pass).

Three things are worth fixing before this goes further:
- **Duplicate spool input is silently dropped.** If the same bytes arrive again after their ID was retired, `spool()` reports success but the item never counts as active.
- **Commit can still get stuck.** It saves the intent before the final proof runs, which is the same problem R2 finding 2 fixed for abort.
- **Some I/O errors are still not a typed `Hold`.** Errors from `_lock`/`_atomic` come out as raw `OSError`.

I didn't verify any of these candidates (this review level skips verification), and I ran nothing: no tests, no probes. I didn't re-raise F9 (repeated proofs/history/lock latency), since it's already open and unwaived. No CLAUDE.md rule applies to this Python diff. Paths below are relative to `${WORKTREE}`.

10 findings, most severe first:

1. `src/heterodyne/acceptance/ledger.py:261`: Spool files are named only by content digest, so a later arrival with the same bytes as an already-retired spool ID is deduplicated onto the retired file and silently left out of the active set.

   Failure scenario: Source re-delivers an identical envelope after release() wrote spool-retired/<digest>: spool() sees path.exists(), _atomic returns idempotently, spool() returns the digest as if it were kept, but _spool_ids() filters it as retired. The next commit/release proof snapshot never sees this late arrival, which breaks the 'late arrivals remain' claim in R2 finding 5.

2. `src/heterodyne/acceptance/ledger.py:543`: commit() saves intent-<fence>.json before the terminal_fence proof (line 546) runs, so a refused proof leaves an immutable intent that blocks any corrected commit; this is the same problem R2 finding 2 fixed only for abort().

   Failure scenario: The proof adapter refuses the commit bundle (e.g. the assessment needs correcting, which changes intent.evaluation). A retry with the corrected intent hits immutable_file_conflict on the intent path forever, and abort() then has to adopt the stale retained intent. The only way out is aborting the attempt.

3. `src/heterodyne/acceptance/ledger.py:657`: abort() writes the intent object and intent file before writing the abort file, so a second abort() on an already-aborted fence persists a new intent and only then fails with immutable_file_conflict.

   Failure scenario: abort('x') with no intent succeeds; then abort('x', intent) passes validation and proof, writes intent-<fence>.json plus the object, then fails on abort-<fence>.json. An intent that was never part of the abort is now retained for that fence, which contradicts 'validate before persisting'.

4. `src/heterodyne/acceptance/evaluator.py:518`: The archive tail now requires exactly one qualification event after the Stop, but release() appends reconciled outside-segment events right after the qualification in the same ledger chain.

   Failure scenario: Commit seals the segment (stop + qualification), then release() appends reconciled events. If the archive is evaluated over the ledger's actual post-release chain (or any snapshot taken after release), the tail has more than one event and archive returns not_qualified/archive_terminal_tail for a legitimately sealed segment. Nothing slices the chain at the qualification.

5. `src/heterodyne/acceptance/ledger.py:178`: Typed IOHold is incomplete: OSError from _lock's os.open/flock and from _atomic's mkdir/open/write/fsync/rename/_sync still escapes as a raw OSError instead of Hold.

   Failure scenario: A missing or symlinked spool.lock/publisher.lock, ENOSPC, or EACCES while preparing a write raises FileNotFoundError/OSError. Callers that expect only Hold/InvalidEvidence get no fixed diagnostic code, so the 'missing/unavailable originals produce typed diagnostic Hold' claim (R2 finding 7) only covers reads.

6. `src/heterodyne/acceptance/ledger.py:34`: Fence gains a required attempt_id but keeps the schema literal 'acceptance-fence.v1', and __init__ now requires a spool-retired directory, so ledgers written before this change cannot be opened or recovered.

   Failure scenario: An existing ledger with an in-flight fence.json (no attempt_id) fails to decode in _fence()/recover(), and any pre-existing ledger without spool-retired/ fails Ledger() with ledger_directory_unavailable. There is no version distinction or migration path, and the same schema string now describes two incompatible shapes.

7. `src/heterodyne/acceptance/ledger.py:404`: _validate_events checks monotonic_ns only between adjacent events with the same boot, so a regression across an interleaved boot change is not detected.

   Failure scenario: Events on boot A with monotonic 100, then boot B with 5, then boot A with 50: no adjacent pair shares a boot, so 'event_monotonic' passes even though boot A's clock went backwards. Tracking the last monotonic_ns per boot_id would catch it.

8. `src/heterodyne/acceptance/ledger.py:679`: The release-retry.json path (written at 728, read at 679, deleted in begin at 295) is effectively dead: the fence is unlinked only after the receipt and all retirement markers are durable, so the no-fence retry path can only re-run work that already finished.

   Failure scenario: Extra persistent state, plus the only delete operation in begin(), adds a non-idempotent branch and another file to audit for no recovery benefit. Any crash before the fence unlink already resumes through the active-fence + release-program path.

9. `src/heterodyne/acceptance/ledger.py:717`: On the first-run path, release() replays the full chain (rebuilding inventory and verifying every event proof) twice, at line 695 and again at 717 over the same bytes, and compares the program against requested twice (686 and 713).

   Failure scenario: Each release does two full O(chain x proof) replays plus extra inventory rebuilds and hashes under both locks, doubling lock hold time on the path that blocks spooling. Skip the second replay when proven is True, and keep a single program comparison.

10. `src/heterodyne/acceptance/ledger.py:354`: _spool_ids(), which runs on every spool() call, reads and decodes every retirement marker and does linear tuple membership against the receipt's spool_ids, so spool cost grows with the total history of retired IDs (up to MAX_OBJECTS).

    Failure scenario: After many release cycles, with tens of thousands of retired markers, each incoming spool() under the spool lock does tens of thousands of file reads plus O(n*m) membership checks, stalling ingestion. Caching the receipt spool_ids as a set, or keeping an active index, would avoid it.
