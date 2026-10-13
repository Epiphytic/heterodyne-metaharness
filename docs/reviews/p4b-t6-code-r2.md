Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5. Cycle 2 of 3.
Reviewed commit: 74df7a0b57d4a2ef17d241950fd3e1d7ba7de031 (range 543c9e3b5af9b4597c78caa6400e35e0370ce465..74df7a0b57d4a2ef17d241950fd3e1d7ba7de031). Verdict: APPROVE.

Note: refusing an atomic refresh as LOGIN_STALE is accepted as a conservative false positive; the mount-time ABA and post-check in-place rewrite residuals are deferred.

[NON-BLOCKING] `src/heterodyne/sandbox/runtime.py:546` — A legitimate atomic refresh to a later-expiry token changes the inode and refuses launch as `LOGIN_STALE`. This is an acceptable conservative false positive: the credential key remains unchanged, but the runtime cannot establish which inode was mounted. Two additional offline tests confirmed refusal, complete teardown, and an ended record.

The r1 finding is resolved for persistent repoints and shorter-lived replacements. Binding uses the checked paths; post-create validation runs before agent startup; the deadline retains the checked expiry. No new blocking findings. The acknowledged mount-time ABA and post-check in-place rewrite limitations remain.

Validation: 401 targeted offline tests passed, plus the two additional refresh tests. Ruff and Pyright passed. Repository unchanged.

Verdict: APPROVE