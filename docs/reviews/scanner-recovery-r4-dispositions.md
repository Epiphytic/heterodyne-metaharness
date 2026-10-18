# Fourth MEDIUM author dispositions

Actual result: REVISE for finding1 only. All seven actual findings are retained verbatim in the JSON and raw review. This is an author repair, not a review verdict. Quality debt and actual stage-admission gaps remain UNWAIVED.

## Finding 1: fixed

1. `scripts/scanner_recovery/runner.py:199`: finish_scratch calls `result.get("socket_path_budget_inventory", {}).get(...)`, but main pre-initializes every one of the 57 required fields (including socket_path_budget_inventory) to None, so the `{}` default is never used and `.get` is called on None, raising AttributeError, which is outside the `except (OSError, ValueError)` handler.

   Failure scenario: Execute mode: create_layout succeeds, then filesystem_preflight fails (free space under 2GiB, exec-check fails) or revalidate_candidates fails before line 756. The BaseException handler records HOLD and the finally block calls finish_scratch. archive_scratch succeeds, then line 199 raises AttributeError ('NoneType' object has no attribute 'get'). The error escapes main's finally block before put(result.json), so no result.json is written for the failed attempt. This defeats the r3 Finding 6 disposition ('allowing main result finalization'). The same happens on a LayoutCreationError partial layout whose root identity is complete. Fix: `(result.get("socket_path_budget_inventory") or {}).get("receipt", {})...`, or catch broadly around the bounds block.

Treat the preinitialized None inventory as absent during failed-attempt preservation. Validated inventory still takes the existing surviving-socket checks. The attempt retains HOLD and its missing mandatory evidence; no PASS or stage authority is synthesized.

## Finding 2: UNWAIVED stage-admission gap

2. `scripts/scanner_recovery/environment.py:378`: A kind='tmux' row with isolation 'fixture-owned-S' gets no socket-length bound unless the cited line itself happens to contain a SOCKET_TOKEN, even though `tmux -S PATH` creates a Unix socket that the ADR's '<=100 bytes for every inventoried socket path' rule covers.

   Failure scenario: Source line `subprocess.run(["tmux", "-S", sock, "new", "-d"])` where `sock = tmp_path / name` is built on a line without .sock or other tokens (so it is not a .py candidate). The inventory row {kind:'tmux', isolation:'fixture-owned-S', guard_references:[...]} passes validate_inventory with no base, suffix bytes or measured path. Under basetemp p plus the pytest-of-user/pytest-N/<32-byte case dir>/ nesting, the tmux socket can go over 100/107 bytes. That shows up only as a test failure at bind time, not as the required pre-execution HOLD. Fix: require -S isolation rows to also cite a bounded socket derivation.

A fixture-owned -S isolation label alone is not a byte bound. Complete source-backed tmux socket derivation remains required before actual stage admission; this PoC does not establish it.

## Finding 3: UNWAIVED stage-admission gap

3. `scripts/scanner_recovery/runner.py:777`: In the overlap stage the executed source is a disposable copy with redact.py reverted to BASE, but the socket/tmux inventory and the candidate revalidation are bound only to the worktree `w` (inventory source_root == str(w)), never to the active tree that actually runs.

   Failure scenario: BASE:src/heterodyne/admind/redact.py differs from the inventoried worktree copy. Any tmux, socket or short_dir lines in the BASE bytes are never candidates and never classified, yet they run under `-k overlapping_kinds`. Also, copytree follows symlinks by default (symlinks=False), so the active tree can contain content that the nofollow inventory walk refused or never saw. The bounded-inventory admission claim does not cover the overlap stage's executed bytes.

The overlap active-tree inventory and copytree symlink behavior need exact executed-source coverage before that stage can be admitted. No overlap stage was executed or authorized by this repair.

## Finding 4: UNWAIVED manual-review dependency

4. `scripts/scanner_recovery/environment.py:383`: The tmux isolation and guard evidence is never tied to the content of the cited lines: guard_references only has to name any candidate key, and isolation values are accepted as self-declared labels.

   Failure scenario: A row {kind:'tmux', isolation:'audited-guard', guard_references:['pyproject.toml:1']} discharges a default-server `tmux new -d` line. The pyproject.toml line is a non-.py candidate, so it is always in candidates. Nothing checks that the guard reference contains HZ_REQUIRE_TMUX, `-S` or `-L`. For the PoC this rests entirely on reviewed_by_controller=True, which is fine only if that is stated explicitly as a manual-review dependency.

reviewed_by_controller is a trusted manual controller assertion, not machine verification of guard semantics. Every cited isolation/guard source and invocation must be reviewed; arbitrary guard reference labels are not proof.

## Finding 5: UNWAIVED stage-admission gap

5. `scripts/scanner_recovery/runner.py:851`: The pre-collection and pre-execution drift checks re-hash only PATHS plus git porcelain/diff. Candidate files that are gitignored or inside an untracked directory can change after revalidate_candidates (line 754) without detection.

   Failure scenario: After the execution preflight, an ignored helper .py under tests/ (porcelain stays '?? dir/' or shows nothing) gains a long-path `.bind(`. snapshot() == before still holds and the stage runs with a constructor that was never classified. A cheap fix is to call revalidate_candidates again inside the before_collection/before_execution loop.

Candidate-set revalidation exists before execution setup, but the later per-command snapshot does not re-enumerate ignored candidates. A final source-frozen invocation closure remains a stage prerequisite; no actual stage is admitted.

## Finding 6: UNWAIVED evidence-copy limitation

6. `scripts/scanner_recovery/environment.py:520`: archive_scratch copies the contents of any regular file in scratch, including hardlinks to inodes outside scratch, into the durable evidence tree. Only symlinks are limited to metadata.

   Failure scenario: A test, or an escaped setsid/tmux descendant, creates a same-filesystem hardlink under t/ to a file the same UID owns elsewhere on /tmp's filesystem. The archive copies those bytes into controller evidence. st_nlink > 1 is not recorded or refused. That matters for the 'no live/secret exposure' posture in a private evidence tree.

Regular-file hardlinks are not distinguished from private independent inodes. The PoC must not be used to archive untrusted same-UID scratch or claim absence of external bytes before this is resolved.

## Finding 7: deferred noncritical

7. `scripts/scanner_recovery/runner.py:184`: finish_scratch does an extra os.stat for every entry that directory_entries has already lstat'ed for directories, and the runtime walk duplicates the traversal archive_scratch performs right afterwards. That is redundant I/O, and the second pass can see a different snapshot than the first.

   Failure scenario: With a large runtime tree (up to 65536 entries) the work is doubled. Because escaped writers are disclosed as uncontained, the runtime 'no socket/bus' verdict and the archived inventory can disagree about the same directory. Deriving the r/ socket and bus check from archive_scratch's inventory rows would use a single observation.

The duplicated runtime/archive walk is retained. Their observations are sequential, not an atomic snapshot, and global escaped-writer quiescence is not claimed. No admission proof may infer both describe one instant.
