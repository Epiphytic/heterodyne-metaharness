REJECT

- **[BLOCKING]** The digest pins the ask’s references, but the btq design gate only compares digests. If revision A is approved and the document advances to revision B, the ask can still resolve to A and pass. The gate must also verify that the content being implemented is the approved revision.
- **[BLOCKING]** “Non-decision metadata” has no defined boundary. It appears to include `context_digest` itself and mutable workflow fields, making the hash self-referential or causing valid approvals to fail as state changes. Specify the canonical fields and exclusions.
- **[NON-BLOCKING]** §6.2 still bans hex strings on cards while requiring a 12-character hex digest. State that exception explicitly.