Two blocking gaps remain at `681a6ea`.

1. **[BLOCKING] The rebuttal preserves the pin safeguard but still fails to send updated context.** [Design delta:101](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:101) correctly identifies that an edited pinned bead cannot be decided: [approve-bead:616](<BTQ-LIVE>/bin/approve-bead:616) refuses it, and [btq:588](<BTQ-LIVE>/bin/btq:588) rejects conflicting recorded digests. Automatically renewing that pin would weaken the existing safeguard.

   That establishes why approval must remain blocked; it does **not** establish why the operator receives no updated content. The fixed requirement says to “send them the updated one,” while R29 and [the test plan:77](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:77) explicitly require no fresh card. Moreover, the claimed controller renewal is neither implemented nor required here: [cli.py:221](src/heterodyne/admind/cli.py:221) merely returns the terminal outcome.

   **Fix:** when the persisted pin alone prevents reposting, deliver an updated context card explicitly marked blocked, subject to R21 and the size cap. It must reject decisions and explain that the originator must renew the pin. Keep bead metadata untouched. Replace the “no fresh card” assertion with a real-btq test proving updated content is delivered without manual repinning and approval remains impossible.

2. **[BLOCKING] Changes after the preflight read bypass the fresh-card flow.** [Design delta:46](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:46) limits R29 to a digest mismatch found at step 5. If the bead changes afterward, `approve-bead --expect-digest` safely refuses, but [approvals.py:226](src/heterodyne/admind/approvals.py:226) classifies the open, undecided read-back as `untouched`, and [daemon.py:999](src/heterodyne/admind/daemon.py:999) reopens the old card. No updated card follows.

   **Fix:** extend refresh settlement to an `untouched` read-back whose digest differs from the attempt’s. Preserve `settled='untouched'`, the compare-and-set, and atomic replacement/reply insertion; retain existing handling for recorded, partial or uncertain outcomes. Add a test that changes the bead between preflight and the decision subprocess, verifies nothing was written, and requires updated context without another operator action.

All other earlier findings are resolved at the design level, including the retiring-attempt exception, legacy details reads, transaction rollback tests, reaction decoder/actor handling, recovery threading, audit correlation and operator fixtures. Code confirms posting never takes `work_lock`; today `judge_message` requires adaptation for `actor`, and `ReactionAdded` discards `event_id_hex`.

The declared trust change is honest. The revised live-isolation recipe covers separate Marmot homes, explicit private btq/database/policy locations, credentials/TLS and hostile inherited overrides. No live tests were run during this read-only review.

REVISE