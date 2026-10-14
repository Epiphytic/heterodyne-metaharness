# hostread: a holder that changes uid after a setuid exec (btq-9yiku)

The source is the FINDING-PROD in the btq-19fh0 design review r2 (`/tmp/review-19fh0-r2.md`, rated High). The saved-uid analysis is in r1 (`/tmp/review-19fh0-r1.md`). That finding was noted as out of scope in `docs/superpowers/specs/2026-10-14-hostread-uid-first-design.md` (19fh0, pending Liam's ask 4gt8).

Line numbers are at origin/main e65ade7.

**Recommendation: (a) alone.** The host read's app-server is started under `PR_SET_NO_NEW_PRIVS`, through a small exec trampoline. Rule 5 is unchanged, and its premise is written down. (b) is rejected because of its measured cost, and so (c) is rejected too. The design is test-first, offline, and changes one launcher.

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

## The candidates

### (a) no_new_privs for the host read's app-server (recommended)

`no_new_privs` is inherited across fork, clone and exec, and it can't be unset. After it is set, `execve` grants nothing: setuid and setgid bits and file capabilities are ignored. The only uid changes left without privilege are among a task's own real, effective and saved uids, and here all three are A.
- An unprivileged user namespace can map only the caller's own uid. `newuidmap` is setuid or file-capability, so it is blocked too.
- In the reader's namespace, every task in the app-server's tree keeps real and effective uid A for its whole life.
- So for every process descended from the app-server, `_ours` is true, and rule 5's uid clause can only clear a process outside that tree.

That makes the premise hold for the holders hostread's contract names: the app-server and the descendants that escape its group. Rule 5's code doesn't change.

This repo already uses the same argument for the sandbox (`src/heterodyne/sandbox/channel.py:18,216`: a task under no_new_privs can't gain privilege; "under no no_new_privs, it can still gain them", `tests/test_sandbox_channel.py:636`).

**How it is set.** Python has no `os.prctl`, and `preexec_fn` is unsafe in a threaded process such as wsd. So the host read launches through a trampoline instead:

```python
NO_NEW_PRIVS = (                                # PR_SET_NO_NEW_PRIVS 38, PR_GET_NO_NEW_PRIVS 39
    "import ctypes, os, sys\n"
    "libc = ctypes.CDLL(None, use_errno=True)\n"
    "if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(39, 0, 0, 0, 0) != 1:\n"
    "    os._exit(126)\n"
    "os.execv(sys.argv[1], sys.argv[1:])\n")
```

- `read(codex, env, limit)` in `codex_usage.py` gains a keyword, `no_new_privs: bool = False`.
- When it is true, the argv is `[sys.executable, "-I", "-c", NO_NEW_PRIVS, codex, "app-server"]`, with the same `env`, pipes and `start_new_session=True`.
- hostread's call (`hostread.py:400`) passes `no_new_privs=True`. The in-sandbox read keeps the default, because the sandbox's workload already runs under no_new_privs (channel.py) and the change isn't needed there.
- The exec keeps the PID, the session, the group and the start time. The app-server is still the group leader that `_kill` names, and its start is still after the home.
- The trampoline has `CODEX_HOME` from birth, so a crash between its start and the exec leaves an ordinary holder that the scan finds.
- If the prctl fails, or the exec fails (exit 1 from the uncaught OSError), the client sees `ClosedOutput` before an answer and then `ExitStatus`. The read fails closed (`DIAG_READ_FAILED: ExitStatus`), and its home is removed as for any failed read.
- `setpriv --no-new-privs` (util-linux) would do the same. It is rejected because it adds a host dependency and an argv parser for something four lines of stdlib can do.
- The cost is one interpreter start per read, about 20–40 ms, against a 30 s limit.

### (b) rule 5 settles instead of clearing on the uid clause

This would remove the premise rather than enforce it: another user's refused process started after `since` settles. That is sound but expensive.
- A settling process keeps the scan incomplete, so cleanup waits `RECOVER_SECONDS` (10 s) and keeps the home.
- On the read path that means `DIAG_SCRATCH_KEPT`, and the read is discarded. On `recover` it means `DIAG_RECOVERY_KEPT`, and every home is kept.
- I measured this host on 2026-10-14. Of 673 live processes of other users whose environ is refused (uptime 27 days):

  | started within | processes |
  |---|---|
  | 10 s | 3 |
  | 60 s | 8 |
  | 10 min | 46 |
  | 1 h | 103 |
  | 1 day | 123 |

- **Read path.** `since` is the read's own home, made a second or two before cleanup. Any foreign process started in that window and still alive at cleanup discards the read. With about 3 such processes started in any 10 s, that is a large share of reads.
- **Recovery.** `since` is the crashed read's stamp, which can be hours or days before the restart. Every foreign process started since then and still alive (123 for a one-day gap here) settles. Recovery then waits 10 s at every start and never succeeds while any of them lives.
- No uid-based refinement saves (b). The attack leaves all four uids foreign, and r1 showed that per-task uid reads can't be made complete.

### (c) both

(c) has (b)'s cost. With (a) in place, (b) only adds protection against the residual cases below, which (b) doesn't fully close either. Rejected.

## Does no_new_privs break the pinned app-server?

I checked offline against the installed codex-cli 0.160.0 (`~/.codex/packages/standalone/releases/0.160.0-x86_64-unknown-linux-musl`). The scratch `CODEX_HOME` held no login, and `HTTPS_PROXY`, `HTTP_PROXY` and `ALL_PROXY` were all set to `http://127.0.0.1:9`, so nothing left the host. I drove the host read's protocol (`initialize`, `initialized`, `account/rateLimits/read`, EOF) under `strace -f`, once plain and once through the trampoline above.

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
- `git -c safe.bareRepository=explicit ls-remote https://github.com/openai/plugin… HEAD` and `git-remote-https`: exit 128, failing at the discard proxy.

None of them needs privilege. `uid`, `setuid` and `setresuid` never appear.

The one thing no_new_privs can change is the bwrap probe, on a host where bwrap is **setuid**. The binary's own text says its pipeline runs "bubblewrap first (which may rely on setuid)".
- Here `/usr/bin/bwrap` and the bundled `codex-resources/bwrap` are both mode 0755, not setuid. Unprivileged user namespaces are allowed (`kernel.unprivileged_userns_clone=1`, `apparmor_restrict_unprivileged_userns=0`).
- Elsewhere, a setuid bwrap runs unprivileged under no_new_privs, and the probe may fail.
- That only tells the app-server its command sandbox is unavailable. The host read starts no turn and runs no command, so the answer it needs doesn't depend on the probe.
- I couldn't test a failing probe offline without a host change. If it ever made the app-server refuse to answer, the read would fail closed with `DIAG_READ_FAILED`, which is visible.

## What (a) leaves open (stated, not fixed)

1. **A delegated spawn.** A descendant could ask a service manager or server that isn't under no_new_privs to start a process of ours with `CODEX_HOME` set: `systemd-run --user --setenv`, a running tmux server, D-Bus activation, `at`. That process could then make the setuid transition.
   - It is not a descendant, so the holder contract doesn't cover it today.
   - The pinned app-server's descendants (above) do nothing of the kind.
   - Closing it would take cgroup-scoped reads, which a separate bead could take up, and even that doesn't cover a service manager outside the cgroup.
2. **Homes from before the change.** `recover` at the first start after deployment may meet a holder from a read launched without no_new_privs. This happens once, and needs a crash plus the setuid transition before the upgrade. Accepted.

## Interaction with other work

- **btq-19fh0 (ask 4gt8).** Its three pins are in the zombie-leader branch (`:252–258`), which this design doesn't touch. They stay valid in either merge order.
  - (a) makes their mixed-uid and saved-uid holders impossible for app-server descendants, but the pins test the scan's behaviour for any process, so they stay as defence in depth.
  - Both designs extend `status_text` in `tests/test_sandbox_hostread.py` with a `uids` argument. This design uses the same signature (four fields, defaulting to this user's), so whichever lands second merges trivially.
- **The flakes2 holder scan.** The rule order, settling, `PF_EXITING`, the zombie branch and the read path's settling are all unchanged. no_new_privs doesn't change dumpability, so `/proc/<pid>/environ` of an app-server descendant stays readable to the reader. A descendant of ours that makes itself non-dumpable is still `_Unknown` (kept), as today.
- **u63ak.** The fakes run under the trampoline unchanged. The outlive fake's exec into the sleeper and the sleepers' census and lifeline need no privilege.

## Tests (offline, deterministic; failing tests first)

The new tests are in `tests/test_sandbox_hostread.py` unless noted, and use the existing fake app-server (`tests/fakes/fake_agent_cli.py`):
1. **The host read's app-server runs under no_new_privs.**
   - The fake gains `HZ_FAKE_STATUS_FILE`: at start, it writes its `/proc/self/status` `NoNewPrivs` value there.
   - A `Rig` read returns `WANTED`, and the file says `1`.
   - With the `escaped` fault, the fake also appends its sleeper's `NoNewPrivs` value, read from `/proc/<child>/status` right after the spawn, and that says `1` too. The sleeper is a descendant in a session of its own, so this shows the barrier reaches escaped descendants. sleeper.py doesn't change.
   - Both fail today.
2. **The in-sandbox read doesn't change.** In `tests/test_sandbox_runtime.py` or wherever `codex_usage.read` is tested directly: with the default, the app-server's `NoNewPrivs` is `0` and argv[0] is the codex binary.
3. **The trampoline fails closed.** hostread's `NO_NEW_PRIVS` source is monkeypatched to `os._exit(126)` before the exec.
   - `read()` is None, and the log is `[f"{DIAG_READ_FAILED}: ExitStatus"]`.
   - The app-server's PID file was never written (the fake never ran), and the scratch home is removed.
4. **The trampoline is the group leader the client kills.** With the existing `escaped` and `orphan` faults, the current assertions stay unchanged under the trampoline: the descendant is ended, the scratch is empty and `server_gone()` holds. They need no edit; they run as regression.
5. **A pin of rule 5's uid clause.** Using flakes2's `Scripted`/`Seam`, a seam child with status `S`, `PF_EXITING` clear, environ refused and the four `Uid:` fields foreign is cleared at once: `rmtree`, no kill, `DIAG_RECOVERED`.
   - The same child with our uid in the real or effective field raises `_Unknown` (kept).
   - This pins that (a) leaves rule 5 itself unchanged. It passes today.

No test makes a real uid transition. That would need a setuid binary or root, which the test rules forbid. The kernel guarantee is cited, not re-proven; test 1 proves the barrier is in place on the processes that matter.

## Changes, if approved

- `src/heterodyne/sandbox/resources/codex_usage.py`: `NO_NEW_PRIVS`, and `read(..., no_new_privs=False)`.
- `src/heterodyne/sandbox/hostread.py`:
  - the call at `:400` passes `no_new_privs=True`;
  - `_ruled_out`'s docstring states the premise: every holder descends from an app-server under no_new_privs, so another user's process can't be one, apart from the residuals above;
  - the module docstring says so too.
- `tests/fakes/fake_agent_cli.py`: the `HZ_FAKE_STATUS_FILE` report.
- The tests above. No ADR change: D9 and E4 already put the reader outside the sandbox. A line in plan 4b E4's notes records the barrier.
