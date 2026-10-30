# Round-two repair: retained cleanup and integrity ordering

This candidate follows actual Claude built-in code-review HIGH round two on frozen `8b4d7885ed274cab95de950530ec441e388fc675`. The actual verdict was **REQUEST CHANGES**, preserved in `r2-actual-report.md` with home-prefix-sanitized invocation/start/result copies and hashes of every exact controller artifact. Exact raw originals remain unchanged in controller evidence; public copies replace the local home prefix with `${OPERATOR_HOME}` to satisfy the repository install-agnostic evidence policy. Round one and all original evidence remain unchanged. No review count has been reset and no review repeated for validation. The next controller gate is cumulative round three MEDIUM with the operator's exact PoC question.

Source commit: `c25ac16c613c02177436cf0e63b2569be82883ae`; source tree: `daae1b6a1bd424bccb0352012fbfc5b3b9bac7c2`. The frozen reviewed parent is retained unchanged in its original worktree.

## Disposition

1. The review explicitly allowed documenting and testing intentional pre-mutation HOLD instead of moving cleanup ahead of the hold. This candidate takes that alternative. A native continuation with typed Codex reasoning leaves even a crashed generation's allocation, record and home unchanged; supported explicit stop or normal session reconciliation performs cleanup and settlement. A causal test kills the actual test tmux pane, asserts no backend create/delete or record mutation at refusal, then proves explicit stop ends and removes the retained allocation. This is an offline fake backend with actual test tmux, not OpenShell isolation qualification.
2. The ADR example now uses `reasoning_effort`, and an `effort` typo produces an actionable typed-key hint. Other unknown keys remain refused and generic argument permissions stay empty.
3. The read-only qualification gate reports recorded unlanded commits or owed lifetime WIP before either missing-state diagnosis or reasoning HOLD; it never settles them as a side effect of an unqualified launch. Parametrized causal tests retain a dead generation with each integrity condition and require its specific diagnosis and unchanged record/allocation.
4. A malformed typed setting encountered at the runtime boundary becomes a confirmed `LaunchFailed`, not an unexpected `ConfigError`. A direct runtime test requires no backend creation.
5. Configuration documentation explicitly covers adding typed reasoning to a previously default-profile session, crash retention and supported recovery. Tests cover that actual profile transition, missing retained state refusal, and an explicit fresh handoff succeeding. Live idempotent readback and both caller resume flags remain covered by existing repair tests.

The gate has a named private helper because its Codex qualification and integrity ordering are a distinct read-only boundary from the generic mutating launch pipeline. It uses the existing adapter/state reader and does not establish actual native continuation effort. No host sandbox, service, credential, queue or deployment changed.

## Qualification limits

The accompanying receipt pins exact commands, source hashes and actual logs. The incremental quality report exits 2: three recent-review-fix churn findings and existing runtime class growth from 1367 to 1381 lines. The extracted gate removes the introduced launch complexity regression; it does not erase class-size or historical cumulative interface debt. No quality clearance is claimed. Actual Codex continuation reasoning and actual OpenShell isolation remain unqualified.
