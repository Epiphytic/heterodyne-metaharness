I can't approve `8ba561c` in this round. I ran the built-in `code-review` skill once at HIGH against `5db4c0c..8ba561c`. It was read-only: nothing was edited, committed, tested, sent or posted, and no second review was run.

**Blockers (must fix before approval):**
1. **Security (`recovery.py:1158`):** the card splits each 64-character hash into groups of 8 so the existing redactor doesn't catch it. That breaks the old rule that full hashes stay private unless a safe public link exists. If a 64-hex secret ends up in a hash field, it gets posted to the Marmot group. The exemption for known digest fields should go inside the redactor as an allowlist, not be done by reformatting the hash.
2. **Correctness (`recovery.py:827`):** the table that tracks the current proposal is keyed only by operation. A native proposal and a legacy settle / abandon-notice / finish-health proposal for the same operation therefore replace each other, even though they cover different things. A legacy proposal the operator already consented to fails with "superseded" once a native proposal is registered.
3. **Integration (`recovery.py:163`):** the native target snapshot adds two new fields but keeps the `...target-snapshot.v1` label. A queue coordinator that builds the v1 snapshot from the shared fields will produce a certificate that never matches. Native supersede and permit then fail every time.

**Non-blocking:**
- **Weaker typing (`:171`):** `Register.descriptor` was loosened to `object`. msgspec's tagged unions would keep it typed.
- **Double decode (`:246`):** every descriptor is parsed twice.
- **Duplicated ID regex (`:314`):** it copies the bead-ID pattern instead of importing `BEAD_ID`.
- **Inconsistent budget unit (`:1216`):** native cards count bytes and legacy cards count characters. The byte test uses a non-ASCII name that production rejects anyway.
- **Wasted rendering (`:1186`):** native cards build the legacy lines and throw them away. The no-link insert positions are also wrong, though that path can never succeed.
- **Redundant check (`:311`):** `effects_root` is validated a second time.

On tests: the 117 passing tests don't catch any of the three blockers. Nothing covers mixed native and legacy proposals for the same operation, the snapshot label, or putting a secret in a hash field.

Earlier review rounds and historical reports were left as they were. Production qualification of the snapshot/fence observer is still a separate step.
