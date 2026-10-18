# Scanner helper: actual third MEDIUM review dispositions

Actual review: REVISE, two critical and eight lower findings. Repairs are unreviewed until the root-controlled follow-up. No scanner stage authority is inferred.

## Finding 1 — fixed

1. `scripts/scanner_recovery/environment.py:325`: The classifier checks for tmux before checking for socket tokens. A row that cites one tmux line and one socket line passes as kind 'tmux', so the socket line never gets a size bound (base, case_component_bytes, suffix_bytes, maximum_encoded_bytes, measured path).

   Failure scenario: Inventory row {kind:'tmux', isolation:'fixture-owned-S', guard_references:[...], references:['src/heterodyne/tmux.py:N', 'src/x.py:M']} where line M is `s.bind(str(tmp_path/'long'/'p.sock'))`. The coverage check passes, the socket line has no bound or measured path, and the per-base check after archiving only catches sockets that survive. A transient socket over 100 bytes in that constructor is admitted. This is the R2#5 'socket token requires bounded classification' repair.

Disposition: Classify obligations independently per exact reference. Socket references require a bounded socket row even if another row covers tmux; tmux references separately require an isolation row. One line can be covered by both row kinds without an impossible schema change.

Tests: test_mixed_tmux_socket_cannot_bypass_bound.

## Finding 2 — fixed

2. `scripts/scanner_recovery/environment.py:339`: The tokens that select candidate lines include short_dir and listen_url, but the list that forces a socket classification does not. A line picked only by those tokens can be covered by a 'non-endpoint' row that just gives a reason.

   Failure scenario: The ADR names 'short_dir direct TMPDIR fixtures' as a socket derivation. A Python line `d = short_dir(tmp)` or `listen_url = f'unix://{d}/x'` becomes a candidate, and then {kind:'non-endpoint', reason:'helper'} covers it. No socket bound or measured path is required, which goes against R2#5's claim that a reason-only non-endpoint row cannot cover socket lines.

Disposition: Candidate discovery and forced socket obligations share SOCKET_TOKENS, including short_dir and listen_url. Reason-only coverage cannot discharge either token.

Tests: test_shortdir_listenurl_cannot_be_nonendpoint.

## Finding 3 — fixed-bounded-review-aid

