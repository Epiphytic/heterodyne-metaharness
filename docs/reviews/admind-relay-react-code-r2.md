1. [NON-BLOCKING] The contract still omits the deny-separator rules at [design.md:45](docs/superpowers/specs/2026-10-06-admind-relay-replies-reactions-design.md:45), and R29 still specifies threading updated content to the stale card. Fix: record the intentional R28 deviation and align R29’s threading wording with §5.

No blocking findings. R1 #1 is fixed across reconcile, unverified notices, `!asks`, pinned updated content, and `recover()`. Plain-reply recovery notices correctly thread to the initiating reply under §5.

Separator handling preserves exact approve matching and requires an explicit deny token. The tests assert non-first-chunk threading, absence of new outbox entries after rollback/CAS failure, and isolation of every updated-content chunk.

129 parser tests passed. Integration assertions were reviewed statically; the read-only sandbox prevents their required temporary-file writes.

APPROVE