Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: c5cdd0b0a649eaaed38eb47fed9eaf6720e3668a (range a88f45b32b7a02c8859c301101fba14b034f4b40..c5cdd0b0a649eaaed38eb47fed9eaf6720e3668a). Verdict: REVISE.

Note: both findings were fixed in beef4fa; see p4b-t5-code-r2.md.

- [BLOCKING] `src/heterodyne/sandbox/resources/codex_usage.py:86` and `:119`: The deadline kills only the app-server while `_converse` remains blocked in `readline`. If a descendant inherits stdout and keeps it open, the deadline cannot unblock the read; `_end` and scratch cleanup remain unreachable. An offline fake reproduced a **2.02-second wait with a 0.1-second deadline**. A persistent descendant makes this indefinite. Bound pipe I/O independently of EOF and add a descendant-held-pipe regression test that proves timely return and cleanup.

- [NON-BLOCKING] `src/heterodyne/session/shim.py:419`: `status_main` reads unlimited stdin before `run_status` checks `MAX_LINE`. A hostile oversized stream can exhaust memory before rejection; a stream without EOF holds the shim indefinitely. Use a bounded read and test oversized input through the standalone entry point. Existing tests exercise parsing limits only after bytes have been read.

Otherwise, the diff follows Task 5 and the ADR’s staged scope. Capabilities remain off, subprocesses use no shell, diagnostics omit raw output, and ingestion validates observation labels and percentages. The three declared deviations are benign.

Validation: **150 offline tests passed**, ruff passed, pyright reported zero errors, and `git diff --check` passed. No repository files were edited.

Verdict: REVISE