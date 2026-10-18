I found no critical flaws in the reviewed range. The bounded, undeployed helper looks good enough to implement as a PoC. The latest commit replaces `result.get(...) or {}` at `runner.py:199`, which fixes the R4 crash when the socket inventory is absent (prefilled `None`). Every failure path I traced in `finish_scratch` and `main`'s `finally` holds the result and leaves scratch in place instead of crashing. The process-group check also fails closed.

Nothing here gives the actual scanner stages any authority. They stay on HOLD until the complete inventory exists, as the review target says. The findings below are non-critical. Most are completeness or robustness gaps that the change already lists as unwaived or unproven limits, so none of them blocks the PoC.

8 findings, most severe first:

1. `scripts/scanner_recovery/environment.py:282`: source_candidates skips every file whose suffix is not in the fixed list (.js/.ts/.rs/.go/.c/.pyi/.json, tmux .conf variants, etc.), so endpoint or tmux use in those files never becomes a candidate and validate_inventory can admit an incomplete inventory.

   Failure scenario: A test helper in a .js or .pyi file, or a JSON fixture, builds a long AF_UNIX path or starts a default tmux server. No candidate is produced, so the inventory passes coverage and the unbounded socket is only discovered (if at all) after execution. This is one of the acknowledged 'complete inventory UNPROVEN' limits; it blocks stage authority, not the helper PoC.

2. `scripts/scanner_recovery/environment.py:383`: A tmux derivation's guard_references only has to name some candidate line. Nothing checks that the referenced line is a tmux guard or carries -S/-L, so a claimed isolation is self-attested.

   Failure scenario: A tmux row with isolation='fixture-owned-S' and guard_references pointing at an unrelated socket line passes validate_inventory, so a default-server tmux call is admitted as isolated.

3. `scripts/scanner_recovery/environment.py:363`: A socket derivation's base (h/p/t/r/root) and worst_case_component/suffix are not tied to the bytes of the referenced source line. The bound is reviewer-asserted, and only surviving sockets are cross-checked later.

   Failure scenario: A row declares base 'root' with a tiny component for a socket the source actually creates under p/<32-byte tmp_path case>/nested. The plan and preflight pass, and a transient over-budget socket that is unlinked before archive is never observed.

4. `scripts/scanner_recovery/runner.py:372`: group_has_writers handles only OSError. A vanished or racing /proc/PID/stat read that returns short or empty content raises IndexError/ValueError from rsplit/split/int, and that escapes run() as an unclassified error.

   Failure scenario: A group member exits between open and read and an empty read comes back. IndexError propagates out of run() and the whole attempt HOLDs as 'IndexError' instead of retrying the scan. This fails closed but is noisy.

5. `scripts/scanner_recovery/environment.py:559`: The archive mirrors the scratch root directly into destination/, and then writes destination/inventory.json in the same namespace with 'xb'.

   Failure scenario: Any process that writes a file named inventory.json at the scratch root gets it copied first. The inventory write then raises FileExistsError, so scratch preservation HOLDs with a partial archive and no inventory hash.

6. `scripts/scanner_recovery/runner.py:558`: The ADR says H92 runs from a disposable detached copy of e22f4ed. The runner instead requires the live worktree HEAD to equal e22f4ed and runs with active = w.

   Failure scenario: An H92 stage would run pytest inside the real linked worktree, not a disposable copy, which differs from approved ADR step 2. This matters for stage authority, which is on HOLD; it does not affect the helper PoC.

7. `scripts/scanner_recovery/environment.py:465`: directory_entries reads and sorts the full os.listdir of each directory before the 65536-entry cap is applied, so memory and time are not actually bounded by the cap.

   Failure scenario: A test that leaves a scratch directory with millions of entries makes the archive or runtime walk materialize the whole listing before it raises 'exceeds entry limit'.

8. `scripts/scanner_recovery/environment.py:505`: archive_scratch keeps its own 65536 entry counter on top of the total counter inside directory_entries. That is duplicated limit logic with slightly different semantics (all entries vs. the walk's per-name count).

   Failure scenario: The two limits can drift if one is changed. Maintenance cost only; the simpler form is to rely on the single generator-side cap.
