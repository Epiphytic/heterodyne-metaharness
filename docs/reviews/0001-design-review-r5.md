APPROVE

- **R4 pause race — resolved.** Both supported pause commands take the claim lock before acknowledging. Direct `btq pause` is explicitly best effort.
- **R4 denial transitions — resolved.** Denial cannot execute an action; the task receives the denial, and a denied design approval cannot satisfy the design gate.

No new issues.