3. `scripts/scanner_recovery/environment.py:260`: The candidate scan skips every file whose suffix is not on the list. That includes .conf, which covers systemd drop-ins (foo.service.d/*.conf) and tmux.conf, plus other unit types (.timer, .path, .mount, .target) and .zsh/.env. It also skips extensionless files with no shebang, such as Makefile, Dockerfile, justfile and Procfile.

   Failure scenario: A drop-in `x.service.d/override.conf` adding ListenStream=/run/.../long.sock, or a `.tmux.conf`/Makefile that starts `tmux new -d` on the default server, never becomes a candidate. The coverage check passes with an empty review, and the R2#3 'systemd/extensionless completeness' fix misses these launch paths.

Disposition: Include .conf, .timer, .path, .mount, .target, .zsh, .env and named Makefile/Dockerfile/justfile/Procfile in addition to existing selected sources. This finite review-aid set never claims complete reachable source coverage; uncovered dynamic paths HOLD admission.

Tests: test_config_and_known_extensionless_launchers.

## Finding 4 — fixed-bounded-review-aid

4. `scripts/scanner_recovery/environment.py:279`: Python lines are matched case-sensitively against a narrow token list. 'TMUX'/'Tmux', '.connect_ex(', '.sendto(', socketpair, multiprocessing Listener/Manager and XDG_RUNTIME_DIR path construction never become candidates.

   Failure scenario: `os.environ['HZ_REQUIRE_TMUX']` (the audited tmux guard itself), `os.environ.get('TMUX')`, and `sock.connect_ex(path)` are left out of source_candidates. The tmux guard references and socket connects that the ADR requires to be inventoried are silently missed, and validate_inventory still reports full coverage.

Disposition: Casefold source tokens and add connect_ex/sendto/socketpair/Listener/Manager/XDG_RUNTIME_DIR. Shared token definitions prevent candidate/obligation drift. Semantic reachability remains controller review, not token equality.

Tests: test_case_and_additional_endpoint_tokens.

## Finding 5 — disclosed-scope-not-global-containment

5. `scripts/scanner_recovery/runner.py:413`: The quiescence check only looks at the original process group. Descendants that call setsid, or start a tmux server (which daemonises), escape killpg and group_has_writers, yet the record still says process_group_quiescent=True and archiving goes ahead.

   Failure scenario: The repo's own suites do this: tests/test_admind_r13_summary.py uses os.setsid, test_sandbox_runtime/test_wsd_lifetime start tmux, and test_tmux_watchdog uses start_new_session. In a b1/b2 run those processes survive and keep writing to h/, t/ or p/ while archive_scratch copies. Archive inventory and socket lengths then depend on timing, the processes leak past the run, and the evidence says 'quiescent' anyway (the docs disclaim this, but the recorded flag claims more than that disclaimer).

Disposition: Preserve existing process_group_quiescent compatibility flag and add explicit original-PGID-only scope plus global_descendant_quiescence_proven=false. No new containment architecture or setsid/tmux cleanup proof is claimed. Actual stage admission still requires fixture-owned cleanup/isolation inventory; the existing gap is UNWAIVED.

Tests: original test_normal_exit_kills_descendant_before_return unchanged; explicit scope fields/source documentation.

## Finding 6 — fixed

6. `scripts/scanner_recovery/runner.py:190`: finish_scratch catches only OSError, ValueError and KeyError. directory_entries recurses with a nested `yield from` per directory level, so a deep scratch tree raises RecursionError, which escapes main's finally block before result.json is written.

   Failure scenario: A test leaves a directory chain about 1000 levels deep under p/ or t/. The default soft fd limit (1024) is not reached first, so Python's recursion limit trips during the runtime walk or the archive walk. RecursionError propagates out of finally, put(result.json) never runs, and every collected diagnostic for the attempt is lost (a HOLD with no result file).

Disposition: Iterative descriptor DFS caps directory depth at64 child levels and entries at65536; overflow raises ValueError handled independently by runtime/archive HOLD paths while retaining scratch and allowing main result finalization. No recursive Python stack exhaustion.

Tests: test_deep_scratch_records_hold_without_recursion_error; unchanged wide-FD regression.

## Finding 7 — fixed

7. `scripts/scanner_recovery/runner.py:372`: group_has_writers only catches FileNotFoundError. A process that exits between open() and read() raises ProcessLookupError (ESRCH), and hidepid raises PermissionError; either one escapes run().

   Failure scenario: Any unrelated process on the host exits mid-scan. The exception aborts run() after the leader was reaped, record quiescent stays False, and the whole attempt HOLDs with the scratch kept in place. This is a spurious failure, not the documented 'vanished PIDs ignored' behaviour.

Disposition: ENOENT/ESRCH observations ignore vanished PIDs. Other /proc read errors, including permission denial, become explicit ValueError HOLD with original group quiescence false, not a false success.

Tests: test_vanished_proc_esrch_is_not_a_writer; test_inaccessible_proc_is_explicit_hold.

## Finding 8 — fixed

8. `scripts/scanner_recovery/runner.py:635`: Before execution, the runner rechecks the hashes of files that were already candidates but never re-runs source_candidates. A new file added after planning is not covered.

   Failure scenario: A new socket-binding .py (or an extensionless shebang launcher) appears inside a gitignored or already-untracked directory after the plan snapshot. The porcelain output stays the same ('?? dir/' or ignored), the candidate hashes match, and the run proceeds with that constructor never inventoried.

Disposition: Re-enumerate source candidates at execution preflight and compare exact map before checking whole candidate-file hashes. Newly appearing files now refuse execution. Existing source/ownership/release checks remain. No atomic hostile concurrent filesystem snapshot guarantee is claimed.

Tests: test_new_candidate_file_refuses_after_planning.

## Finding 9 — fixed

9. `scripts/scanner_recovery/environment.py:277`: For extensionless entries, source_candidates calls read_bytes() before checking that the entry is a regular file, so a FIFO blocks forever and a large binary is read whole into memory.

   Failure scenario: The worktree has an extensionless FIFO (for example a leftover named pipe from a fixture) outside the excluded directories. Planning hangs indefinitely in path.read_bytes() instead of HOLDing.

Disposition: Before opening a candidate/extensionless file require lstat regular type and <=8MiB. Open with NOFOLLOW|NONBLOCK, verify descriptor type/device/inode, then bounded-read max+1. FIFO cannot block and excessive files HOLD; unknown files are never silently treated as endpoint evidence.

Tests: test_fifo_is_rejected_before_read; test_oversized_regular_file_holds_before_read.

## Finding 10 — fixed

10. `scripts/scanner_recovery/runner.py:693`: INSPECT is filled in by replacing 'ROOT' and then 'MODS' in sequence. The second replace also rewrites any 'MODS' inside the already-inserted path string.

    Failure scenario: An active source path or run directory containing 'MODS' (for example /home/x/MODS/run) is corrupted inside the root=pathlib.Path(...) literal. The import-origin assertion fails or compares against the wrong root, giving a spurious HOLD or a misattributed origin check. <!-- install-agnostic: allow=home-path; verbatim synthetic reviewer fixture, not installation configuration -->

Disposition: Replace the single complete assignment template once, inserting literal root and module-map values together. No later token replacement scans the inserted path. Preserve original inspection assertions and dependency/import checks.

Tests: test_inspect_literal_substitution_preserves_mods_in_path.

