I ran the built-in `code-review` skill with `high 158`, in a forked context, as Claude Opus 5.5 (`claude-opus-5-5`). The checkout is at `cadc8265f05682752681b24966a4fd7bb9b8770e`. I made no edits, comments, commits, deployments, live probes or queue operations, and I did not re-run the tests. I checked findings 1–3 and the `_entry_owner` cost in finding 5 against the source myself; the others are the skill's own analysis.

## Findings, most severe first

1. **When the private manager socket refuses, unit facts come from the session bus with too little authentication** (`src/heterodyne/actions/deployment_host_observer.py:121`).
   - `_endpoint_state` accepts a refused `systemd/private` as `'refused'`, and `systemctl` then falls back to `/run/user/<uid>/bus`. That only proves the bus is owned by the service UID, not that the reply comes from systemd.
   - Any process running as the service UID, including the admind being observed, could claim `org.freedesktop.systemd1` if the manager hasn't, and report `inactive`, `MainPID=0`. That skips the `_running_release` binding entirely.
   - The reused cessation `_show` requires the private endpoint to connect, so the two copies disagree.
   - This matters on the real host: your note says the live private socket is genuinely `ECONNREFUSED`, so the host runs this fallback path today. The PR's tests only show a stale-private / live-bus pair being tolerated; nothing shows who answers on the bus. I rate this plausible, not proven exploitable.

2. **For an inactive unit, `EnvironmentFile=` and the manager environment are never queried** (line 356).
   - `_PROPERTIES` (line 43) has only `Environment`. A drop-in `EnvironmentFile=` setting `HETERODYNE_RELEASE=B` would win over `Environment=…A`.
   - The observer would then record release A and A's tree digest, while the next start runs B. The process-environ check only covers the active case.

3. **A `MainPID` that overflows escapes the error contract** (line 211).
   - The regex accepts up to 20 digits. A value outside the `pid_t` range makes `os.pidfd_open` raise `OverflowError`.
   - `OverflowError` is an `ArithmeticError` and isn't in the except tuple at line 385, so callers get a raw exception instead of `deployment-observer-unavailable`. Easiest to trigger through finding 1.

4. **No test covers the WAL path** (`tests/test_deployment_host_observer.py:174`). The only end-to-end `_observe` test uses a rollback-journal DB, but the production store uses WAL. Copying `-wal`, opening it read-only, and the `-wal` digest part never run in tests.

5. **Tree hashing cost grows with entries × depth, times four** (line 247). `_entry_owner` reopens the whole directory chain from the root for every entry. That happens in both walks inside `_tree_digest`, which itself runs twice. A large valid venv could use up the 30s deadline and HOLD.

6. **The second `_tree_digest` re-reads all content** (line 365) just to detect change, and the pinned binaries are hashed 12 times per observation. Comparing the first pass's metadata keys would detect the same change at metadata-only cost.

7. **The binary fds are kept open but never used for exec**, and the path/pin tuples are written out twice in `_show` (line 137). Holding the fds suggests they anchor what runs, but argv still executes by path. This matches the limitation you already disclosed (no `fexecve`); the duplicated tuples can drift on edit.

8. **`_show` and `_parse_unit` are near-copies of the cessation `_show`** (line 132), and the copies already differ on stale-socket handling (see 1). Fixes to manager reads now have to land in two places.

9. **Dead and redundant checks in the refused branch** (line 124). `exc.errno != ECONNREFUSED` can never be true inside `except ConnectionRefusedError`. The `S_ISSOCK`/uid recheck repeats `_manager_endpoint`, so `deployment-observer-private-endpoint` is only reachable through a race, and the existing negative test passes on the earlier check, not this one.

10. **`_profile` doesn't validate `target_id`** (line 77). A malformed value is only rejected by `decode_observation` after the whole observation (manager queries, tree hashing, DB copy) has run, and with the wrong error code.

## Not reported
- Clone and symbol-collision flags with no correctness impact: finding 8 is the skill's own duplication finding, not the ripwire clone flags, and nothing was restructured to satisfy those.
- The disclosed limits already out of scope: not globally atomic, not the external runtime closure, the guard always unchecked, and the HOLD posture.

The skill reported no defects in the codec (`deployment_observation.py`). Findings 1 and 2 are the ones I'd treat as blocking for R2: they are where the recorded release or unit state can differ from what systemd will actually do.
