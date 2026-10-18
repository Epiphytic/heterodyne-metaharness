**Verdict: REVISE**

I read the full range 597a1d4..2dd16a9 (one commit), the ADR, the helper doc, the contract and the failure map. I also compared the new runner against the original private D4 runner (`scanner-runner-preparation/scanner_d4_runner.py`) and the prior `h92_runner.py`. I ran no tests, probes or edits.

Some things check out:
- The failure map hash matches the ADR value (`12bd59c8…`).
- At H e22f4ed, the only source change from BASE is the hostread test, so treating H92 as a baseline phase is consistent.
- H92 is pinned to the fixed test hash `35e94c…`.
- The verifier/mask chunks and the 19 probes run through unchanged.

The diagnostic selection gives 318 + 55 + 2 = 375 nodes in the original collection order.

The ten findings below are ranked most severe first:

10 findings, most severe first:

1. `tests/test_scanner_recovery_environment.py:11`: The new test module imports `scripts.scanner_recovery`, which only resolves when the repo root is on sys.path. There is no `scripts/__init__.py`, no pytest `pythonpath` setting and no root conftest, and existing tests (e.g. test_tiers.py:15) add `ROOT/"scripts"` to sys.path explicitly instead.

   Failure scenario: Hosted CI runs `uv run pytest -q` (.github/workflows/ci.yml:26). The pytest console script does not put the working directory on sys.path, and prepend import mode only adds `tests/`, so collection fails with ModuleNotFoundError: No module named 'scripts'. pytest then stops with a collection error and the unfiltered hosted CI fails. The reported '11 PASS' only works because `python -m unittest` puts the working directory on sys.path.

2. `scripts/scanner_recovery/runner.py:818`: The runtime socket/bus check and the runtime identity check run before `archive_scratch` in the same try block. If either fails, the scratch archive is skipped completely, and line 826 replaces the earlier verdict with a generic HOLD that drops the original reason.

   Failure scenario: A test leaves a socket (or anything named `bus`) in `/tmp/hzX/r`, or changes the mode of `r`. require() raises ValueError, `archive_scratch` never runs, and `scratch_archive_inventory_sha256` stays null. The ADR requires an archive at completion including HOLD, but here the scratch is not preserved in exactly the case that needs it. Also, if the run had already held with e.g. 'Unexpected failure identities …', that reason is replaced by 'Scratch preservation/runtime check failed', and the outer wrapper's `previous` keeps only the replacement.

3. `scripts/scanner_recovery/environment.py:283`: `os.fwalk` is called with the default `onerror=None`. Subdirectories it cannot open (EACCES) are silently skipped, so the archive and its inventory are reported as complete without a HOLD. fwalk also classifies entries with `entry.is_dir()`, which follows symlinks, and opens directory entries without O_NOFOLLOW, so external symlink targets are stat'ed and opened.

   Failure scenario: A test leaves a 0o000 or 0o300 directory under basetemp (patterns exist in test_harness_paths.py:201, test_config_check.py:111, test_harness_selection.py:108; they restore permissions in `finally`, but a killed or timed-out test will not). The subtree is silently left out and the result still records an archive hash. Separately, a symlink in scratch that points at /run/user/UID or an automount directory is opened by fwalk (CPython os.py:499/525), contrary to the rule never to follow symlinks or inspect external targets. The unit test's `dirlink` case passes only because fwalk does not descend after opening the target.

4. `scripts/scanner_recovery/runner.py:478`: Plan mode returns before any of these checks: inventory coverage, the diagnostic `--failed-result`/`--failed-collection` hashes, the 375-node selection, the probe/supplement checks. They only run after the release, after `run_dir` is created and after the verifier-probe child has been launched.

   Failure scenario: The controller runs plan mode, which succeeds, and releases the exact hashes. The execute run then fails with 'Source inventory coverage changed', or because `--failed-result` was omitted (line 625). A HOLD `result.json` is written in a run directory that can never be reused. Under the one-attempt, no-rerun rule, an input error that plan mode could have caught uses up the stage attempt and forces a new authenticated disposition.

