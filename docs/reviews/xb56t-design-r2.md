Reviewer: gpt-6.1-sol. Reviewed commit: 3836e7849ae36577f7099dce0f925608516062c5. Verdict: REVISE.

Note: cycle 2 of 3 of the cross-model design review (author claude-opus-5-5).

Cycle-1 verification: findings 3, 5 and 7 are addressed: unreadable-tip discard is defined, import uses the confirmed snapshot SHA, and attribution plus namespace-conflict handling are corrected. Finding 6’s lane identity is fixed, but the operation-lock requirement remains unspecified. Findings 1, 2 and 4 are partly addressed; the remaining gaps follow.

All design references below are to [the revised design](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md).

1. **[BLOCKING] Rename-before-checkpoint crashes cannot replay.**  
   **Design:72, 84, 95.** A crash after renaming the session directory but before recording `moved` leaves an `imported` intent and no record under `sessions/`. Replay asserts that `imported` still has its record, and the precondition requires that record. This recreates the record-gone-but-git-left failure, now in `.trash`. The assertion at line 89 that nothing beyond the recorded step has happened is false.

   **Fix:** Reconcile filesystem state against the trusted intent. An `imported` intent with a matching trash directory and absent source must resume cleanup. Define refusal for conflicting source/trash identities. Specify directory fsync ordering for intent replacement, rename and final removal. Test crashes between each side effect and its checkpoint—not only after numbered steps.

2. **[BLOCKING] Journal completion and intent removal have no implementable ownership contract.**  
   **Design:74–75, 120.** The runtime’s sequence emits the event and removes the intent, but the wire contract assigns emission to the daemon after `resolve_unlanded()` returns `Resolution`. No finalization API or acknowledgement is defined. Removing the intent before returning reintroduces the unaudited destructive-operation window; retaining it leaves no specified way to finish.

   **Fix:** Define explicit prepare/complete methods: runtime retains the `moved` intent, daemon commits `emit_once`, then runtime finalizes the intent. Alternatively inject the journal operation into runtime resolution. Test journal failure, crash before emission, and crash after commit but before finalization.

3. **[BLOCKING] Recordless replay is not explicitly bound to the requested workstream.**  
   **Design:79–85, 95.** Replay checks action and tip against the intent, but does not require `request.ws == intent.ws`. Once the record has moved or been deleted, the record-based workstream check cannot apply. A retry naming another configured workstream could consequently execute cleanup on the wrong lane, outside serialization with the actual workstream’s pickup.

   **Fix:** Validate the complete trusted intent identity, including workstream, before replay or mutation. Use the intent’s identity for journal emission and hold handling. Add wrong-workstream retries at every checkpoint, especially after rename and deletion.

4. **[BLOCKING] Loose-object validation does not establish the promised snapshot integrity.**  
   **Design:32.** `cat-file -t` establishes an object’s type, while `fsck --connectivity-only` deliberately omits full object-content validation. This does not establish that loose-object contents match their filenames, nor explicitly require that the confirmed tip is a commit. The view consumes the snapshot before the later strict import could detect problems.

   **Fix:** Validate copied loose objects’ hashes and contents with full strict checking, and explicitly require a commit tip before reporting counts or subjects. Specify that keep uses the same strict reachable-object import as land. Add validly compressed objects stored under incorrect hashes, malformed blobs and non-commit tips to the tests.

5. **[BLOCKING] The shared snapshot preconditions contradict automatic landing.**  
   **Design:34, 93–95; `src/heterodyne/sandbox/runtime.py:545`.** Automatic `_end` is moved onto the snapshot path, but the design requires every snapshot to have an ENDED, unlanded record. `_end` calls `_land` while STOPPING, before recording ENDED; ordinary successful landings are not unlanded. Its reaper kill also suppresses errors, which does not establish the new all-generation absence requirement.

   **Fix:** Separate operator identity preconditions from the shared quiescence boundary. Permit automatic landing from a durably STOPPING record after confirmed teardown, and specify how all reapers are confirmed absent. Test automatic landing with failed reaper termination and stale-generation reapers.

6. **[NON-BLOCKING] Preserve the existing operation-lock convention explicitly.**  
   **Design:101.** Workstream lanes serialize these socket jobs with pickup, but the design omits the existing `Parker.entry()` lock held across workstream operations. Cycle 1 explicitly requested both.

   **Fix:** Require the operation lock across validation, snapshot, mutation, journal completion and finalization, with a test against a concurrent operation using that lock.

Subject sanitization, fixed refusal reasons, fail-closed runtime listings, and the `unlanded/` namespace are sound design choices. That namespace is distinct from `btq/`; the specified prefix-conflict refusal handles existing branches. These commands do not inherently require an ADR amendment, provided the corrected recovery contract preserves operator-only access and fast-forward bead landing.

The proposed tests are useful, but their crash coverage must include the gaps above. No files were modified.

Verdict: REVISE