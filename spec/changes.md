# Change lifecycle

Historical proposals are chronological append-only records, with explicit supersession
or revocation events. Proposal text and approval evidence never change after recording.
Lifecycle events may append; completed requires recorded approval, current spec updates,
implementation and infrastructure/dependency reconciliation, and verification evidence.
User authorization is evidence only for the actual requirements that user approved.
New proposals remain proposed pending real approval.

Run `python3 -m harness.governance validate` before review. The validator checks small
spec files, local links, runtime reference direction and historical lifecycle evidence.
Governance tooling may inspect historical metadata to validate it; runtime behavior,
SOUL and operating skills refer only to the active specification. Beads owns execution
tracking. A historical completed label is written only after deployment verification.
See [authority](README.md) and [maintenance admission](maintenance.md).
