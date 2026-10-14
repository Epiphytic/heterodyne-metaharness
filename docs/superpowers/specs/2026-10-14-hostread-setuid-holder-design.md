# hostread: a holder that changes uid after a setuid exec (btq-9yiku)

The source is the FINDING-PROD in the btq-19fh0 design review r2 (`/tmp/review-19fh0-r2.md`, rated High). The saved-uid analysis is in r1 (`/tmp/review-19fh0-r1.md`). That finding was noted as out of scope in `docs/superpowers/specs/2026-10-14-hostread-uid-first-design.md` (19fh0, pending Liam's ask 4gt8).

Reviews: `docs/reviews/9yiku-design-r1.md` (REVISE) and `docs/reviews/9yiku-design-r2.md` (REVISE).

Line numbers are at origin/main e65ade7.

**Recommendation (r3): (a), plus a subordinate-uid check in `_ours`. The check reads the allocation file, and its limits are stated as a PoC residual.**
- The host read's app-server is started under `PR_SET_NO_NEW_PRIVS`, through an exec trampoline.
- The trampoline first checks the launch conditions below and fails closed if they don't hold.
- `_ours` also counts this user's subordinate uids (`/etc/subuid`) as this user's. r1 found that a descendant can reach them even under no_new_privs; I reproduced that on this host (below).
- r2 showed that the file's current entries don't bound the kernel's existing mappings. Checking the kernel's mapping instead (`/proc/<pid>/uid_map`) would be sound. On this host it costs about one read in 25, and it leaves credential copies behind, so it is rejected ("Why not the kernel's mapping"). The gap is root's edits to the allocation file, which r3 states as residual 4, and the invariant is qualified to match.
- Rule 5's order is unchanged.
- (b) is rejected because of its cost, and so (c) is rejected too.

## The gap

`_judge` (`src/heterodyne/sandbox/hostread.py:222`) clears a live process whose environ read is refused, if `_ruled_out` (`:186`) holds (rule 5, `:261`):
- `_ours` (`:177`) finds neither its real nor its effective uid to be this user's, **or**
- it started before the earliest home.

The first clause relies on a premise: *another user's process can't be a holder.* Holding means having `CODEX_HOME=<home>` in its environment. Today nothing makes that true. The scenario from r2:
1. An app-server descendant inherits the scratch `CODEX_HOME` and calls `setsid()`, which takes it out of the client's group kill.
2. It execs a setuid binary owned by user B that keeps the environment. The exec alone leaves real uid A; the binary then calls `setresuid(B,B,B)`.
3. Its environ read is now refused, `PF_EXITING` is clear and `_ours` is false, so rule 5 clears it.
4. The read's cleanup (`:411`) or `recover` (`:340`) removes the home while that process lives. The home was the only recovery marker.

Preconditions: a setuid executable on a suid mount, and no inherited `no_new_privs`. The trusted reader runs outside every sandbox (ADR 0001 D9, plan 4b E4). The shared client's `Popen` (`src/heterodyne/sandbox/resources/codex_usage.py:87`) sets no privilege barrier.

## Launch conditions (r2)

The guarantee below holds only if the app-server starts with all of these:
1. **Uid A, ordinary.** The reader's real, effective and saved uids are this user's (wsd's), and none is 0.
2. **No capabilities.** `CapPrm`, `CapEff` and `CapAmb` are all zero in `/proc/self/status`.
   - no_new_privs stops exec from *granting* privilege. It doesn't remove what is already held: an app-server that inherited ambient `CAP_SETUID` could `setresuid(B,B,B)` despite it.
   - With no capability at all, no mapping of the current user namespace can be used to take another uid: changing uid inside a namespace needs `CAP_SETUID` there.
   - `CapInh` is ignored. Under no_new_privs, inheritable capabilities can only be raised through file capabilities, and those are suppressed.
3. **no_new_privs set, and confirmed** with `PR_GET_NO_NEW_PRIVS` after the set.

Rather than leave these as assumptions, the trampoline checks them and exits 126 before the exec if any fails. wsd today runs as an ordinary user service with all three capability sets zero (checked on this host: `CapPrm`, `CapEff` and `CapAmb` are 0, uid 1001). So the check costs nothing in operation, and it turns the premise into a check.

## The candidates

### (a) no_new_privs for the host read's app-server, and launch conditions checked

Under the launch conditions:
- **Exec can't add privilege.** no_new_privs is inherited across fork, clone and exec, and can't be unset. After it is set, `execve` grants nothing: setuid and setgid bits and file capabilities are ignored ([kernel documentation](https://docs.kernel.org/userspace-api/no_new_privs.html)).
- **Only A's own uids remain.** The only uid changes left without privilege are among a task's own real, effective and saved uids, and all three are A.
- **So the reported scenario is closed.** Step 2's setuid exec runs as A and gains nothing.

This repo already uses the same argument for the sandbox (`src/heterodyne/sandbox/channel.py:18,216`: a task under no_new_privs can't gain privilege; "under no no_new_privs, it can still gain them", `tests/test_sandbox_channel.py:636`).

**What no_new_privs does and doesn't stop (r2):**
- **Direct `sudo`, `su` and `pkexec` are stopped.** They are setuid, so under no_new_privs they run without elevation and fail, even with `NOPASSWD`.
- **Already-privileged services reached over IPC are not stopped.** Examples are a root daemon's socket, a system service manager, or a container engine's root socket. They can start processes as anyone, but they can't change the descendant's own credentials, because there is no such syscall. Whatever they start is not a descendant: see "Residuals".
- **SCM_RIGHTS passes descriptors, not uid-changing capabilities by itself.** A descendant can be sent, or can open, a user-namespace file. That case is the next section's.
- **ptrace stays subject to its own permission checks.** A descendant can't attach to a more privileged process.

**The subordinate-uid path (r1 BLOCKING 1), reproduced.** no_new_privs doesn't stop `setns` into a user namespace that this user owns. Inside it, the joiner has every capability, so it can take any uid the namespace maps, including this user's subordinate uids. The namespace can come from:
- this user's own rootless containers (podman `--userns=keep-id` maps the rest of the namespace to `/etc/subuid`'s range);
- a helper outside the tree that runs `newuidmap` for a namespace the descendant made. `newuidmap` itself is setuid, so the descendant can't run it under no_new_privs.

On this host, 2026-10-14 (no root, no podman):
- openclaw has `/etc/subuid` and `/etc/subgid` entries `openclaw:165536:65536`.
- `/usr/bin/newuidmap` and `/usr/bin/newgidmap` are setuid root (mode 4755).
- `/etc/nsswitch.conf` has no `subid:` line, so `/etc/subuid` is the only source.
- The reproduction:
  1. `unshare --user --map-auto --map-root-user sleep`, outside no_new_privs, made a namespace mapping `0→1001` and `1→165536 (65536)`.
  2. A Python process started under `setpriv --no-new-privs` opened `/proc/<that pid>/ns/user`, then called `os.setns(fd, CLONE_NEWUSER)` and `setresuid(1000,1000,1000)`.
  3. From the host it showed `Uid: 166535` on all four fields, with `NoNewPrivs: 1`.
  4. Its environ read was refused: it joined after its last exec, so its memory map's namespace is the initial one.
  5. Rule 5 would clear it.

**The check that closes it, while the allocation is unchanged.** `_ours` also counts a real or effective uid in this user's subordinate ranges:
- **Where the ranges come from.** `/etc/subuid` lines whose first field is this user's login name or numeric uid, as `newuidmap` reads them. They are read once per scan.
  - The login name is `pwd.getpwuid(os.getuid())`'s. If that lookup fails, every uid counts as ours.
  - Every matching line is taken, so a user with several ranges gets all of them.
  - A line `name:start:count` covers `start` through `start + count - 1`, both ends included.
  - A line whose start or count isn't a decimal integer, whose count is 0 or less, or which doesn't have exactly three fields, makes the file malformed. That applies to other users' lines too, since `newuidmap` would also be reading a damaged file.
- **Missing file.** No ranges. With no subordinate uids, `newuidmap` maps nothing for this user, so no namespace this user maps through it holds any.
- **Unreadable or malformed file, an unreadable `/etc/nsswitch.conf`, or an nsswitch `subid:` source** (which this design doesn't parse). Every uid counts as ours. That fails closed with (b)'s cost below, on such hosts only.
- **What it doesn't bound (r2 BLOCKING).** The kernel keeps a namespace's mapping after `/etc/subuid` changes. So the check reads an allocation, not a mapping, and it is wrong when the two differ:
  - a range removed from this user's lines, or the file deleted, while a namespace mapped from it still lives;
  - a range added between the scan's read of the file and a helper's `newuidmap` call.
  
  Both need root to edit `/etc/subuid`. Residual 4 states them, and the premise in "Changes" is qualified to match.

**Why not the kernel's mapping (r2 option 1).** I worked this through and measured it, then rejected it on cost.
- **The rule.** A foreign-uid process would be ruled out only if `/proc/<pid>/uid_map` reads exactly `0 0 4294967295`, which is the initial namespace's identity map. Anything else, even one other line, means a non-initial namespace, and the process would be `_Unknown`. That subsumes the subuid check: the escape above needs a non-initial namespace, whatever the allocation file says.
- **It would be sound.** The reads would go status (foreign uid), then `uid_map`, then signal 0 through the pidfd that was opened before the reads.
  - **Exits.** `ENOENT` or `ESRCH` at any of the three reads means gone, as now.
  - **An unreadable map** (`EACCES`, for example under `hidepid`) would fail closed, as `_Unknown`.
  - **The two reads are of one namespace.** A thread group shares one user namespace: `setns` and `unshare` of `CLONE_NEWUSER` need a single-threaded caller, and `clone` refuses `CLONE_NEWUSER` with `CLONE_THREAD`. An exec keeps the namespace. So, unlike most of status, the namespace is a group fact, like the uid and the start time. That holds even when an exec moves a worker into the leader's PID between the reads.
  - **It is monotone.** A task can join or create only namespaces below its own, never return to an ancestor. So an identity map at the later read shows the group was in the initial namespace at the status read, and there, under the launch conditions, it couldn't have taken the foreign uid.
  - **A process that joins after we read it.** If it joined before the status read, the later `uid_map` read shows a non-identity map, so it is kept. If it joins after the status read, the status showed a uid it had while still in the initial namespace, which is either ours (kept) or one it couldn't have reached (so it is no holder). Switching to a uid mapped by the joined namespace comes after the join. Rule 5's existing order handles that, and no re-read is needed.
  - **A reader outside the initial namespace** sees no identity maps at all, so every foreign process would be kept. That fails closed.
- **What it would cost, measured on this host on 2026-10-14.**
  - 670 to 678 live foreign processes had refused environs. All their `uid_map`s were readable, and 48 to 50 were non-identity. They come from chrome's sandbox under uid 1000, from containers whose uids are mapped by root (1065536 to 1066536, outside every `/etc/subuid` range here), and from system services with a namespace of their own (uids 0, 116 and 126).
  - The start-time clause would still clear those that started before the home. So the cost is the young ones.
  - **The read path.** Over 180 s, sampled every 0.5 s, a non-identity foreign process younger than 2 s existed in 8 of 346 samples; younger than 5 s in 14; younger than 10 s in 23. Chrome starts and ends them all the time. A read's cleanup window is its duration plus the 1 s slack, so about 4% of reads would raise `_Unknown`, be discarded and keep their home (`DIAG_SCRATCH_KEPT`).
  - **Recovery.** Each kept home holds a copy of the login, and recovery would remove it only at a start with no non-identity foreign process younger than the oldest home. At one instant, 5 had started within the last hour, mostly container processes. A container restarted after a home was kept would block recovery for as long as it runs.
  - (b) costs more (123 blockers in a day), but this cost is still not small, and it compounds: a discarded read is how the homes that block recovery are made.
- **The trade.** With the allocation check (r2 option 2), the gap is root's edits to a file that root owns, made while this user's namespaces from the old allocation live. That is the same trust class as residual 2, where root maps any uid. With the kernel's mapping (option 1), the gap is closed, but about one read in 25 would be lost, along with an on-disk credential copy that might never be recovered on a shared host. For the PoC I pick option 2. A narrower kernel-map rule is possible (for example, keeping only the non-identity processes whose uid isn't in another user's current range), but it brings back an allocation-file dependence of its own, so it isn't proposed.

**What the check costs.** A refused process in the subordinate range, started after the home, becomes `_Unknown`: the home is kept, and the read is discarded or recovery kept.
- Ordinary rootless-container processes don't reach that point. Each one's environ is readable, because this user owns its namespace and so has `CAP_SYS_PTRACE` there for the read's access check. That holds for both the task's namespace and its memory map's, since they exec'd inside it.
- Only a process that joined after its last exec, or one in a namespace nested under a subordinate owner, is refused.
- On this host now, 0 processes run in the range.
- The 19fh0 pins and the new rule-5 pin use foreign uids outside the range. Tests patch the ranges.

**How it is set.** Python has no `os.prctl`, and `preexec_fn` is unsafe in a threaded process such as wsd. So the host read launches through `codex_usage.py` itself, in a new mode:

```python
def exec_no_new_privs(argv, *, status=lambda: Path("/proc/self/status").read_text(), prctl=None,
                      execv=os.execv, exit=os._exit):
    """`python3 -I codex_usage.py --exec-no-new-privs <codex> app-server`: check the launch conditions,
    set and confirm no_new_privs, then exec argv. Exits 126 if any fails; never returns. The keywords
    are seams for the tests only."""
    try:
        if not unprivileged(status()):
            exit(126)
        prctl = prctl or ctypes.CDLL(None, use_errno=True).prctl
        if prctl(38, 1, 0, 0, 0) != 0 or prctl(39, 0, 0, 0, 0) != 1:   # PR_SET/GET_NO_NEW_PRIVS
            exit(126)
        execv(argv[0], argv)
    finally:
        exit(126)

def unprivileged(status):
    """The launch conditions, from one read of status: uids all this user's and not 0, and CapPrm,
    CapEff and CapAmb zero."""
```

- `read(codex, env, limit)` gains a keyword, `no_new_privs: bool = False`.
- When it is true, the argv is `[*barrier_argv(), codex, "app-server"]`, where `barrier_argv()` returns `[sys.executable, "-I", __file__, "--exec-no-new-privs"]`. The same `env`, pipes and `start_new_session=True` apply. `__file__` is set by hostread's `spec_from_file_location` load.
- The script's existing usage, `codex_usage.py <codex> [limit]`, is unchanged. The new mode is chosen only by its flag.
- hostread's call (`hostread.py:400`) passes `no_new_privs=True`. The in-sandbox read keeps the default; the sandbox's workload already runs under no_new_privs (channel.py).
- **What the exec keeps.** The PID, session, group and start time are unchanged, so the app-server is still the group leader `_kill` names, and it still starts after the home. r1 confirmed `PID == PGID == SID` and `NoNewPrivs: 1` after the exec.
- **No window before the barrier.** The trampoline starts nothing before the exec, so no descendant can be spawned before no_new_privs is set. It has `CODEX_HOME` from birth, so a crash before the exec leaves an ordinary holder the scan finds.
- **If a check or the exec fails,** the process exits 126 with no answer. The client sees `ClosedOutput`.
  - If `_end`'s SIGKILL lands before the exit, the status is `-9`, which is accepted, and `ClosedOutput` stands.
  - If the exit lands first, the status is 126 and `ExitStatus` replaces it.
  - Either way the read fails closed (`DIAG_READ_FAILED`), returns no reading, and its home is removed as for any failed read.
- **Alternative.** `setpriv --no-new-privs` (util-linux) is rejected: it is a host dependency, and it doesn't check capabilities.
- **Cost.** One interpreter start per read, about 20–40 ms against a 30 s limit.

### (b) rule 5 settles instead of clearing on the uid clause

This would remove the premise rather than enforce it: another user's refused process started after `since` settles. That is sound, but expensive.
- A settling process keeps the scan incomplete. Cleanup waits `RECOVER_SECONDS` (10 s) and keeps the home: `DIAG_SCRATCH_KEPT` on the read path, where the read is discarded, and `DIAG_RECOVERY_KEPT` on `recover`, where every home is kept.
- **Recovery is obstructed.** `since` is the crashed read's stamp, which can be hours or days before the restart. Every foreign refused process started since then and still alive settles, so recovery waits 10 s at every start and never succeeds while any of them lives.
  - A snapshot of this host on 2026-10-14 (uptime 27 days) found 673 live foreign processes with refused environs. Of them, 46 had started within the last 10 min, 103 within the last hour and 123 within the last day.
  - So a one-day crash gap would leave over a hundred blockers.
- **Reads would sometimes fail.** At the same instant, 3 of those processes had started within the last 10 s. A read cleaning up then, with a home that old, would have settled. This is one snapshot of surviving processes, not an arrival rate. It shows the read path would sometimes discard reads, without saying how often.
- **The hybrids don't save it:**
  - Filtering by start time is (b) itself.
  - Filtering by session misses a `setsid()` descendant, which is the reported scenario.
  - Filtering by cgroup needs enforced membership. `systemd-run --scope` alone doesn't stop a descendant from asking a delegated service to start a process elsewhere.
  - Filtering by uid fails because the attack leaves all four uids foreign, and r1 (19fh0) showed per-task uid reads can't be made complete.
- The subordinate-range check above is the one uid hybrid that holds. It is sound because the ranges a descendant can reach without privilege are exactly this user's, as long as root doesn't change them (residual 4).

### (c) both

(c) has (b)'s cost. With (a) and the subordinate check in place, (b) would only add protection against the residuals below, and it doesn't fully close them either. Rejected.

## Does no_new_privs break the pinned app-server?

I checked offline against the installed codex-cli 0.160.0 (`~/.codex/packages/standalone/releases/0.160.0-x86_64-unknown-linux-musl`). The scratch `CODEX_HOME` held no login. `HTTPS_PROXY`, `HTTP_PROXY` and `ALL_PROXY` were all set to `http://127.0.0.1:9`, so nothing left the host. I drove the host read's protocol (`initialize`, `initialized`, `account/rateLimits/read`, EOF) under `strace -f`, once plain and once through an equivalent no_new_privs trampoline.

| | plain | no_new_privs |
|---|---|---|
| `initialize` | result | result |
| `account/rateLimits/read` | -32600, "authentication required" | the same |
| app-server's `NoNewPrivs` | 0 | 1 |
| exit at EOF | 0 | 0 |
| files left in `CODEX_HOME` | 18 (sqlite state, logs, `installation_id`, `skills`, `.tmp`) | the same 18 |

The app-server's descendants were the same in both runs, with the same exit statuses:
- `/usr/bin/bwrap --unshare-user --unshare-net --ro-bind / / /bin/true`: a bubblewrap probe, exit 0;
- `lsb_release` and `getconf`: exit 0;
- `git -c safe.bareRepository=explicit ls-remote https://github.com/openai/plugin… HEAD` and `git-remote-https`: exit 128, at the discard proxy.

None of them needs privilege. `setuid` and `setresuid` never appear. The bwrap probe creates a user namespace, but it maps only uid A, so it is not a subordinate-range process.

The one thing no_new_privs can change is the bwrap probe, on a host where bwrap is **setuid**. The binary's own text says its pipeline runs "bubblewrap first (which may rely on setuid)".
- Here `/usr/bin/bwrap` and the bundled `codex-resources/bwrap` are both mode 0755, not setuid. Unprivileged user namespaces are allowed (`kernel.unprivileged_userns_clone=1`, `apparmor_restrict_unprivileged_userns=0`).
- Elsewhere, a setuid bwrap runs unprivileged under no_new_privs, and the probe may fail.
- That only tells the app-server its command sandbox is unavailable. The host read starts no turn and runs no command.
- I couldn't test a failing probe offline without a host change. If it ever made the app-server refuse to answer, the read would fail closed with `DIAG_READ_FAILED`, which is visible.

## Residuals (stated and accepted for the PoC)

1. **A delegated spawn.** A descendant could ask a service manager or server that isn't under no_new_privs to start a process with `CODEX_HOME` set:
   - this user's own (`systemd-run --user --setenv`, a running tmux server, D-Bus activation, `at`), which could then make a setuid transition;
   - an already-privileged service, which could start it as any user.

   It is not a descendant, so the holder contract doesn't cover it today, and the pinned app-server's descendants (above) do nothing of the kind. Closing it would need enforced cgroup membership for the read and every service it can reach, which is not proposed.
2. **A privileged helper that maps a namespace outside the subordinate ranges.** Root, or a service with `CAP_SETUID` in the initial namespace, can write any `uid_map` for a namespace the descendant made, and so give it any host uid. `newuidmap` can't do this; it maps only the caller's ranges. This is the privileged-service case of residual 1, applied to the descendant's own credentials. Accepted the same way.
3. **Homes from before the change.** `recover` at the first start after deployment may meet a holder from a read launched without no_new_privs. This happens once, and needs a crash plus a uid transition before the upgrade. Accepted.
4. **Root changes this user's subordinate allocation (r3).** These cases need root to edit `/etc/subuid`:
   - root removes a range from this user's lines, or deletes the file, while a namespace mapped from that range lives;
   - root reassigns the range to another user while such a namespace lives;
   - root adds a range during a scan, after the file was read, and a helper maps it at once.

   In each, a descendant can hold a uid that the scan doesn't count as this user's. These are root's edits to a file root owns, the same trust class as residual 2. The kernel-mapping check that would close them costs about one read in 25 on this host (above). Accepted for the PoC.

## Interaction with other work

- **btq-19fh0 (ask 4gt8).** Its three pins are in the zombie-leader branch (`:252–258`), which this design doesn't touch.
  - Its pins use another user's uid on all four fields. That uid must be outside the subordinate ranges, which the tests patch to a fixed set, so they stay valid in either merge order.
  - (a) makes their mixed-uid and saved-uid holders impossible for app-server descendants under the launch conditions, but the pins test the scan for any process, so they stay as defence in depth.
  - Both designs extend `status_text` in `tests/test_sandbox_hostread.py` with a `uids` argument. This design uses the same signature (four fields, defaulting to this user's), so whichever lands second merges trivially.
- **The flakes2 holder scan.** The rule order, settling, `PF_EXITING`, the zombie branch and the read path's settling are unchanged. Only `_ours`, which only rule 5 calls, widens.
  - no_new_privs doesn't change dumpability, so an app-server descendant's environ stays readable to the reader.
  - A descendant of ours that makes itself non-dumpable is still `_Unknown` (kept), as today.
- **u63ak.** The fakes run under the trampoline unchanged. The outlive fake's exec into the sleeper, and the sleepers' census and lifeline, need no privilege.

## Tests (offline, deterministic; failing tests first)

The new tests are in `tests/test_sandbox_hostread.py` unless noted, and use the existing fake app-server (`tests/fakes/fake_agent_cli.py`).
1. **The host read's app-server, and an escaped descendant, run under no_new_privs.**
   - The fake gains `HZ_FAKE_STATUS_FILE`: at start, it writes its `/proc/self/status` `NoNewPrivs` value there.
   - With the `escaped` fault, it also appends its sleeper's value, read from `/proc/<child>/status` right after the spawn.
   - A `Rig` read returns `WANTED`, and the file says `1`. With `escaped`, both values say `1`.
   - **Launch path (r3).** The runner may already have `NoNewPrivs: 1` (r2's review process did), so the values alone could pass without the barrier. The test also wraps `subprocess.Popen` in the loaded module, and asserts that the read's argv is exactly `[sys.executable, "-I", <codex_usage.py>, "--exec-no-new-privs", <codex>, "app-server"]`. The env, `start_new_session=True` and the pipes are unchanged. This assertion fails without the trampoline, whatever the runner's own `NoNewPrivs` is.
   - Both fail today. sleeper.py doesn't change.
2. **The default path is direct and inherits.** In the test file that drives `codex_usage.read`:
   - with the default, argv[0] is the codex binary;
   - the app-server's `NoNewPrivs` equals the test runner's own, whatever that is. (The runner may already have 1, so this doesn't assert 0.)
3. **A failing trampoline fails closed.**
   - Setup: hostread's `_usage_module` is wrapped so the loaded module's `barrier_argv` returns `[sys.executable, "-I", "-c", "import os; os._exit(126)"]`.
   - `read()` returns None.
   - The log is exactly one line, `DIAG_READ_FAILED` with `ExitStatus` or `ClosedOutput`. Both are accepted, because `_end`'s SIGKILL may land before the exit.
   - The fake's PID file was never written, since it never ran, and the scratch home is removed.
   - This pins what hostread does when the trampoline fails. The trampoline's own branches are test 3b's.
3b. **The trampoline's branches (r3; unit, in process, bounded).** `exec_no_new_privs` is called directly with its seams.
   - `exit` is a fake that records its code and raises, so the call never leaves the test. `execv` is a fake that records its argv, and `prctl` is a fake that records its calls and returns scripted values. Nothing really execs, and no real prctl runs.
   - **The checker rejects:** `status` returns a text with `CapEff` non-zero, and another with a uid of 0. The exit code is 126. Neither `prctl` nor `execv` is called.
   - **PR_SET fails:** `prctl(38, …)` returns -1. The code is 126, `PR_GET` isn't asked, and `execv` isn't called.
   - **PR_GET returns 0:** the set returns 0 but the get returns 0. The code is 126, and `execv` isn't called.
   - **`execv` raises `OSError`:** the code is 126.
   - **All pass:** the calls are `prctl(38, 1, 0, 0, 0)`, then `prctl(39, 0, 0, 0, 0)`, then `execv(argv[0], argv)`, in that order. The fake's return then reaches `finally`'s 126, which the real `execv` never returns to.
   - These fail today, because the function doesn't exist.
4. **`unprivileged`** (unit, on status texts):
   - true for this user's uids with zero capabilities;
   - false for uid 0 on any field, for a uid that isn't ours, for any non-zero bit in `CapPrm`, `CapEff` or `CapAmb` (with `CAP_SETUID` as the named case), and for a status missing any of those lines;
   - `CapInh` non-zero alone stays true.
5. **The trampoline is the group leader the client kills.** The existing `escaped` and `orphan` tests' assertions stay unchanged under the trampoline: the descendant is ended, the scratch is empty and `server_gone()` holds. They run as regression.
6. **The subordinate ranges.** `hostread._subordinate_uids` is patched to `{(200000, 65536)}`. With flakes2's `Scripted`/`Seam`, take a seam child with status `S`, `PF_EXITING` clear, and environ refused:
   - with uid 200500 on all four fields, it is `_Unknown` (kept, no kill);
   - with uid 300000, it is cleared at once (`rmtree`, no kill, `DIAG_RECOVERED`), which pins that rule 5's uid clause still clears another user's process;
   - with our uid in the real or effective field, it is `_Unknown`, as today.
   - The 200500 case fails today.
7. **Parsing `/etc/subuid`** (unit, on file texts, with the user's name and uid given; the readers are seams that return a text or raise):
   - lines by name and by numeric uid are taken, and other users' lines are ignored;
   - **several ranges:** two lines for the user, one by name and one by uid, give both ranges;
   - **endpoints:** with `name:200000:65536`, uids 200000 and 265535 are in the range, and 199999 and 265536 are not;
   - **invalid counts:** a count of 0, a negative count, or a non-numeric start or count gives "all", and so does a line with two or four fields;
   - a missing file gives no ranges;
   - an unreadable file gives "all";
   - an nsswitch `subid:` line, or an unreadable `/etc/nsswitch.conf`, gives "all";
   - **a failed name lookup:** `getpwuid` raising `KeyError` gives "all".

No test makes a real uid transition, which would need a setuid binary or root. The setns reproduction above used `unshare --map-auto` and is not a test: it depends on `/etc/subuid` and setuid `newuidmap`. The kernel guarantees are cited; tests 1, 3b, 4, 6 and 7 show the barrier, its branches, the check and the widened rule are in place.

## Changes, if approved

- `src/heterodyne/sandbox/resources/codex_usage.py`: `exec_no_new_privs` (with its test seams), `unprivileged`, `barrier_argv`, the `--exec-no-new-privs` mode, and `read(..., no_new_privs=False)`.
- `src/heterodyne/sandbox/hostread.py`:
  - the call at `:400` passes `no_new_privs=True`;
  - `_subordinate_uids()` (`/etc/subuid`, as above), read once per scan;
  - `_ours` counts those ranges;
  - `_ruled_out`'s and the module's docstrings state the premise: under the launch conditions, every holder descends from an app-server under no_new_privs, so a process with a uid that is neither this user's nor in this user's current `/etc/subuid` ranges can't be one. That holds while root leaves this user's allocation unchanged during the lifetime of its namespaces, and apart from the residuals above.
- `tests/fakes/fake_agent_cli.py`: the `HZ_FAKE_STATUS_FILE` report.
- The tests above. No ADR change: D9 and E4 already put the reader outside the sandbox. A line in plan 4b E4's notes records the barrier and the launch conditions.
