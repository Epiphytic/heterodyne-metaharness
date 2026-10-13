Reviewer: gpt-6.1-sol. Reviewed commit: 756c1316cca954f1dd94f42375cf085f36474458. Verdict: REVISE.

Note: cycle 1 of 3 of the cross-model design review (author claude-opus-5-5).

1. **[BLOCKING] Recordless replay can silently discard unpreserved commits.** [Design:61](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:61) accepts any recordless directory with the supplied short ID. Nothing proves that a previous resolve imported its objects, or even that it belongs to the requested workstream. A record lost during launch would also qualify; `--keep` could then delete it without keeping anything. Partial `rmtree` also contradicts line 55’s claim that the unlanded record remains: `_records()` ignores missing records, so the workstream hold can disappear before cleanup finishes.

   **Fix:** Persist a trusted resolution intent outside the deletion subtree, including workstream, bead, generation, confirmed tip, action, destination ref, and progress. Keep the hold until cleanup completes; permit recordless replay only against that intent. Test an unrelated launch orphan, a wrong-workstream retry, and pickup after partial deletion.

2. **[BLOCKING] The view’s untrusted-object read path is unspecified.** [Design:19](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:19) promises read-only inspection but defines neither object-store validation nor quiescence for that operation. Running Git against the private directory would read agent-controlled configuration; merely passing its objects as an alternate would still expose agent-controlled alternates and links. Existing `_trusted_alternates` rewrites `objects/info/alternates`, so it cannot satisfy the read-only promise.

   **Fix:** Specify a shared safe inspection/import boundary: pinned host Git only, validated private object files, trusted alternates, and confirmed absence of writers before inspection. Use a temporary trusted inspection store if necessary to avoid modifying the source. Test malicious config/hooks, redirected alternates, symlinks, special files, malformed objects, and failed runtime listings for both the view and `--keep`. Subject sanitization is appropriate, but test invalid UTF-8 and embedded line/control characters too.

3. **[BLOCKING] An unreadable branch cannot be discarded.** [Design:52](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:52) requires a readable tip for every action, although the stated problem explicitly includes unreadable branches. `_tip` rejects missing or malformed refs; consequently neither inspection nor `--discard` provides recovery for that class of hold.

   **Fix:** Return a per-session inspection error without aborting other views, and define an explicit discard confirmation for an unavailable tip, bound to the trusted session identity and generation. Keep land/keep unavailable unless a valid commit can be established. Test missing refs, malformed packed refs, and unavailable objects.

4. **[BLOCKING] Success journaling has an unrecoverable crash window.** [Design:63](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:63) emits the event only after deleting the source of its identity. A crash or journal failure between deletion and emission leaves a completed destructive operation without an audit event. The stated “once on success” test does not cover this.

   **Fix:** Use the durable intent from finding 1 to recover and emit completion exactly once, with a stable operation identity. Include crashes after complete deletion, before emission, after emission but before acknowledgement, and journal-write failures. A partial failure must retain recoverable progress rather than merely emit nothing.

5. **[NON-BLOCKING] Make confirmation apply to the actual import tip.** [Design:31](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:31) checks `_tip`, then calls `land`, which independently reads `_tip` again at `sessiongit.py:144`. Confirmed quiescence prevents an agent race, but the API does not enforce the promised equality at the point of import.

   **Fix:** Pass the validated full SHA into the import/land helper and use that SHA throughout. Explicitly check all sandbox generations and reaper names, treating listing failures as unknown. Add a test changing the ref between the initial check and helper execution; the moved-tip-before-resolve test is insufficient.

6. **[NON-BLOCKING] Specify lane request identity.** [Design:46](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:46) correctly chooses the workstream lane, but leaves `key` undefined. `Lane.submit` coalesces queued requests sharing a key. A generic resolve key could make a keep request receive the result of a discard request.

   **Fix:** Include session, tip, and action in the key, or use unique request identities. Execute mutation and completion journaling within the lane job and existing operation-lock convention. Test different actions and sessions queued behind pickup, plus caller disconnection.

7. **[NON-BLOCKING] Clarify namespace ownership and ADR attribution.** [Design:3](~/repos/heterodyne-metaharness-v2-xb56t/docs/superpowers/specs/2026-10-13-xb56t-resolve-unlanded-design.md:3) asserts D26/§9 conformance, but the supplied ADR’s §9 is scheduling, and I could not locate a D26-labelled decision there. `refs/heads/unlanded/...` is distinct from `btq/...`; however, an existing `unlanded` branch or prefix can cause a ref conflict.

   **Fix:** Cite the actual governing decision and document reservation/conflict behavior for `unlanded/`. Test prefix conflicts with fixed, path-free refusal text. These commands do not inherently require a substantive ADR amendment if they retain operator-only access, safe object import, and fast-forward-only bead landing; correct the conformance references and document the new recovery contract.

Verdict: REVISE