Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: e9a024166456033526e2ca5ef57ea4c9f03bdacc (range fe64b01bb13ff1fda4e1b1d69ae3eeec46985b06..e9a024166456033526e2ca5ef57ea4c9f03bdacc). Verdict: APPROVE.

No blocking or non-blocking findings.

Task 7 conforms to the approved design and ADR. The capabilities_of deviation is sound: it uses the adapter table, returns NONE for removed profiles, and reads no login. Draining, queue bounds, rebinds, and end-time pruning are correct. All capabilities remain off by default.

Validation: 278 offline tests passed; ruff, pyright, and diff checks passed. Worktree remains clean.

Verdict: APPROVE