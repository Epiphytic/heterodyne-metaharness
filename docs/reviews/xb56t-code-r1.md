Reviewer: gpt-6.1-sol. Reviewed commit: db3f3cb1e663c79b6af6245e7b869b588b481b96. Verdict: REVISE.

Note: code-review cycle 1 of 3, range 850d531..db3f3cb.

Review restricted to **850d531..db3f3cb1e663c79b6af6245e7b869b588b481b96**. Later worktree changes were excluded. No files were modified and no tests were run. Line references below refer to the reviewed endpoint.

1. **BLOCKING — Git success is checkpointed before ref durability is established.**  
   [sessiongit.py:389](~/repos/heterodyne-metaharness-v2-xb56t-impl/src/heterodyne/sandbox/sessiongit.py:389) performs `update-ref` without forcing durability, then [runtime.py:982](~/repos/heterodyne-metaharness-v2-xb56t-impl/src/heterodyne/sandbox/runtime.py:982) durably records `imported` and deletes the source. Git’s fsync policy is configurable and reference hardening is a separate component; command success alone does not guarantee the required persistence. [Git documentation](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corefsync)

   Consequently, a machine crash can preserve the intent/deletion while losing the keep ref or branch update. Replay from `imported` with only trash present proceeds to deletion without repeating the import. Establish object and ref durability before recording `imported`, regardless of repository fsync settings, and test that ordering.

2. **BLOCKING — The first intent’s containing directory is not durably created.**  
   [runtime.py:772](~/repos/heterodyne-metaharness-v2-xb56t-impl/src/heterodyne/sandbox/runtime.py:772) creates `<state>/resolutions/`. `write_private` syncs the intent and `resolutions/`, but neither it nor `private_dir` syncs the state directory containing the newly created directory entry.

   On the first resolve, a machine crash can therefore lose the resolution directory despite subsequent source removal being durable. This loses the recovery intent and hold, and potentially the pending audit event. Sync the parent before proceeding with resolution side effects; add a test checking that durability ordering.

3. **BLOCKING — The new tmux listing does not consistently fail closed.**  
   [tmux.py:99](~/repos/heterodyne-metaharness-v2-xb56t-impl/src/heterodyne/tmux.py:99) treats `_gone()` responses as an empty listing. That includes `lost server` and `server exited unexpectedly`, which indicate connection loss rather than an authoritative successful enumeration. Upstream tmux produces the latter through its lost-connection callback. [tmux source](https://github.com/tmux/tmux/blob/master/client.c)

   This can allow resolution to continue without establishing that panes/reapers are absent. Treat transport failures as `TmuxError`/`RuntimeUnavailable`; reserve empty results for established server absence. Test these raw subprocess responses, rather than only monkeypatching `session_names()` to raise.

4. **BLOCKING — Required test coverage is incomplete.**
   
   - [test_sandbox_runtime.py:949](~/repos/heterodyne-metaharness-v2-xb56t-impl/tests/test_sandbox_runtime.py:949) uses generation **1**, and the cases called “older reaper” and “older sandbox” also create generation **1** actors. They do not exercise the design’s required older-generation refusal. Use a newer record generation with an older actor and assert refusal before mutation.
   - [test_sandbox_runtime.py:1012](~/repos/heterodyne-metaharness-v2-xb56t-impl/tests/test_sandbox_runtime.py:1012) exercises the six runtime crash points, but calls `finish_resolution` directly without journaling. The daemon crash/journal-failure tests use `FakeRuntime`. Thus the required real durable-intent replay plus exactly-once event assertion across those checkpoints is not exercised. Add coverage combining the real runtime and journal, including journal failure retaining a `removed` intent and hold.

The eight reported deviations were checked explicitly:

| # | Assessment |
|---|---|
| 1 | **NON-BLOCKING:** Alternate supplied per Git call; full strict fsck excludes it, while later connectivity checks use the trusted alternate. |
| 2 | **NON-BLOCKING:** Lazy inspection cleanup is protected by the process-local threading lock. |
| 3 | **BLOCKING:** All-generation pane/reaper matching exists, but listing failures are not consistently closed; see finding 3. |
| 4 | **NON-BLOCKING:** Fresh resolves reject unfinished intents for the same workstream or session across generations. |
| 5 | **NON-BLOCKING:** Missing repository/worktree and pin failures become unreadable tips, permitting explicit discard without `--tip`. |
| 6 | **NON-BLOCKING:** Resolution drops the owed WIP mark; retained worktree changes and this behavior are documented. |
| 7 | **NON-BLOCKING:** Success directs the operator to `wsd tick pickup --ws <ws>`. |
| 8 | **NON-BLOCKING:** Scheduler hold-message assertion was updated consistently. |

No additional blocking issue was found in snapshot input filtering, confirmation checks, keep-ref conflict handling, replay identity checks, lane keys, transactional `emit_once`, or holding `Parker.entry()` through journal and finalization.

Verdict: REVISE