5. `scripts/scanner_recovery/runner.py:429`: The diagnostic predecessor is a special case outside the STAGES `required` lists. For create-red, b1, overlap, b2 and create-green it is checked only for hash, stage, PASS-375 and the 57 fields. Unlike the general predecessor loop, it is never bound to the receipt's task, B1 three-file hashes, B2-before hashes or h_delivery evidence. No predecessor is bound to this driver, supplement or disposition hash either.

   Failure scenario: The b1 receipt names a different PASS-375 diagnostic result, for example one run against another B1 candidate or with a different supplement or disposition, than the one t1 and h92 were chained to. The b1 stage accepts it, so the ordering diagnostic → H92 → t1 → b1 is not bound to a single source and evidence chain. Fix: add "diagnostic" to every later stage's `required` list, remove the special block, and compare `driver_source_sha256` and the disposition/supplement hashes.

6. `scripts/scanner_recovery/runner.py:732`: H92 is weaker than the prior `h92_runner.py`. `collection_check` is non-broad for h92, so it only checks count == 92 and uniqueness. There is no check that every node is `tests/test_sandbox_hostread.py::…`, no check that the added regression node sits between its manifest predecessor and successor, and no clean-porcelain requirement.

   Failure scenario: A dirty H worktree whose porcelain matches the controller receipt passes the gate. So does a 92-node collection that drops the added regression node and gains another hostread node (or, for example, a conftest-generated node). The prior H92 runner rejected both: `not before['porcelain']`, the startswith check and the delta adjacency check.

7. `scripts/scanner_recovery/environment.py:203`: `source_candidates` only scans `*.py` under src/ and tests/ for the tokens `.sock`, `AF_UNIX`, `tmux` and `short_dir`. Yet `validate_inventory` treats `covered == set(candidates)` as complete whole-suite socket/tmux coverage.

   Failure scenario: Actual bind and connect lines are not candidates: src/heterodyne/admind/ctl.py:113 `sock.bind(str(self.path))`, ctl.py:267 and wsd/ctl.py:257 `open_unix_connection(...)`, session/server.py:85. Shell scripts that tests run (docs/spikes/s9-10/inner.sh `TMUX_TMPDIR=/tmp tmux -L s9-10`, via tests/test_spike_s9_10.py) and defaults.toml `helper.sock` are never scanned. An inventory can be accepted as reviewed and complete while real endpoint sites were never presented to the controller.

8. `scripts/scanner_recovery/runner.py:123`: The runner checks that the supplement's original_* lists match the manifest, but never checks that `required_fields == original_required_fields + additional_required_fields` or that `environment_allowlist == original_environment_allowlist + ["XDG_RUNTIME_DIR"]`. `validate_result` and `validate_environment` only check counts (57 and 14) and key-set equality.

   Failure scenario: A supplement that swaps one original mandatory field (e.g. `per_node_durations`) for another name, or swaps an original env key for `DBUS_SESSION_BUS_ADDRESS` while staying at 14 keys, passes every runner check and the four construction probes. The guarantee that the original 51 plus six fields and the 13 keys plus XDG are preserved then rests only on someone noticing the supplement hash in the release.

9. `scripts/scanner_recovery/environment.py:238`: The socket maximum is computed as `len(base) + component + suffix`, which leaves out the path separators between the base, the case component and the suffix. `filesystem_preflight` also records the /tmp parent as a hard-coded `{uid:0, mode:0o1777}` instead of a measured lstat.

   Failure scenario: Base `/tmp/hzXXXXXXXX/p` (17 bytes) + a 32-byte tmp_path component + the 38-byte admind suffix gives 87 accepted, but the real path is `base + '/' + component + suffix`, one byte longer. Each additional separator or component a reviewer leaves out of the suffix pushes the real path further past the recorded `maximum_encoded_bytes`, so the evidence understates the actual budget. The preflight evidence states the parent owner and mode without measuring them at preflight time.

10. `tests/test_scanner_recovery_environment.py:15`: None of the ~575-line `runner.main()` is tested: no tests cover predecessor ordering, the diagnostic gate, release binding, H92 file exclusions, diagnostic 375-node selection, the HOLD-path archive, or the merge of the verdict with archive results.

    Failure scenario: Findings 2, 4, 5 and 6 above pass the reported '11 PASS' suite unnoticed. A regression that, for example, removes 'h92' from t1's `required` list or skips the archive on HOLD would also pass every current test. The tests only exercise environment.py helpers plus `slot_check` and `collection_check`.

There was also a lower-ranked issue I left out of the list: a timing gap (TOCTOU) between hashing and parsing input files. The inventory is hashed and bound into the release at runner.py:472 but parsed again at :558, after child processes have run. The disposition is read at :328 and hashed separately at :473, so the validated bytes are not proven to be the released bytes. Reading each file once, then hashing and parsing the same bytes, would close this.

No CLAUDE.md rules apply to these files.
