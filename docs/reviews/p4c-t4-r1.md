Reviewer: gpt-6.1-sol (Codex, workspace-write sandbox), cross-model; author claude-opus-5-5.
Reviewed commit: f2eb415f4619a3cfc74f20b59e58b16ea9465a14 (range 9940101de80fe7936452ac741d675fb3822bd68c..f2eb415f4619a3cfc74f20b59e58b16ea9465a14). Verdict: REVISE.

Note: code-review cycle 1 of 3.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:1977` — Recovery restores 0555 without fsyncing the root before committing the receipt and deleting the intent. After a crash at `P2.renamed`, recovery can report `published`, then a power loss can lose the seal and leave a writable root with no recovery intent. An fsync recorder confirmed recovery never syncs the root. Make the seal durable before P3.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:1976` — Recovery changes the opened root’s mode before checking its identity. Replacing the install path between the check at line 2054 and `_reverify`’s open causes recovery to chmod an unrelated directory to 0555 before refusing. Reproduced with an injected replacement. Validate `(dev, ino)` on the opened fd before any mutation.

- [BLOCKING] `src/heterodyne/harness/resources/store_core.py:2024` — When the staged manifest exists, recovery ignores conflicting committed metadata and overwrites it through `_complete`. Reproduced by crashing at P4 and replacing the meta manifest: recovery returned `published`. E21 explicitly requires conflicting metadata to fail closed. Validate existing meta files against the intent before completing recovery.

- [NON-BLOCKING] `src/heterodyne/harness/resources/store_core.py:2540` — Operation arguments are validated after recovery and sweep. Consequently, a malformed `list` request with unexpected arguments can delete expired staging before returning `BAD_REQUEST`; reproduced. Validate the complete request before allowing state changes.

Validation: 277 offline harness tests passed; ruff and pyright passed. Worktree unchanged.

Verdict: REVISE