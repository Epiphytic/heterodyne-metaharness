Reviewer: gpt-6.1-sol (Codex, -s workspace-write). Author: claude-opus-5-5. Mode: cross-model. Reviewed commit: 1224a26929414b4ab376245cf50b2ac773fdfda7. Verdict: REVISE.

Note: cycle 2 of 3. Fixed in 4ac749b; aa03898 records the operator's §17 item 13 decision (ask b2m6: yes + verity).

Reviewed commit `1224a26929414b4ab376245cf50b2ac773fdfda7` against cycle 1, ADR r16 `93d0e90`, and plan 4b `e47dbf5`. I made no repository changes and ran only offline temp-file checks.

Cycle-1 findings 1–4 and 6–9 are addressed in the specified implementation and tests. E20 retains the verified entry baseline across rename; its root-only ctime exception does not reset file baselines. Taint-before-refusal ordering is explicit. E13’s required fields prevent vacuous approval, and its read-only consumption rule forbids repairing decisions. Plan 4b’s migration contract is preserved. Finding 5 remains incomplete, with additional recovery gaps below. The known verity edit is excluded.

References below are to the pinned [plan](~/repos/heterodyne-metaharness-v2-plan4c/docs/superpowers/plans/2026-10-13-plan4c-managed-installs.md).

1. **[BLOCKING] Recovery reintroduces the rehash-to-record race.**  
   E21, line 115, says recovery of an already-renamed tree performs a full rehash, then takes a fresh record. A same-size rewrite after a file is hashed but before recording becomes the accepted baseline. Subsequent `check_fast` passes although the bytes differ from the approved manifest. An offline temp-file reproduction confirmed this sequence.

   **Fix:** Use record → rehash and structural verification → unchanged-record comparison for recovery too. Retain that verified baseline through metadata publication. Recovery performs no rename, so it needs no root ctime exception. Add rewrite controls during rehash and between rehash and recording.

2. **[BLOCKING] P5 breaks recovery and leaves no durable staging-ID receipt.**  
   E21 deletes `.staged/<id>.json` before the intent. A crash between those deletions leaves an intent whose recovery requires a now-missing manifest. After both deletions, the listed metadata retains no staging-ID association, yet `publish(id, digest)` and `outcome(id)` must distinguish a completed retry from another staging that produced the same install name. Idempotence is asserted without specifying the evidence that establishes it.

   **Fix:** Persist a completion receipt binding staging ID, adapter, install name, and full approved digest before deleting recovery evidence. Define recovery for every cleanup boundary, including using the committed manifest when the staged copy is gone. Verify the receipt and complete tree before returning success. Expose the required recovery APIs through `Store` and the helper protocol. Test mid-P5 and a retry after P5 fully completes.

3. **[BLOCKING] Concurrent helper requests can recover an active publication.**  
   E3 uses one process per connection; the socket allows four connections. Every request runs recovery, but no cross-process store lock is specified. While request A is between P1 and P2, request B can treat A’s intent as abandoned and restart its publication. Both can then rename, write metadata, or remove evidence concurrently. E16’s `wsd` dispatch lock does not serialize these helper processes.

   **Fix:** Specify a store lock shared by helper processes, covering recovery and mutations, with equivalent synchronization for `LocalStore`. Recovery must acquire the lock before interpreting intents. Use atomic destination conflict handling and preserve unrelated existing installs. Add barrier-controlled tests for overlapping publish/recovery and two stagings targeting the same name.

4. **[BLOCKING] Durable publication does not fsync the install contents.**  
   Task 3’s implementation, line 608, fsyncs the staged manifest but specifies no fsync of extracted regular files or nested directories. E21 fsyncs metadata files and rename parents. Those operations do not establish durability of the approved tree’s contents. The proposed `os._exit` crash tests leave the kernel’s dirty cache intact and cannot demonstrate power-loss durability.

   **Fix:** Fsync regular files after their final writes and metadata changes, then directories bottom-up, before making the staged evidence or publication durable. Include `.staged` and newly created metadata directories in the durability ordering. Extend the fsync-order test to cover tree data and directory entries.

5. **[NON-BLOCKING] E13’s denial outcome is ambiguous for null readout fields.**  
   Line 146 treats “any `denied_*` field present” as denial, while predicate 5 requires null denial fields. The strict `Readout` always contains `denied_by` and `denied_digest`, including for approvals.

   **Fix:** Define whether this checks raw bead metadata or non-null readout values. Add a successful approval case with both required readout fields present and null.

6. **[NON-BLOCKING] Explicit verification’s record-refresh contract is too narrow.**  
   Task 4, line 638, permits rewriting the record only when device values changed. Restoring a modified file to its exact approved bytes leaves a different ctime, so rehash can succeed while the old fast record still fails. This conflicts with E17’s recovery through a matching full verification.

   **Fix:** Define explicit verification as structural validation plus race-safe full rehash and record refresh for a manifest-matching tree. Test restoration to approved bytes followed by successful verification and taint clearance.

Verdict: REVISE