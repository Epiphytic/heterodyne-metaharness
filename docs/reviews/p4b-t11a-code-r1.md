Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: c64393bc2f3f38281fc64b820c10a9c6403ff98f (range 5105158ccc8b1a4be22844520a93e2b6f1cc094b..c64393bc2f3f38281fc64b820c10a9c6403ff98f). Verdict: APPROVE.

Note: the brief's check line said "Task 4" by a typo; the reviewer reviewed Task 11 Part A as named in the brief's first line and range.

No [BLOCKING] or [NON-BLOCKING] findings.

The diff matches Task 11 Part A and stays within ADR r16. `CAPABILITIES` and `EVIDENCE` remain empty. The checker enforces all six specified invariants; tests exercise each rejection and validate the shipped table against actual adapter pins.

Validation: 116 targeted tests passed; ruff, pyright, and diff whitespace checks passed. No repo files changed.

Verdict: APPROVE