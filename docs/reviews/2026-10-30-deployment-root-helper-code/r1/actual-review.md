I ran the built-in `code-review` skill at HIGH on `c77dd91…7629601` in this worktree (HEAD is `7629601`). I then checked each candidate against `src/heterodyne/actions/deployment_root_helper.py`, the tests and the qualification doc. I made no edits, commits, queue actions or service calls.

**Result: no critical correctness or security flaws in the dormant helper.** The core properties hold:
- **Closed request:** the only input is a 64-character packet SHA.
- **Root custody:** profile, permit and installer must be root-owned, mode 0400, single-link, opened by descriptor without following symlinks, and are re-read and compared before issuing.
- **Sudo check:** it reads the immediate parent's kernel UIDs as `(executor,0,0,0)` and matches its executable's device/inode to the pinned sudo. It checks PID and start time stability, and a forged `SUDO_UID` alone is refused.
- **Pinned launch:** pinned bash runs the exact installer bytes from a root-private temp file in `/run`. Argv is fixed and the environment is scrubbed.
- **Issue before effect:** the `O_EXCL` `.issued` record is written and fsynced, along with its directory, before the launch. The outcome is always UNKNOWN and retries are refused.

Remaining findings, most severe first:

| # | Verdict | Location | Finding |
|---|---|---|---|
| 1 | Plausible, medium | `deployment_root_helper.py:230` | The `_HELPER` file is opened and its hash checked against `profile.helper_sha256` / `packet.root_helper_sha256`, then never executed, passed to the child, or compared with the running process (`/proc/self/exe`, script, import closure). The test fixture's helper is `exit 1` and the run still returns 7. The doc already leaves the interpreter/import closure on HOLD, but it should say plainly that this pin is a file-custody check, not proof of which code is running as root. |
| 2 | Confirmed, medium (tests) | `tests/test_deployment_root_helper.py:~179` | The real sudo probe runs `sudo -n /usr/bin/python3 …` with no skip. Any host without a NOPASSWD rule for root python3 fails the suite, and on hosts that have one, the test runs code as host root. The namespace test likewise needs `unshare --map-auto` and subuid mappings, also with no skip. Both need a capability skip or an explicit marker. |
| 3 | Confirmed, low (doc accuracy) | `deployment_root_helper.py:145`; qualification doc line 30 | `os.pidfd_open(parent)` is opened and closed but never used; all identity reads go through `/proc/<pid>`. The `getppid()` re-check is what actually makes this safe, so either use the pidfd (for example `pidfd_pid`) or drop it and correct the doc's "retained pidfd" claim. |
| 4 | Confirmed, low (test claim) | `tests/test_deployment_root_helper.py:92` | One of the four counted "kernel denials" is `h._RootInstaller(store)`, which fails on a Python `getuid()` check, not a kernel permission denial. So 3 are kernel-enforced, and the doc's "actual kernel read/write/create denials" should count 3. |
| 5 | Confirmed, low | `deployment_root_helper.py:201`, `:255–261` | `checkout` is only checked for being absolute and normalized. A NUL or control character passes, then `subprocess.run` raises after `_issue`, burning the one-use permit as UNKNOWN with nothing launched. Only a root-written bad profile can trigger this, but validating the value and building argv before `_issue` is cheap. |
| 6 | Low, cleanup | `:101`, `:277–285`, `:200/210`, `:251–252` | Smaller cleanups: |
| | | | • Intermediate directory opens in `_binary` lack `O_CLOEXEC`, and `/` itself is never checked. |
| | | | • `count <= 0` is a dead branch. |
| | | | • The `schema` comparisons repeat checks the strict decode already enforces. |
| | | | • A retry of an issued packet does the full lock-and-copy work before failing, and reports `target-busy` instead of `already-issued` under contention. |

I dropped two candidates:
- **Before-state check:** `expected_before_sha256` is compared only against data inside the same packet, so it never sees live target state. That is by design: the doc makes the permit producer, which is on HOLD, responsible for checking the stopped_guard state.
- **Lock-file umask:** a umask strict enough to break the 0600 lock check isn't realistic under sudo.

Nothing here requires deploying the dormant helper. Items 1, 3 and 4 are about making the qualification doc's claims match the code. Item 2 is the one change I'd make before relying on the 121-test count on other hosts.
