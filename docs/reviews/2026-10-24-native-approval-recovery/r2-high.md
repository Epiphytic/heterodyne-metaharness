I ran round 2 of the high-reasoning code review (the built-in `code-review` skill, not the plugin) over `5db4c0c..2e221dd`. **Result: not cleared, and not an APPROVE.** Nine candidate findings came back; the top three are potential blockers.

This was read-only: no edits, commits, tests, services, DB or queue access, network sends or GitHub comments. I didn't write a report file, so the R1 log and the author disposition are untouched. As you asked, there was no second validation pass, so every finding below is an unverified candidate (only duplicates were removed), ranked most severe first.

### Potential blockers

1. **A switch between legacy and native proposals is permanently refused** (`recovery.py:615`). `supersede()` refuses any proposal whose protocol differs from the operation's current one, and it does this before the terminal and permit checks. `maintenance_recovery_current` keeps one row per operation, and nothing clears it.
   - **Failure case:** operation X gets a legacy `maintenance-recovery.v1` settle proposal, which then ends (terminal, revoked or expired). The coordinator registers a native descriptor for X's remaining unknown effect. That raises `recovery-scope-conflict` every time, so the remaining native unknown can never be consented.
   - Terminal operations also report `recovery-scope-conflict` instead of `operation-terminal`.
   - This goes beyond the disposition's "one current proposal per operation": the refusal is permanent, not just a block on running two permits at once.

2. **Native hashes are split to get past the hex redactor** (`redact.py:173`). `native_recovery_digest_lines` breaks each 64-hex value into eight 8-character groups so it gets past the redactor's 64-plus hex-run rule. That rule exists to hide hex private keys. The only check is the shape `[0-9a-f]{64}`, which is also exactly what a hex private key looks like.
   - If a coordinator bug puts a 64-hex secret into `observation_sha256`, `plan_sha256` or `intent_envelope_sha256`, the card posts it to the Marmot group with a note that the groups concatenate without spaces.
   - The disposition admits this limit. Whether to accept it is your call: it holds only if the external writer is trusted not to put a secret in those three fields.

3. **The card states things admind never checks** (`recovery.py:1197`). The native card says "Earlier effects: terminal" and "Required: original writer and descendants ended". In admind, `native_descriptor` only checks `ordinal == effect_count`, and `original_provenance` only checks the operation record hash. A descriptor with ordinal 3 of 3 is accepted whether or not effects 1–2 are terminal. Unless the queue enforces those claims (deferred to the future combined review), the operator consents to guarantees admind doesn't provide.

### Lower severity

4. **Dead no-link branch** (`recovery.py:1217`). A native card with no `descriptor_links` entry gets the legacy 64-hex lines inserted at fixed positions. The redact check then always fails, so that branch can never render.
5. **Budget units differ by protocol** (`recovery.py:1220`). The native budget is counted in UTF-8 bytes and the legacy one in characters. Because actors must be ASCII, this does nothing in production. The test that covers it, `test_native_card_budget_counts_utf8_bytes`, uses an actor (`contrôleur`) that production can never supply.
6. **`supersede()` now always re-validates the stored descriptor** (`recovery.py:615`). Before, it only did this when a permit existed.
   - A stored legacy row that fails a check tightened since it was written could previously be replaced. Now every replacement for that operation fails before the state checks run.
   - When a permit exists, the same descriptor is decoded twice inside the write transaction.
7. **`descriptor()` parses its input twice** (`recovery.py:249`). Combined with about four calls per register, that means repeated parses of up to 256 KiB under `work_lock` or the store transaction.
8. **Native render still builds the full legacy line list** (`recovery.py:1169`), then discards it. The link line's position is a hard-coded index (10 vs 5), so adding or removing a native line would silently misplace it.
9. **The 64-hex rule is written in several places** (`recovery.py:314`, plus `redact.py`). The `effects_root` check is repeated, and the native-scope error for a bad `effects_root` can never fire because the general hash check raises first.

**Verdict:** the source isn't cleared. Findings 1 and 3 look like real gaps in how the slices fit together, and finding 2 needs your explicit acceptance of the residual risk. Separately from this source review, the production snapshot and fence qualification are still outstanding.

`★ Insight ─────────────────────────────────────`
- Finding 2 shows a common trap: changing the output so it slips past a pattern-based redactor turns off that safety net for those fields. What's left is the type labels, and the labels can't prove a value isn't secret.
- Finding 1 is about the order of checks. Running the "scope conflict" check before the terminal-state check means the operation can never leave the state where it was blocked.
`─────────────────────────────────────────────────`
