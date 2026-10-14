Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 2498fd4071a256744fb72ffd865d148350aafe39 (range f2eb415f4619a3cfc74f20b59e58b16ea9465a14..2498fd4071a256744fb72ffd865d148350aafe39). Verdict: REVISE.

Note: code-review cycle 2 of 3; verifies the r1 fixes in 2498fd4.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:1835` — Abandonment deletes metadata without establishing that it belongs to the intent. After a P4 crash, replace the root and metadata with another install lacking a receipt. Recovery returns `RECOVERY_FAILED` and preserves the replacement tree, but deletes both replacement metadata files. Reproduced in `/tmp`. Preserve metadata whose ownership by the intent cannot be established.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:1761` — Abandonment checks root identity by pathname, then `_remove` independently opens that pathname. A replacement between those operations is chmodded and recursively deleted without an fd identity check. Reproduced during recovery abandonment: the unrelated replacement was deleted while the intent’s original tree survived elsewhere. Validate the opened deletion fd before mutation and perform recursive deletion through it.

The seal fsync fixes, `_reverify` fd check, byte-identical metadata checks, orphan-record refusal, and early OP_ARGS decoding passed review. Gzip `mtime=0` is appropriately limited to test determinism.

Validation: 285 offline harness tests passed; ruff, pyright, and diff whitespace checks passed. Worktree unchanged.

Verdict: REVISE