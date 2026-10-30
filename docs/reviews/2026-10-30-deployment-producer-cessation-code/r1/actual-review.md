**Verdict: changes requested.** PR145 should not pass round one at HIGH. The public executor stays on HOLD, as the qualification doc already says.

I checked HEAD first: it is `c77dd91a84656f01d265d09344a2a21902e8f682`. All 15 files you listed match your SHA-256 hashes exactly.

The built-in `code-review` skill ran at high effort over `76c07c0..c77dd91` and found 10 candidate issues. It ran no separate verify pass, so treat the findings below as plausible rather than confirmed. It also couldn't run the tests: pytest fails loading `tests/conftest.py` because `heterodyne` isn't installed in that interpreter. So your 116-test PASS (3.32s) was not reproduced; it rests on the committed log, whose hash matches. The skill didn't read the qualification doc or the PR143 history. Nothing here contradicts or reopens the PR143 findings.

## Correctness findings to fix

1. **High: `src/heterodyne/actions/deployment_cessation.py:171`.** `_empty_group` treats `st_nlink == 0` on the retained cgroup descriptor as proof the cgroup was removed. On real cgroupfs, a directory's link count is recalculated as 2 plus its subdirectories, so it probably never reads 0 even after removal.
   - After a normal stop, systemd removes the unit's cgroup. Opening `cgroup.events` in the removed directory then fails with ENOENT, which becomes `cessation-observation-unavailable`.
   - The result is that `observe()` and `stopped_snapshot()` would probably always refuse on a real host. The tests use an ordinary `tmp_path` directory, so they can't catch this, and the missing-events test asserts the refusal.
   - I read the code and the logic is as described. I haven't confirmed the kernel's link-count behaviour on this host.
   - This is the most important gap between the source tests and real process evidence.

2. **`deployment_cessation.py:140`.** If `stopped_snapshot` refuses after the copy (`cessation-live-database-changed`), or the snapshot fails partway, the destination file is never removed. A complete copy that passed its integrity check is left in the protected recipe output, where it looks like a valid stopped snapshot. Any retry to the same destination then fails permanently with `snapshot-unavailable`.

3. **`deployment_cessation.py:146` and `:128`.** `sqlite3.Error` isn't converted to `InvalidAction`, so a locked or corrupt live database raises a raw sqlite3 exception. The custody stat loop (line 128) runs before the `try`, so a missing `live.db` raises a raw `FileNotFoundError`.

4. **`deployment_cessation.py:90`.** `__init__` calls `_show()`, `_boot()` and `_capture_group()` without the exception handling that `observe()` has. A missing service user, a systemctl timeout, a refused bus connection or non-ASCII output escapes as a raw exception. A caller that catches only `InvalidAction` would crash instead of holding.

5. **`deployment_cessation.py:219`.** The peer-UID check authenticates `/run/user/UID/bus`. But `systemctl --user` with `XDG_RUNTIME_DIR` set connects first to `$XDG_RUNTIME_DIR/systemd/private`. So the checked socket isn't the one that supplies `ActiveState`, `MainPID` or `InvocationID`, and the "kernel-UID-checked manager bus" claim doesn't hold for the observed properties.

6. **`deployment_cessation.py:287`.** The outcome is `clean` only for `ExecMainCode == 1` with status 0. A normal `systemctl stop` of a process that doesn't trap SIGTERM (`CLD_KILLED`, status 15, Result=success) is labelled `forced`. Exits listed in `SuccessExitStatus` are mislabelled the same way.

7. **`src/heterodyne/actions/deployment_publication.py:128`.** `_verify`'s except clause leaves out `tarfile.TarError` and `EOFError`. An archive with truncated member data escapes as a raw exception instead of `publication-readback-unavailable`. `store_core` already groups these as `_TAR_ERRORS`.

## Cleanup and efficiency

8. **`deployment_card.py:52`.** `_prepare` repeats the card checks that `_check_card` already ran, including a second admind profile read. The two copies can drift, and they can validate against two different profile snapshots. Having `_check_card` return its row and parts would remove the duplicate.

9. **`deployment_publication.py:42`.** `_retain` reimplements the bounded copy in `store_core._copy`. It also fsyncs an anonymous temporary file that is never kept. That file's location comes from the ambient `TMPDIR`, which contradicts the module docstring's no-ambient claim.

10. **`deployment_publication.py:107`.** The archive members are walked twice and the packet is decoded twice. The live HTTPS fetch also happens before the cheap local card and size checks, so a card that is too wide still triggers a network fetch. Separately, each cessation `observe()` runs `_show()` twice.

## Scope

- **Stays unqualified:** the object-view opener and custody, DNS and connect deadlines, exclusion of all other writers, the native route and the root helper. None of the findings changes the HOLD.
- **Not credited:** no live authority or readiness booleans. The source tests remain source-only evidence, not proof of live custody.
- **Not done:** I made no edits, commits, service calls, live SQL, queue actions or GitHub comments.

Fix items 1–7 before round two. For item 1, round two needs test evidence from a real cgroup.
