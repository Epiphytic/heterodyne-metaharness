# Plan8 cumulative MEDIUM round 9 dispositions

Actual verdict: no critical flaws; good enough as a source-only merge PoC. No deployment clearance or findings waiver. Historical review ordinals remain unchanged.

1. **DOCUMENTED SCOPE; no new change**

**Finding 1 is in scope and not a defect.** The reviewer said `wsctl harness install/use/remove/prune` still run during `scheduled-hold` (`src/heterodyne/wsd/daemon.py:761`) and called the round-8 fix "half done". I checked the documented contract in `docs/plan8-early-interfaces.md:17` and the r1 disposition: the hold closes new *scheduled, effect and paired-backup* ingress only. It explicitly does not pause workstreams or operator commands, and the installer still has to quiesce and drain. Operator `pickup` isn't held either. The new `harness_advance` guard (`daemon.py:398`) covers the timer-driven path, which is all it needed to cover.

2. **OPEN UNWAIVED BEFORE ACTIVATION**

- No production path calls `EffectStore.initialize`. That is the intended fail-closed behaviour, but it blocks activation.

3. **OPEN UNWAIVED CAPACITY RESIDUAL**

- Harness alerts that aren't in metadata use up alert capacity in the shared alerts directory (`local_alerts.py:219`). This is the documented best-effort limit.

4. **OPEN UNWAIVED**

- If a copy is cancelled during shutdown, the cycle is recorded as `failed`, so it can never be retried (`effect_owner.py:136`).

5. **OPEN UNWAIVED OBSERVABILITY**

- A cancelled queued future leaves a stale `running_origins` entry, so health shows a phantom "running" job (`daemon.py:235`). This only affects observability.

6. **OPEN UNWAIVED BEFORE SYSTEMD ACTIVATION**

1. **Shutdown timeout vs harness install** (`daemon.py:1046`). This comes from the merge: Plan8's 120 s drain now sits on top of main's harness install, which can legitimately take about 1200 s. A SIGTERM during an install makes wsd exit with code 1 and leave the install to be recovered at the next start. Fix it before any systemd-mode activation.

7. **OPEN UNWAIVED CI HYGIENE**

2. **Probe supervisor can orphan pytest** (`scripts/pytest_shutdown_probe.py:65`). It starts pytest in a new session and has no SIGTERM/SIGINT handler, so if the job is cancelled or times out, the pytest tree and its tmux servers keep running on the persistent self-hosted macOS runner. It doesn't make CI falsely green. It's a hygiene problem that can interfere with later jobs.

8. **OPEN UNWAIVED DIAGNOSTICS**

3. **CI timeout margin** (`.github/workflows/ci.yml:7`). The 70-minute job timeout leaves under about 9 minutes over the probe's own 3600 s pytest limit plus setup. On a slow runner GitHub kills the job before the probe writes its result, and the hang diagnostics you added are never collected.

9. **OPEN UNWAIVED INHERITED DEBT**

4. **Duplicated wait helper.** The DefaultSelector wait is copied into four places, and `admind/hook.py:543` still calls `select.select`, which fails above fd 1023. That's an existing call site the fix missed, not a regression.

10. **RESOLVED BY THIS SIGNED MERGE PUBLICATION**

5. **Uncommitted state.** The merge is still pending (MERGE_HEAD present) with unstaged and untracked files, which goes against CLAUDE.md's "commit at every rest state". It's process, not code, but the reviewed candidate exists only in the working tree.

EffectStore.initialize(path, installation, generation) is an explicit separately authorized deployment operation with retained installation/generation provenance. Never recreate missing production stores. Foreign alerts count toward shared capacity and must not be deleted or adopted.

Validation retained: 195 passed/2 BTQ_REPO-dependent skips; overlapping probe smoke 9 passed/natural exit 0; full configured typing 451 files/zero errors or warnings; configured Ruff/diff pass. Whole-merge quality 601 major and prior format debt remain unwaived. Hosted shutdown root cause and full Ubuntu outcomes are not established by these local checks.
