Reviewed spec commit `653cbdb` against round 3 and the committed relay code, excluding implementation work in progress. Both round 3 blockers are resolved: pinned edits receive updated, undecidable content; edits after preflight trigger refresh on an `untouched` read-back.

No blocking design findings remain. The declared trust change is honest, and the revised locking, atomic settlement, recovery, redaction and delivery rules preserve the remaining security properties.

1. **[NON-BLOCKING] Specify recognition of reaction-triggered updated-context chunks.** [Delta:46](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:46) uses `askd:` keys, but [store.py:118](src/heterodyne/admind/store.py:118) accepts only a bare 64-hex request ID there. Reusing `r:<hex>` would make these chunks unrecognizable as cards, risking replies reaching the admin agent.

   **Fix:** require either the bare reaction event ID in these keys or an explicitly extended recognizer. Test a reaction-triggered pinned refresh, asserting every updated chunk resolves to the stale ask and subsequent replies/reactions never reach the agent.

2. **[NON-BLOCKING] Remove the unsupported automatic-renewal claim.** [Delta:101](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:101) still asserts that the controller renews the pin after `ask wait` returns. [cli.py:221](src/heterodyne/admind/cli.py:221) only returns the outcome.

   **Fix:** describe renewal as the originator’s required next action unless a concrete controller implementation is identified.

Code confirms posting never takes `work_lock`; the current guard needs adaptation from `message.sender` to `actor`, and the reaction decoder currently discards `event_id_hex`. R27 explicitly addresses both.

The test plan covers the major failure modes. Its live-isolation recipe covers separate homes, private database/repository/policy locations, credentials/TLS and hostile inherited overrides. No live tests were run in this read-only review.

APPROVE