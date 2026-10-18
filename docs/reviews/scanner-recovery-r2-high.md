**Verdict: REVISE.** This is the second HIGH review of `597a1d4..ae71b76`, read-only: no tests, probes, edits, queue, services or subagents. Most R1 fixes do hold up:
- The additive 57/14 contract check is exact.
- Every later stage now binds the diagnostic and the common driver/support/supplement/disposition/failure-map chain.
- H92 checks clean porcelain, hostread-only nodes and exact adjacency.
- Input files are parsed and hashed from the same bytes.
- Plan mode validates before any attempt directory is created.

The findings below are unverified, ranked most severe first. No `CLAUDE.md` rule applies to these files (there is no repo-level one, and the home-level rules are Rust/workflow rules).

10 findings, most severe first:

1. `scripts/scanner_recovery/runner.py:167`: The runtime socket/bus check in finish_scratch only looks at top-level entries of the private XDG_RUNTIME_DIR, and `path.is_socket()` follows symlinks, so nested sockets go undetected and external symlink targets get stat'ed.

   Failure scenario: A test creates XDG_RUNTIME_DIR/heterodyne/ctl.sock or XDG_RUNTIME_DIR/dbus-x/bus. iterdir() only yields 'heterodyne', which is neither named 'bus' nor a socket, so the check passes and the stage can PASS. The ADR requires that no bus or runtime socket is exposed before or after. The archive only records that socket as 'excluded-special' and never holds. Separately, a top-level symlink r/x -> /run/user/UID/bus is followed by is_socket(), which inspects an external target and goes against the no-follow rule R1 #3 was fixed for.

2. `scripts/scanner_recovery/environment.py:326`: directory_entries opens a no-follow fd for every subdirectory of a directory and pushes them all onto `pending` before closing any, so open descriptors grow with directory width.

   Failure scenario: With an explicit --basetemp, pytest puts every tmp_path directory directly under p/. About 2020 test functions here take tmp_path. Under the common 1024 soft RLIMIT_NOFILE (systemd/service default; Python does not raise it), archiving p/ hits EMFILE (OSError). finish_scratch then HOLDs every B1/B2 3373 attempt with 'Scratch preservation failed', using up a one-shot attempt. This shell has a 1M limit, which hides the problem. Fix: process depth-first, opening each child only when it is popped, so at most tree-depth fds are open.

3. `scripts/scanner_recovery/environment.py:248`: source_candidates picks shell/config files by suffix only, so test-reachable systemd unit files and extensionless shell/Python scripts are never offered as candidates, although the helper doc says 'every shell/config line' is included.

   Failure scenario: deploy/store-helper/heterodyne-store-helper.socket and heterodyne-store-helper@.service (exercised by tests/test_deploy_store_helper.py), scripts/test-fast / test-full / test-changed (POSIX shell, no suffix) and spikes/s5/ws-request (python3 shebang, connects to the per-session socket) produce zero candidates. An inventory that covers the enumerated set exactly passes validate_inventory, while these endpoint/config sources were never shown to the controller.

4. `scripts/scanner_recovery/runner.py:897`: The ADR requires 'Inventory, actual measured path lengths and margin' as evidence and says unexpected longer real paths must HOLD, but the runner only records statically reviewed maxima and never measures actual socket paths created during the run.

   Failure scenario: A test binds a socket whose real path is longer than its reviewed derivation (say 95 bytes against a declared 89, or above 100 through an unlisted route). Nothing compares observed socket path lengths with the inventory. The archive lists surviving sockets as 'excluded-special' with no length or margin check, so the result PASSes without the required measured-length/margin evidence.

5. `scripts/scanner_recovery/environment.py:278`: validate_inventory lets a 'non-endpoint' row with just a reason string cover any candidate, including real tmux invocations and .sock/bind lines, so the ADR rule that every real tmux invocation must bind -S/-L or the audited guard is not enforced by the code.

   Failure scenario: One derivation {kind:'non-endpoint', references:[all candidates], reason:'reviewed'} covers `TMUX_TMPDIR=/tmp tmux -L s9-10` and `sock.bind(...)` lines and is accepted. The plan-mode test fixture does exactly this for a '.sock' line. Unbound default-server tmux use or an unbounded socket path then reaches execution without a HOLD. Candidates that contain tmux or socket tokens should require a tmux or socket row.

6. `scripts/scanner_recovery/environment.py:280`: Allowed socket bases are only p/t/r/root, but HOME (layout 'h', the same 18-byte private child) is where the product's default state directory, and therefore admind's sockets, lives.

   Failure scenario: config/paths.py:35 resolves state to $HOME/.local/state/heterodyne whenever XDG_STATE_HOME is unset, which it always is in the 14-key environment. Admind ctl/ask/wn-agent sockets reached through that default cannot be written as a socket derivation ('Unbound socket base'). The controller then has to mislabel them as non-endpoint or HOLD with no valid way through.

7. `scripts/scanner_recovery/runner.py:342`: run() kills the child's process group only on timeout. After a normal exit, surviving session descendants (daemons or servers spawned by tests) keep running while the runtime check and scratch archive read the scratch.

   Failure scenario: A test leaves a background helper that keeps writing under p/ or holds a socket in r/. archive_scratch then fails with 'Scratch changed during archive' or FileNotFoundError, giving a spurious HOLD. Or the socket appears or disappears between the runtime check and the archive, so the evidence depends on timing. Fix: killpg the session (or verify it is empty) after every child, before finish_scratch.

8. `scripts/scanner_recovery/environment.py:306`: socket_path_budget_inventory.inventory_sha256 is a digest of the re-serialized canonical JSON, while the plan and the root release bind inventory_sha256 as the SHA of the raw file bytes, so one result carries two different 'inventory_sha256' values for the same inventory.

   Failure scenario: A controller or auditor checks result.socket_path_budget_inventory.inventory_sha256 against the released inventory_sha256 and finds a mismatch for an unchanged inventory. That either triggers a false lineage HOLD or teaches reviewers to ignore the field. This weakens the 'same bytes parse+hash' claim. Fix: pass and record the raw-bytes hash.

9. `scripts/scanner_recovery/environment.py:57`: If create_layout fails after mkdtemp (root length over 16, a child mkdir failure, or a child path over 18), the scratch root is left in /tmp but layout stays None, so the root path is never recorded and finish_scratch never archives it.

   Failure scenario: Any raise inside create_layout after mkdtemp leaves the result with private_runtime_layout=None and no trace of /tmp/hzXXXX. That conflicts with the ADR rule that the attempt records its literal private paths and keeps its directories for disposition. The orphaned directory then cannot be tied to the HOLD attempt.

10. `scripts/scanner_recovery/runner.py:242`: The fix duplicates logic instead of reusing it. The original contract checks run in both validate_supplement and pinned_inputs:247-254, with SUPPLEMENT read and parsed twice. The H92 adjacency check re-implements the adjacency part of collection_check. The whole-tree source_candidates walk runs in plan validation and again in execute.

    Failure scenario: Each check has two copies to keep in sync, and the two SUPPLEMENT reads mean the one validated in pinned_inputs is not the bytes later hashed into chain_identity. Every execute reads every .py, .sh and config file in the worktree twice. Fix: validate the single supplement_data parse once, factor adjacency into one helper used by both collection checks, and reuse the plan-time candidate map (re-verifying only its digest) at execute time.
