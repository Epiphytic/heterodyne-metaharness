# R1 disposition

Actual first built-in HIGH review returned changes on frozen f07838be05eea548d4a4e3cd1f5907fd37abc5e6; the report is preserved verbatim. No approval verdict is manufactured.

1. The correlated one-use insert commits before the late lifetime check. A later parent loss cannot roll it back; a causal mapped-root test faults that exact post-commit check and verifies the reservation survives three process attempts.
2. SQLite now allows a bounded five-second busy wait. An actual competing writer test holds and releases BEGIN IMMEDIATE while the journal transaction waits. Storage failure still means HOLD, not permission to retry consumed native authority.
3. Negative never-issued settlement checks the authoritative SQL row/events without requiring a live parent. The entry attempts that safe settlement on a later failure. An issued event or ambiguous failed write never clears a target by assertion. Native ordered consumption remains the earlier durable authority; this component does not promise recovery from unavailable storage.
4. Complete table/index SQL, including UNIQUE and CHECK constraints, is compared against the same source schema in an independent in-memory SQLite database. A real weakened partial index is refused. No old schema is silently migrated.
5. A correlated expired record is durably retained as failed-no-effect before refusal; the mapped-root test then restores the clock and confirms that the consumed record is still not reusable. Invalid transport/profile records cannot establish a valid admission; native consumption remains recorded independently, and no global admission is claimed for an invalid handoff.
6. Entry handles TypeError, KeyError and AttributeError with the same fixed HOLD marker and safe negative settlement. It does not turn errors into successful authorization.
7–8. The short bounded repeat profile read remains independently rehashed and parsed from the same bytes; duplication/complexity debt is retained rather than hidden by widening the authority API or refactoring existing outage storage during this slice.
9. SQLite's fixed database and sidecars stay within a descriptor-walked root-owned private directory. The database inode is rechecked; paths are not caller-selected. Existing root-equivalent operators are explicitly trusted by the approved design. A custom SQLite VFS or excluding trusted root administrators is outside this finite component; no weaker directory ownership is accepted.

Native host-parent review additionally required distinct nonce domains: bootstrap Consumed retains native nonce A, while admission FD6/metadata/reply use fresh B. Correlation now requires canonical native nonce A and refuses A==B, trusting the source-qualified native parent to authenticate A. The actual mapped-root test uses distinct nonces without modifying the consumed frame.

The earlier genuine native qualification JSON remains historical source-hash evidence, not a claim that the repaired source was retested against fresh production consent. Current synthetic kernel mechanics and causal fault tests are labelled accordingly. No effect event, permit or deployment success is issued by this component.
