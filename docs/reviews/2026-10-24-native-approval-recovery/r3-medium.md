I ran the built-in `code-review` skill at MEDIUM on the exact R3 diff for base `5db4c0c` → head `acd0a58`. It only read source. I didn't edit anything, run tests, touch a DB, queue or server, send anything over the network, or run a second review. The R1 and R2 HIGH "NOT CLEARED" reports stay on record unchanged.

## Verdict: no critical flaws found. Good enough as a source-only PoC, but not for deployment.

None of the 10 findings is a safety break inside the PoC. The R2 problems look fixed: the global redactor matches the original again, native cards render only from the exact evidence-descriptor link, hashes aren't rendered, and an active permit can't be replaced across schemas. Fix the four correctness findings below before this goes past PoC.

### Correctness: fix soon, not blockers
1. **`recovery.py:636`, same-schema replacement drops consent.** The new "active proposal can't be replaced" rule only applies when the schema changes. Registering a second native descriptor with a different `verdict` or `observation_sha256` still quietly replaces a consented, unexpired proposal. That contradicts the new "Preserve one active scope" docstring and treats native-to-native and native-to-legacy replacement differently. This is the closest finding to a real flaw.
2. **`recovery.py:624`, raw `FileNotFoundError` escapes.** A cross-schema register after an expired permit with no certificate throws an OS error instead of a fixed `Invalid` refusal code. The new test expects `FileNotFoundError`, so the leak is now baked into the tests.
3. **`recovery.py:174`, `Register.descriptor` is typed as `object`.** Decoding the request no longer checks the descriptor's structure, so validation depends on the later `descriptor()` call. Tagging the structs with `tag_field="schema"` would keep the field typed and remove the hand-written schema dispatch.
4. **`recovery.py:336`, rollback strands rows.** If admind is downgraded, the older `descriptor()` rejects stored `settle-approval-native.v1` rows in the replace, permit and message paths. Any operation already registered as native gets stuck.

### Outside this PoC (already listed as separate gates)
- **`recovery.py:163`:** the external queue executor (y2luw) hasn't been updated for the native snapshot schema or `native_effect`. Until it is, native permits could strand or be misread by a legacy-only executor. You already listed actual queue recovery as gated separately, so this isn't a PoC blocker. It must be done before any real queue recovery.
- **`docs/admind/maintenance-recovery.md`:** doesn't describe the native descriptor, the snapshot schema, or the cross-schema replacement rule. Whoever builds the executor will need that.

### Cleanup (minor)
- `render` (line 1230) measures native cards in bytes and legacy cards in characters against the same character budget. Every rendered field is ASCII-only today, so it doesn't change anything yet.
- `descriptor()` (line 249) parses the JSON twice.
- `native_descriptor` (line 314) repeats an `effects_root` hex check that can never fail at that point.
- `render` (line 1169) repeats the `isinstance` check that's already stored in a local, and `health` is computed but unused on the native path.

These findings come from one review pass. As you asked, I didn't re-verify them.
