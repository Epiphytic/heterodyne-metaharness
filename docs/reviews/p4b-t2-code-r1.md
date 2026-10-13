Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: 935043ce96651ac5e784e48a3f61d690cc7e6acc (range 5105158ccc8b1a4be22844520a93e2b6f1cc094b..935043ce96651ac5e784e48a3f61d690cc7e6acc). Verdict: REVISE.

Note: the blocking finding was fixed in 7757db0; see p4b-t2-code-r2.md.

[BLOCKING] `src/heterodyne/wsd/credlock.py:42–43`: Directory validation and lock-file opening are separate pathname operations. A process able to rename entries in the lock directory’s parent can replace `credlocks` with a symlink after `private_dir()` returns. `open_lock_file()`’s `O_NOFOLLOW` protects only the final file component. I reproduced two simultaneous same-key lock bodies using different inodes, violating D8 exclusion. Keep the validated directory descriptor open, open the lock relative to it, and add a directory-swap regression test. The current symlink test covers only a pre-existing link.

The remaining Task 2 implementation matches the plan. `lock_wait_seconds` belongs to Task 6; `Generator[None]` passes pyright. All 26 focused tests, ruff, and pyright pass. Repository unchanged.

Verdict: REVISE