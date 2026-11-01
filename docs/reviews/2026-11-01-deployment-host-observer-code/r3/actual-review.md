**Verdict: it's good enough to build as a private, read-only PoC.** The review at frozen HEAD `73a0da3` (medium, PR 158) found nothing that makes the PoC unsafe or pointless within the stated scope (no effects, no permit or authority constructor, and the guard always reports unchecked). It found 9 issues. Two of them limit how far you can trust an observation and should be fixed before any later authority work.

**Issues that limit trust (not blocking for the PoC):**

1. **The release is identified only by the `HETERODYNE_RELEASE` env label** (`deployment_host_observer.py:42`, `:213-241`, `:366`). I checked this one: no code compares the unit's `ExecStart`, `WorkingDirectory`, `/proc/<pid>/exe` or cwd against the tree being hashed. If the unit runs `/srv/app/current/...`, or a drop-in points it at another release while the env label still says A, the observer hashes `releases/A` and reports A as running. It's the most important gap: the "bind to actual running release" fix ties the label to the process, not the code it runs. It isn't critical for a PoC because the unit config sits in the same service-UID trust domain you already accept. It does mean `installed_tree_sha256` shouldn't be read as proof of what code is executing.
2. **The private manager endpoint is trusted on peer UID alone** (`:143`). Any process running as the service UID can bind a fake `systemd/private` that answers `inactive`/`MainPID=0`, which skips `_running_release`. This is the same class of weakness R2 raised for the session bus. You've already disclosed it as the inherited private-endpoint UID contract. The suggested fix is to check that the peer PID equals the `MainPID` of `user@<uid>.service` as reported by the system manager.
3. **The live DB and WAL are copied while the unit may be running** (`:327`). Concurrent writes are detected only by size/mtime/ctime, so an in-place WAL overwrite in the same timestamp tick can produce a mixed snapshot that still passes `integrity_check`. This falls under your disclosed "not global-atomic / all-writer exclusion" limit.

**Robustness (minor):**

4. **A timeout leaves a stray `systemctl` process** (`:161`). On timeout only `runuser` is killed; its forked PAM child keeps running. Using `start_new_session` plus `killpg` would fix it.

**Efficiency and cleanup (not correctness bugs):**

5. **A spurious HOLD is possible on large venvs:**
   - Each observation walks the tree four times and hashes it fully twice (`:374`).
   - `_entry_owner` reopens the directory chain from the root for every entry (`:250`).
6. **Binary pinning:**
   - `runuser`, `env` and `systemctl` are each hashed four times per observation (`:151`).
   - The pinned file descriptors are never used to exec, so they don't actually anchor anything. You already disclose this ("no fexecve").
7. **`_show`/`_parse_unit` duplicate `deployment_cessation._AdmindInvocation._show`** (`:136`), and the two copies have already drifted once. Any fix to manager authentication (item 2) would have to be made in both places.
8. **The `refused` branch of `_endpoint_state` is dead code** (`:125`). Since R2, `_show` rejects a stale endpoint, so this path never gives a usable result. It suggests a fallback that the fix forbids.

**Bottom line:** go ahead with the PoC as scoped. Before anything uses these observations as authority, fix item 1 (check the executable/cwd against the hashed tree) and item 2 (check the peer PID), and share one manager-read helper so the fix lands in one place. Nothing was edited, posted, committed or probed, and I didn't run any extra validation review. Apart from item 1, which I checked against the source, these findings come straight from the review and I didn't re-check them.
