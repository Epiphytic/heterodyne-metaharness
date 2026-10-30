**Verdict: changes requested. PR145 does not pass round two at HIGH.** The R1 repairs look sound in the source. But the cumulative source has a new high-severity custody gap in `stopped_snapshot`, and its destination side has a second one. The dormant owning factory stays on HOLD, and none of these findings changes that.

The built-in `code-review` skill ran at high effort over the cumulative range `76c07c0...6ac40e3` (HEAD confirmed as `6ac40e3`). I read the R1 report under `docs/reviews/2026-10-30-deployment-producer-cessation-code/r1/` and checked the top findings against the actual source. I made no edits or commits, touched no services, and did nothing in the queue. I did not rerun the 130 tests, so that PASS rests on your committed logs.

## R1 repairs, checked in source
- **R1 #1 (nlink):** fixed. The ENOENT path in `_empty_group` (`deployment_cessation.py:205-212`) now compares the retained descriptor's readlink against the original cgroup path plus ` (deleted)`. It no longer relies on `st_nlink`.
- **R1 #2 (failed attempts):** fixed. Snapshots are built in a `TemporaryDirectory` attempt directory and only linked into place after the second `observe()` succeeds (`:162-183`).
- **R1 #3 and #4 (raw errors):** fixed. `sqlite3.Error` and the custody loop are now inside the try that converts errors to `InvalidAction` (`:187`).
- **R1 #6 (clean exit):** fixed, but it went one step too far (finding 4 below).
- **R1 #7 (tar errors):** fixed. `_verify` now catches `store_core._TAR_ERRORS`.
- **R1 #5 (manager socket peer and inode checks):** the skill reported nothing against the new checks. I did not trace them myself.

## Findings to fix
1. **High: `src/heterodyne/actions/deployment_cessation.py:152-164`.** Custody is checked only for `live.db` and its ancestors, and the service UID is allowed to own them. `wal_snapshot` (`wsd/backup_copy.py:50`) then opens the DB as root with `?mode=ro`. In WAL mode SQLite still opens `live.db-shm` and `live.db-wal`, and it opens them without NOFOLLOW. As root it also fchowns and can truncate the `-shm` file.
   - The root-only gate is `:247`.
   - Scenario: the service user plants `live.db-shm` as a symlink or hardlink to a root-owned file. Root then chowns or truncates that file, and service-controlled WAL content flows into the "stopped snapshot".
   - Fix: require that both sidecars are absent, or are regular files with the expected owner, before and after the snapshot. Alternatively, run the read with dropped privileges.
2. **Medium: `deployment_cessation.py:162-174`.** `destination.parent` gets no custody check (owner, mode or symlinked ancestors), yet root creates `.stopped-*` there and runs `os.link` into it. The source path is checked. The destination side should be checked the same way to back the "fresh protected recipe output" claim.
3. **Medium (plausible, not traced): `deployment_outage_authority.py:36`.** Switching to `_approval_body` drops the inner-card `_check_card` validation from `_prepare`, while `_bindings` still requires the full check. A card that `_prepare` accepts and posts could then be refused by `_bindings` after the operator approves it.
4. **Low: `deployment_cessation.py:329`.** `code in {1, 2, 3}` treats `CLD_DUMPED` (3, a core dump) as clean whenever Result is success. Use `{1, 2}`.
5. **Low: `deployment_publication.py:80`.** The TLS handshake runs inside `connect()` before `response.bind()`, so the watchdog has no socket to shut down during the handshake. A peer trickling handshake bytes can block indefinitely, held only by the 10s per-operation socket timeout.
6. **Low: `deployment_publication.py:97,125`.** `_verify` compares the Git member against `encode_record(packet)`, not against `original`. `codec._decode` (`codec.py:106`) accepts non-canonical JSON, so `_PreparedCard.operation_original_sha256` can differ from `published_original_sha256`. That matches "same packet" semantically. Either state in the docstring that the binding is to canonical bytes, or require `original == canonical`.

## Cleanup, not blocking
- **`deployment_cessation.py:300`:** each snapshot runs `_show()` 4 times, which means 24 full binary hashes and 16 socket connects. This widens the window between observations.
- **`deployment_publication.py:40`:** `published_original` and `packet_sha256` are never read, and the packet is decoded and encoded twice.
- **`deployment_cessation.py:70`:** `_boot()` duplicates `heterodyne.platform.boot_id`.
- **`deployment_cessation.py:187`:** several redundant patterns (`TimeoutError` is already an `OSError`; the repeated `isinstance(InvalidAction)` checks; `is_symlink()` after `stat(follow_symlinks=False)`).

Findings 1, 4 and 6 are confirmed against the source. 2 and 5 are read from the code but not exercised. 3 is plausible and untraced.

All of this is one-unit cessation only. None of it is a claim about all writers, and none of it needs live deployment proof.
