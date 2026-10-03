# ADR 0001 design review r18 (revision 13, round 1)

Reviewer: gpt-6.1-sol (Codex, read-only, reasoning high). Author: claude-opus-5-5. Mode: cross-model. Reviewed: `design..b582246`.

- **BLOCKING — [docs/adr/0001-workstreams-v2.md:477](../adr/0001-workstreams-v2.md:477):** Membership transitions lack a completion/recovery contract. Recording the new expected count before mutation temporarily makes the existing count “wrong” under the latch rule. Specify serialization with guard checks, confirmed completion, and recovery after failure or restart; pending intent must not silently become trusted membership.

- **BLOCKING — [docs/adr/0001-workstreams-v2.md:472](../adr/0001-workstreams-v2.md:472):** Exact sender keys are insufficient to define ingress authentication. The §3.4 reference permits only registered workstream/control groups, excluding admind’s independent group. Explicitly retain authenticated sender verification, binding to admind’s configured group, and replay protection, using host `policy.toml` without depending on `wsd`.

- **BLOCKING — [docs/adr/0001-workstreams-v2.md:493](../adr/0001-workstreams-v2.md:493):** Redaction is contradictory. §8 promises verbatim `!details` reply text and redacts only the additional tool records for `!details full`; §11 requires both modes to be redacted. Specify whether “unabridged” means complete **after redaction**, and define redaction consistently across summaries, batches, and both details modes.

- **BLOCKING — [docs/adr/0001-workstreams-v2.md:492](../adr/0001-workstreams-v2.md:492):** A backstop batch can contain several turns, potentially from different operators, but threading and details retrieval refer to one originating message and “that turn.” Define batch-to-turn provenance and require `!details` to recover every included reply, with `full` recovering the corresponding tool records.

- **NON-BLOCKING — [docs/adr/0001-workstreams-v2.md:492](../adr/0001-workstreams-v2.md:492):** Clarify the minute cadence, handling of batches shorter than 50 lines, and whether skipped-line counts are calculated before or after deduplication. Also cover a broken reply pipeline explicitly; the listed triggers concern summarizer failures.

- **NON-BLOCKING — [docs/adr/0001-workstreams-v2.md:85](../adr/0001-workstreams-v2.md:85):** “Passthrough only” is stale now that admind runs a summarizer. Scope §6.2’s eight-line and no-pane-dump rules explicitly so §8’s batches and details are clear exceptions.

VERDICT: REJECT