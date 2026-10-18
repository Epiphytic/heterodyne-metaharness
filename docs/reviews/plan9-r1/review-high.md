10 findings, most severe first:

1. `src/heterodyne/acceptance/ledger.py:404`: Ledger.commit publishes a sealed segment and a qualification event based on the caller's Assessment (`result == "qualified_pending_archive"`, `unresolved == ()`). It never runs Evaluator.interval over the fenced event prefix and never checks that the Assessment's coverage is the seven passing segment rows.

   Failure scenario: A segment has a 700 s checkpoint gap, or a manual_intervention event with segment_id=null after the start. The caller builds an Assessment with result=qualified_pending_archive, coverage=() and any `evaluation` artifact, with elapsed >= 259200000000000. The only checks are endpoint duration, artifact-graph equality and the terminal_fence (source-completeness) adapter, so commit appends segment_stop and qualification. The ledger records a qualified segment that Evaluator.interval would reject with observation_gap or manual_intervention.

2. `src/heterodyne/acceptance/evaluator.py:305`: For isolated drills, `_projection` only checks that each projection_id exists in both the release identity and the drill identity. It never checks that the projected Components (kind and content_digest) are equal, although the design says a fresh isolated identity may differ only outside the fixed projection.

   Failure scenario: An isolated drill (e.g. dolt_outage) runs against component `wsd` with content_digest X, while Entry.release.identity has `wsd` with content_digest Y, and projection_ids=("wsd",). Both `_projection` calls pass because only IDs are compared, so a drill run on different code counts toward the 17 subcases. The design's required test for isolated-drill projection mismatches is also missing.

3. `src/heterodyne/acceptance/ledger.py:235`: begin() does not reject a segment_id that already has a committed terminal-<segment>.json. Running begin again for an already-sealed segment after release() leaves a fence that nothing can clear.

   Failure scenario: Segment S commits and is released. A collector retry calls begin("S"). Then: commit fails with immutable_file_conflict on terminal-<S>; abort returns committed_cannot_abort; recover returns manifest_fence (the stored manifest has the old fence hash); release returns fence_not_terminal; append returns terminal_fenced. The ledger stays blocked until someone deletes fence.json by hand.

4. `src/heterodyne/acceptance/evaluator.py:487`: archive() accepts the Segment's self-reported duration_ns and status=sealed without checking end/end_event_sha256 (both nullable in the model). It never replays events or checkpoints, and never checks that the pre-archive Assessment has the seven passing segment coverage rows.

   Failure scenario: A Segment with status=sealed, end=null, end_event_sha256=null, duration_ns="259200000000000" and a before-Assessment with coverage=() pass every structural check in archive(). With a terminal_fence adapter that only verifies source completeness, the result is release_complete for an interval whose duration was never derived from checkpoints. The design requires terminal Segments to have non-null end fields pointing to their unique Stop.

5. `src/heterodyne/acceptance/ledger.py:484`: abort() never reads the persisted intent-<fence>.json, so the abort record can name no intent, or a different intent, than the one actually prepared.

   Failure scenario: commit() writes intent-<fence>.json (intent T1), then the terminal proof holds. The operator calls abort(reason) with intent=None, or with some other SealIntent T2. The abort record stores intent_sha256=None or digest(T2), and release() unfences. The design requires recovery to abort the exact prepared intent, but the retained history now misstates which prepared transaction was aborted.

6. `src/heterodyne/acceptance/ledger.py:520`: release() (and commit() at line 431) lists the spool directory under the publisher lock only, not spool.lock. Spooling continues concurrently, so the drain proof covers a stale listing before the fence is unlinked, and nothing records which spool envelopes were reconciled.

   Failure scenario: Between release() taking the spool listing and `fence.json.unlink()`, a source spools an adverse manual_intervention envelope. The proof approved the earlier listing and the fence is removed. That envelope is never reconciled into the hash chain, and later appends proceed, so adverse evidence from the fenced window is silently left out of the published chain.

7. `src/heterodyne/acceptance/codec.py:102`: native_utc uses datetime.fromisoformat, which (Python 3.11+) silently truncates fractional seconds beyond 6 digits and accepts lenient forms (space separator, basic/compact and week dates). As a result, approved_at is not checked exactly against approved_at_utc.

   Failure scenario: A native approved_at of "2026-10-16T21:10:14.123456999Z" (nanosecond precision) and a different native time "2026-10-16T21:10:14.123456001Z" both map to "2026-10-16T21:10:14.123456Z". The authority_time check passes for a timestamp that does not match exactly, defeating the design's 'native and Marmot raw timestamp precision' requirement.

8. `src/heterodyne/acceptance/ledger.py:199`: Every _append re-reads, fstat-checks and decodes the entire event chain through _events(). commit() then calls _events() and _inventory() again several times (in _fence, the starts scan, _proof, _complete and each per-event _append), and _object counts objects with a full iterdir on every write.

   Failure scenario: A 72-hour segment at 300 s has more than 864 heartbeats plus other events. Each append costs O(n) reads and decodes, so publishing the chain is O(n^2). A single commit rebuilds the inventory (reading up to 128 MiB) three or more times and lists up to 100k objects per _object call. This lengthens the 'short' publisher-lock hold the design depends on. Caching the chain head and length, and building the inventory once per commit, would avoid it.

9. `src/heterodyne/acceptance/ledger.py:224`: Failed _atomic writes deliberately leave .prepare-* temp files behind. In spool/ and objects/ these count toward MAX_SPOOL and MAX_OBJECTS, and they appear in the sorted spool listing passed to the terminal_fence proof.

   Failure scenario: Repeated crashes or ENOSPC during spool() leave .prepare-<uuid> files. They use up the 1,024-envelope limit, so a real envelope gets spool_overflow. They also appear in the commit and release spool listings as non-envelope names, which the drain proof has to treat as unknown envelopes.

10. `src/heterodyne/acceptance/ledger.py:252`: heartbeat(), abort() and release() read fence.json without checking that it exists, so calling them with no active fence raises FileNotFoundError instead of the typed Hold the rest of the API uses.

    Failure scenario: A collector calls heartbeat() or release() after a crash where begin() never ran, or after a fence was released. It gets an uncaught FileNotFoundError rather than Hold, so callers that handle only Hold or InvalidEvidence (as the Evaluator does) crash instead of recording a diagnosable HOLD.
