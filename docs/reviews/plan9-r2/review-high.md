10 findings, most severe first:

1. `src/heterodyne/acceptance/ledger.py:264`: A retry fence for the same segment can have exactly the same bytes as an earlier fence, so it picks up the old fence's abort, released, intent and heartbeat receipts; the Fence record has no nonce or attempt number.

   Failure scenario: begin('S') then abort('x') (no heartbeat yet) then release(()) then begin('S') again produces identical fence.json bytes, since before_head, before_seq and heartbeat_sha256=None have not changed. abort-<digest>.json already exists, so every commit() fails with aborted_fence, and release() is allowed without a new abort. If the spool changed in between, or reconciled events are supplied, _atomic(released-<digest>) fails with immutable_file_conflict after events were already appended. The fence is stuck, and the segment can never be committed on retry.

2. `src/heterodyne/acceptance/ledger.py:567`: abort() saves a newly supplied intent as intent-<fence>.json, and stores it as an object, before the reason is validated, before the intent is checked against the fence heartbeat checkpoint or start event, and before the terminal_fence proof passes.

   Failure scenario: abort('bad reason', intent=X) fails validation on Abort.reason, or abort('r', intent=X) fails because the proof adapter returns Hold. In both cases intent-<fence>.json already holds X, which may point at an unrelated checkpoint or transaction. A later commit(intent=Y) on this fence then fails with immutable_file_conflict, so the fence can only be aborted. The retained 'prepared intent' was never actually prepared by commit.

3. `src/heterodyne/acceptance/ledger.py:436`: commit() checks the Stop and qualification events only for seq, previous hash and segment fields. It does not apply the Evaluator._events rules: chronology, inventory refs, the per-event 'event' proof, and boot_id/monotonic consistency.

   Failure scenario: A Stop whose `at` is earlier than the heartbeat, or whose refs point to an artifact that was never retained, passes commit and becomes an immutable terminal manifest. Later, Evaluator.archive over the same prefix fails with event_chronology or artifact_missing_or_changed. The sealed segment can never be archived, and the published chain cannot be fixed.

4. `src/heterodyne/acceptance/ledger.py:594`: release() checks reconciled events only for segment_id is None before appending them to the immutable chain. Stop/checkpoint shape, chronology, refs and event proof are not checked, so release can add events that every later Evaluator._events replay will reject.

   Failure scenario: A reconciled event with event='heartbeat' and checkpoint=None, or event='segment_stop' with stop=None, or an `at` earlier than the previous event, is appended once the release proof passes. Every later interval() or archive() over a chain prefix that includes it fails with event_checkpoint_shape, event_stop_shape or event_chronology, which blocks all later segments.

5. `src/heterodyne/acceptance/ledger.py:246`: The spool is never drained. release() records the reconciled spool_ids but never retires them, so MAX_SPOOL=1024 is a lifetime limit for the whole ledger, not a limit per fence.

   Failure scenario: Over a 72-hour run, sources keep spooling while fenced, as the design requires. Once 1,024 envelopes have accumulated in total, every spool() call fails with spool_overflow. Every later commit or release proof also re-reads and re-hashes the full history of already-reconciled envelopes. Source evidence then cannot be retained at all.

6. `src/heterodyne/acceptance/evaluator.py:517`: _archive_interval allows an empty tail after the Stop (`len(events[index+1:]) <= 1`), so archive does not require the qualification event that commit always publishes for a sealed segment.

   Failure scenario: The caller passes event_bytes cut off right after the segment_stop, or a chain where the qualification event was never published, for example a crash before _complete appended it. archive() still returns release_complete, even though the sealed segment's qualification event is absent from the replayed evidence.

7. `src/heterodyne/acceptance/ledger.py:194`: Fix 10 converted only a missing fence into a typed Hold. A missing or symlinked object, binding or terminal file in _get, _inventory, _complete or _heartbeat still raises a raw FileNotFoundError or OSError (ELOOP), not a Hold.

   Failure scenario: An inventory binding's object was removed, or the heartbeat object is missing after a partial crash. commit(), recover() or abort() then raises an uncaught FileNotFoundError instead of the typed diagnostic HOLD that the doc promises for 'missing originals ... holds for diagnosis'. Callers that handle only Hold or InvalidEvidence crash.

8. `src/heterodyne/acceptance/codec.py:109`: A native time with more than 6 fractional digits raises InvalidEvidence('invalid_native_time'), the same error as malformed input. It is not a distinct unsupported-precision Hold, although plan9-offline.md says 'unsupported precision holds'.

   Failure scenario: A genuine Marmot or native approval stamped with nanosecond precision (RFC3339Nano, '...14.123456789Z') is reported as malformed evidence with the same code as garbage input. Evaluator.entry() called directly raises a ValueError subclass, not Hold, so an operator cannot tell unsupported precision from corrupted evidence.

9. `src/heterodyne/acceptance/ledger.py:486`: Performance finding 8 is still open and the repairs make it worse. commit() now re-runs the full Evaluator.entry, every event proof and the coverage proofs while holding the publisher lock. commit() and release() re-read and re-hash up to 1,024 spool envelopes of up to 8 MiB each while holding spool.lock for the whole external proof call. archive verifies the entry and every event twice.

   Failure scenario: During a 72-hour segment commit, _events() runs 4+ times, _inventory() 3+ times and the full entry proof chain again. Meanwhile spool() from every source blocks on spool.lock until the adapter returns, which contradicts the doc's 'short' lock and independent-spooling assumption. _archive_interval runs _events (all proofs), then interval() runs entry() and _events() again. Caching the verified chain head, building the inventory once, and passing pre-validated events into interval would avoid the repeated work.

10. `src/heterodyne/acceptance/ledger.py:158`: `except BaseException: raise` in _atomic does nothing and is dead code. Failed prepared files are kept only because nothing deletes them, not because of this handler.

    Failure scenario: The handler and its comment suggest the retention logic is in this block. A maintainer who adds cleanup here would silently break the quarantine guarantee. Removing the try/except and moving the comment to the write site keeps the same behavior.
