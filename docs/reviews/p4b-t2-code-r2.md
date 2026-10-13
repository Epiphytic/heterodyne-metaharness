Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5. Cycle 2 of 3.
Reviewed commit: 7757db0fc59be15ac5c6bcb33a9d1752916ac660 (range 5105158ccc8b1a4be22844520a93e2b6f1cc094b..7757db0fc59be15ac5c6bcb33a9d1752916ac660). Verdict: APPROVE.

Note: the non-blocking residual (same-uid substitution of a real private directory) is outside D8's promise per the reviewer; deferred.

[NON-BLOCKING] `src/heterodyne/wsd/credlock.py:37`: A same-uid host process can rename `credlocks` while a holder runs and substitute another owned 0700 directory. A second holder can then lock a different inode for the same key. Mode 0700 does not prevent same-uid mutation. This requires write access to the trusted host namespace; Task 2/D8 does not promise exclusion against malicious host-side namespace replacement. ADR r16’s same-uid install hardening is a separate boundary. The residual does not block this stage.

The r1 symlink finding is resolved: swaps before directory opening fail closed; swaps afterward cannot redirect the relative lock-file open. The three regression tests prove those cases. Existing `open_lock_file(path)` callers remain compatible. Descriptor cleanup covers validation failures, file-open failures, lock failures, and body exceptions; no leak found.

All 40 focused offline tests passed; ruff, pyright, and diff checks passed. Repository unchanged.

Verdict: APPROVE